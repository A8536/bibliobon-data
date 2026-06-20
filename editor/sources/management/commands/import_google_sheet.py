from pathlib import Path
from shutil import copy2
import csv

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from sources.google_sheets import (
    ARTICLE_PLACEMENT_HEADERS,
    ARTICLE_PLACEMENTS_SHEET,
    AUTHOR_HEADERS,
    AUTHORS_SHEET,
    JOURNAL_HEADERS,
    JOURNAL_ISSUE_HEADERS,
    JOURNAL_ISSUES_SHEET,
    JOURNALS_SHEET,
    LANGUAGE_HEADERS,
    LANGUAGES_SHEET,
    SECTION_HEADERS,
    SECTIONS_SHEET,
    TAG_HEADERS,
    TAGS_SHEET,
    WORK_AUTHOR_HEADERS,
    WORK_AUTHORS_SHEET,
    WORK_GROUP_HEADERS,
    WORK_GROUP_ITEM_HEADERS,
    WORK_GROUP_ITEMS_SHEET,
    WORK_GROUPS_SHEET,
    WORK_HEADERS,
    WORK_TAG_HEADERS,
    WORK_TAGS_SHEET,
    WORKS_SHEET,
    bool_cell,
    clean_cell,
    get_sheets_service,
    int_or_none,
    read_sheet_values,
    row_dict,
)
from sources.models import (
    Article,
    Author,
    Journal,
    JournalIssue,
    Language,
    Section,
    Source,
    Tag,
    Work,
    WorkAuthor,
    WorkGroup,
    WorkGroupItem,
    WorkTag,
)

HEADER_ALIASES = {
    "raw_publication_details": {"publication_details_raw"},
}


class Command(BaseCommand):
    help = "Imports editor data from Google Sheets."

    def add_arguments(self, parser):
        parser.add_argument("--spreadsheet-id", default=settings.GOOGLE_SHEETS_SPREADSHEET_ID)
        parser.add_argument("--credentials", default=settings.GOOGLE_SHEETS_CREDENTIALS)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        if not options["spreadsheet_id"]:
            raise CommandError("Нужен --spreadsheet-id или GOOGLE_SHEETS_SPREADSHEET_ID.")
        if not options["credentials"]:
            raise CommandError("Нужен --credentials или GOOGLE_SHEETS_CREDENTIALS.")
        try:
            service = get_sheets_service(options["credentials"])
            values = read_sheet_values(service, options["spreadsheet_id"])
            if not options["dry_run"]:
                backup = backup_sqlite_database("before-google-import")
                if backup:
                    self.stdout.write(self.style.WARNING(f"Backup создан: {backup}"))
            stats = import_values(values, dry_run=options["dry_run"])
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run: изменения не сохранены."))
        self.stdout.write(self.style.SUCCESS(f"Импорт завершен: {stats}"))


@transaction.atomic
def import_values(values_by_sheet, dry_run=False):
    stats = {"updated": 0, "created": 0, "relations_replaced": 0, "skipped": 0}
    duplicate_issue_keys = duplicate_journal_issue_keys(values_by_sheet.get(JOURNAL_ISSUES_SHEET, []))
    if duplicate_issue_keys:
        report_path = write_duplicate_issue_report(duplicate_issue_keys)
        stats["duplicate_issue_keys"] = len(duplicate_issue_keys)
        stats["duplicate_issue_report"] = str(report_path)

    import_languages(values_by_sheet.get(LANGUAGES_SHEET, []), stats)
    import_sections(values_by_sheet.get(SECTIONS_SHEET, []), stats)
    import_tags(values_by_sheet.get(TAGS_SHEET, []), stats)
    import_authors(values_by_sheet.get(AUTHORS_SHEET, []), stats)
    import_journals(values_by_sheet.get(JOURNALS_SHEET, []), stats)
    import_issues(values_by_sheet.get(JOURNAL_ISSUES_SHEET, []), stats)
    pending_container_links = import_works(values_by_sheet.get(WORKS_SHEET, []), stats)
    import_article_placements(values_by_sheet.get(ARTICLE_PLACEMENTS_SHEET, []), stats)
    apply_work_container_indexes(pending_container_links, stats)
    import_work_groups(values_by_sheet.get(WORK_GROUPS_SHEET, []), stats)
    import_work_group_items(values_by_sheet.get(WORK_GROUP_ITEMS_SHEET, []), stats)
    import_work_authors(values_by_sheet.get(WORK_AUTHORS_SHEET, []), stats)
    import_work_tags(values_by_sheet.get(WORK_TAGS_SHEET, []), stats)

    if dry_run:
        transaction.set_rollback(True)
    return stats


def duplicate_journal_issue_keys(values):
    by_key = {}
    duplicates = {}
    for sheet_row, row in enumerate(data_rows(values, JOURNAL_ISSUE_HEADERS), start=2):
        key = (
            clean_cell(row["journal_id"]),
            clean_cell(row["year"]),
            clean_cell(row["issue_number"]),
            clean_cell(row["volume"]),
        )
        if not key[0]:
            continue
        payload = {
            "sheet_row": sheet_row,
            "journal_issue_id": clean_cell(row["journal_issue_id"]),
            "journal_id": key[0],
            "journal_title": clean_cell(row["journal_title"]),
            "year": key[1],
            "issue_number": key[2],
            "volume": key[3],
            "gross_number": clean_cell(row["gross_number"]),
            "raw_publication_details": clean_cell(row["raw_publication_details"]),
        }
        if key in by_key:
            duplicates.setdefault(key, [by_key[key]]).append(payload)
        else:
            by_key[key] = payload
    return duplicates


def write_duplicate_issue_report(duplicates):
    reports_dir = settings.PROJECT_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / "google_sheet_duplicate_journal_issues.tsv"
    with report_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "key",
                "sheet_row",
                "journal_issue_id",
                "journal_id",
                "journal_title",
                "year",
                "issue_number",
                "volume",
                "gross_number",
                "raw_publication_details",
            ],
            delimiter="\t",
        )
        writer.writeheader()
        for key, rows in duplicates.items():
            key_text = " | ".join(key)
            for row in rows:
                writer.writerow({"key": key_text, **row})
    return report_path


def data_rows(values, headers, optional_headers=None):
    optional_headers = set(optional_headers or [])
    if not values:
        return
    actual = [clean_cell(item) for item in values[0]]
    if not any(actual):
        return
    missing = [
        header
        for header in headers
        if header not in actual
        and header not in optional_headers
        and not (HEADER_ALIASES.get(header) or set()) & set(actual)
    ]
    if missing:
        raise ValueError(f"Нет колонок: {', '.join(missing)}")
    for raw in values[1:]:
        if any(clean_cell(cell) for cell in raw):
            row = row_dict(actual, raw)
            for canonical, aliases in HEADER_ALIASES.items():
                if canonical in row:
                    continue
                for alias in aliases:
                    if alias in row:
                        row[canonical] = row[alias]
                        break
            yield row


def import_languages(values, stats):
    for row in data_rows(values, LANGUAGE_HEADERS):
        obj, created = Language.objects.update_or_create(
            language_id=clean_cell(row["language_id"]) or f"language-{row['code']}",
            defaults={
                "code": clean_cell(row["code"]),
                "title": clean_cell(row["title"]),
                "description": clean_cell(row["description"]),
                "sort_order": int_or_none(row["sort_order"]) or 0,
            },
        )
        bump(stats, created)


def import_sections(values, stats):
    pending = []
    for row in data_rows(values, SECTION_HEADERS):
        section_id = clean_cell(row["section_id"]) or next_id(Section, "section_id", "section")
        obj, created = Section.objects.update_or_create(
            section_id=section_id,
            defaults={
                "source_code": clean_cell(row["source_code"]) or section_id,
                "title": clean_cell(row["title"]),
                "note": clean_cell(row["note"]),
                "description": clean_cell(row["description"]),
                "sort_order": int_or_none(row["sort_order"]) or 0,
                "parent": None,
            },
        )
        pending.append((obj, clean_cell(row["parent_section_id"])))
        bump(stats, created)
    for obj, parent_id in pending:
        if parent_id and parent_id != obj.section_id:
            Section.objects.filter(section_id=obj.section_id).update(parent_id=parent_id)


def import_tags(values, stats):
    pending = []
    for row in data_rows(values, TAG_HEADERS):
        tag_id = clean_cell(row["tag_id"]) or next_id(Tag, "tag_id", "tag")
        obj, created = Tag.objects.update_or_create(
            tag_id=tag_id,
            defaults={
                "title": clean_cell(row["title"]),
                "tag_type": clean_cell(row["tag_type"]) or Tag.TagType.GENERAL,
                "description": clean_cell(row["description"]),
                "sort_order": int_or_none(row["sort_order"]) or 0,
                "parent": None,
            },
        )
        pending.append((obj, clean_cell(row["parent_tag_id"])))
        bump(stats, created)
    for obj, parent_id in pending:
        if parent_id and parent_id != obj.tag_id:
            Tag.objects.filter(tag_id=obj.tag_id).update(parent_id=parent_id)


def import_authors(values, stats):
    for row in data_rows(values, AUTHOR_HEADERS):
        author_id = clean_cell(row["author_id"]) or next_id(Author, "author_id", "author")
        obj, created = Author.objects.update_or_create(
            author_id=author_id,
            defaults={key: clean_cell(row[key]) for key in AUTHOR_HEADERS if key != "author_id"},
        )
        bump(stats, created)


def import_journals(values, stats):
    for row in data_rows(values, JOURNAL_HEADERS):
        journal_id = clean_cell(row["journal_id"]) or next_id(Journal, "journal_id", "journal")
        obj, created = Journal.objects.update_or_create(
            journal_id=journal_id,
            defaults={
                "title": clean_cell(row["title"]),
                "parallel_title": clean_cell(row["parallel_title"]),
                "title_remainder": clean_cell(row["title_remainder"]),
                "responsibility_statement": clean_cell(row["responsibility_statement"]),
                "place": clean_cell(row["place"]),
                "publisher": clean_cell(row["publisher"]),
                "issn": clean_cell(row["issn"]),
                "periodicity": clean_cell(row["periodicity"]),
                "numbering_start": clean_cell(row["numbering_start"]),
                "numbering_end": clean_cell(row["numbering_end"]),
                "start_year": int_or_none(row["start_year"]),
                "end_year": int_or_none(row["end_year"]),
                "title_history_note": clean_cell(row["title_history_note"]),
                "description": clean_cell(row["description"]),
            },
        )
        bump(stats, created)


def import_issues(values, stats):
    for row in data_rows(values, JOURNAL_ISSUE_HEADERS):
        journal = Journal.objects.filter(journal_id=clean_cell(row["journal_id"])).first()
        if not journal:
            stats["skipped"] += 1
            continue
        issue_id = clean_cell(row["journal_issue_id"]) or next_id(JournalIssue, "journal_issue_id", "journal-issue")
        obj, created = JournalIssue.objects.update_or_create(
            journal_issue_id=issue_id,
            defaults={
                "journal": journal,
                "title": clean_cell(row["title"]),
                "year": int_or_none(row["year"]),
                "publication_date": clean_cell(row["publication_date"]),
                "issue_number": clean_cell(row["issue_number"]),
                "volume": clean_cell(row["volume"]),
                "part_number": clean_cell(row["part_number"]),
                "gross_number": clean_cell(row["gross_number"]),
                "date_text": clean_cell(row["date_text"]),
                "chronology": clean_cell(row["chronology"]),
                "enumeration": clean_cell(row["enumeration"]),
                "publication_place": clean_cell(row["publication_place"]),
                "publisher": clean_cell(row["publisher"]),
                "publication_details": clean_cell(row["raw_publication_details"]),
                "issn": clean_cell(row["issn"]),
                "isbn": clean_cell(row["isbn"]),
                "notes": clean_cell(row["notes"]),
            },
        )
        bump(stats, created)


def import_works(values, stats):
    pending_container_links = []
    for row in data_rows(values, WORK_HEADERS, optional_headers={"is_container", "circulation", "container_index", "linked_authors", "page_start", "page_end", "data_source", "first_seen_at", "updated_at"}):
        language = Language.objects.filter(language_id=clean_cell(row["language_id"])).first() or Language.objects.filter(code=clean_cell(row["language_code"]) or "ru").first()
        section = Section.objects.filter(section_id=clean_cell(row["section_id"])).first()
        if not language:
            stats["skipped"] += 1
            continue
        work_id = clean_cell(row["work_id"]) or next_id(Work, "work_id", "work")
        work_type = clean_cell(row["work_type"]) or Work.WorkType.UNKNOWN
        if bool_cell(row.get("is_container", "")):
            work_type = Work.WorkType.CONTAINER
        obj, created = Work.objects.update_or_create(
            work_id=work_id,
            defaults={
                "source_sequence": int_or_none(row["source_sequence"]),
                "source_number": int_or_none(row["source_number"]) or 900000000,
                "work_type": work_type,
                "is_container": work_type == Work.WorkType.CONTAINER,
                "source_section": section,
                "language": language,
                "raw_author_string": clean_cell(row["raw_author_string"]),
                "title": clean_cell(row["title"]),
                "parallel_title": clean_cell(row["parallel_title"]),
                "subtitle": clean_cell(row["subtitle"]),
                "title_remainder": clean_cell(row["title_remainder"]),
                "volume_number": clean_cell(row["volume_number"]),
                "part_number": clean_cell(row["part_number"]),
                "part_title": clean_cell(row["part_title"]),
                "responsibility_statement": clean_cell(row["responsibility_statement"]),
                "edition_statement": clean_cell(row["edition_statement"]),
                "additional_edition_statement": clean_cell(row["additional_edition_statement"]),
                "publication_place": clean_cell(row["publication_place"]),
                "publisher": clean_cell(row["publisher"]),
                "publication_date": clean_cell(row["publication_date"]),
                "inferred_year": int_or_none(row["year"]),
                "extent": clean_cell(row["extent"]),
                "illustrations": clean_cell(row["illustrations"]),
                "dimensions": clean_cell(row["dimensions"]),
                "accompanying_material": clean_cell(row["accompanying_material"]),
                "circulation": clean_cell(row.get("circulation", "")),
                "physical_description": clean_cell(row["physical_description"]),
                "article_pages": clean_cell(row["article_pages"]),
                "page_start": int_or_none(row.get("page_start", "")),
                "page_end": int_or_none(row.get("page_end", "")),
                "series_statement": clean_cell(row["series_statement"]),
                "notes": clean_cell(row["notes"]),
                "publication_details": clean_cell(row["raw_publication_details"]),
                "description_status": clean_cell(row["description_status"]) or Work.DescriptionStatus.RAW_ONLY,
                "public_review": clean_cell(row["public_review"]),
                "isbn": clean_cell(row["isbn"]),
                "issn": clean_cell(row["issn"]),
                "doi": clean_cell(row["doi"]),
                "url": clean_cell(row["url"]),
            },
        )
        update_target_source_from_work_sheet(obj, row)
        bump(stats, created)
        pending_container_links.append((work_id, clean_cell(row.get("container_index", ""))))
    return pending_container_links


def update_target_source_from_work_sheet(work, row):
    source = Source.objects.filter(source_id=work.work_id).first()
    if not source:
        return
    changed_fields = []
    raw_publication_details = clean_cell(row["raw_publication_details"])
    if raw_publication_details and source.raw_publication_details != raw_publication_details:
        source.raw_publication_details = raw_publication_details
        changed_fields.append("raw_publication_details")
    data_source = clean_cell(row.get("data_source", ""))
    if data_source and source.data_source != data_source:
        source.data_source = data_source
        changed_fields.append("data_source")
    first_seen_at = datetime_or_none(row.get("first_seen_at", ""))
    if first_seen_at and source.first_seen_at != first_seen_at:
        source.first_seen_at = first_seen_at
        changed_fields.append("first_seen_at")
    updated_at = datetime_or_none(row.get("updated_at", ""))
    if updated_at and source.updated_at != updated_at:
        source.updated_at = updated_at
        changed_fields.append("updated_at")
    if changed_fields:
        source.save(update_fields=changed_fields)


def datetime_or_none(value):
    value = clean_cell(value)
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def apply_work_container_indexes(pending, stats):
    for work_id, container_index in pending:
        if not container_index:
            continue
        work = Work.objects.filter(work_id=work_id).first()
        if not work:
            stats["skipped"] += 1
            continue
        target_kind, _, target_id = container_index.partition(":")
        if target_kind == "container":
            container = Work.objects.filter(work_id=target_id).first()
            if not container or container.work_id == work.work_id:
                stats["skipped"] += 1
                continue
            article = ensure_article_for_container_index(work)
            article.container_work = container
            article.journal_issue = None
            article.collection = None
            article.pages = work.article_pages or article.pages
            article.pages_raw = work.article_pages or article.pages_raw
            article.page_start = work.page_start
            article.page_end = work.page_end
            article.save()
            Work.objects.filter(work_id=work.work_id).update(work_type=Work.WorkType.ARTICLE, is_container=False, host_title="")
        elif target_kind == "issue":
            issue = JournalIssue.objects.filter(journal_issue_id=target_id).first()
            if not issue:
                stats["skipped"] += 1
                continue
            article = ensure_article_for_container_index(work)
            article.journal_issue = issue
            article.container_work = None
            article.collection = None
            article.pages = work.article_pages or article.pages
            article.pages_raw = work.article_pages or article.pages_raw
            article.page_start = work.page_start
            article.page_end = work.page_end
            article.save()
            Work.objects.filter(work_id=work.work_id).update(work_type=Work.WorkType.ARTICLE, is_container=False, host_title="")
        else:
            stats["skipped"] += 1


def ensure_article_for_container_index(work):
    article = Article.objects.filter(work=work).first()
    if article:
        return article
    return Article(article_id=f"article-for-{work.work_id}", work=work)


LEGACY_ARTICLE_PLACEMENT_HEADERS = [
    "article_id",
    "work_id",
    "work_title",
    "journal_issue_id",
    "journal_title",
    "pages_raw",
    "location_note",
    "placement_note",
]


def import_article_placements(values, stats):
    headers = article_placement_import_headers(values)
    optional_headers = {
        "container_id",
        "container_type",
        "container_title",
        "journal_issue_id",
        "journal_title",
        "container_work_id",
        "container_work_title",
        "page_start",
        "page_end",
    }
    for row in data_rows(values, headers, optional_headers=optional_headers):
        work = Work.objects.filter(work_id=clean_cell(row["work_id"])).first()
        if not work:
            stats["skipped"] += 1
            continue
        issue, container = resolve_article_container(row)
        page_start = int_or_none(row.get("page_start", "")) or work.page_start
        page_end = int_or_none(row.get("page_end", "")) or work.page_end
        article_id = clean_cell(row["article_id"]) or f"article-for-{work.work_id}"
        obj, created = Article.objects.update_or_create(
            article_id=article_id,
            defaults={
                "work": work,
                "journal_issue": issue,
                "container_work": container,
                "collection": None,
                "pages": clean_cell(row["pages_raw"]),
                "pages_raw": clean_cell(row["pages_raw"]),
                "page_start": page_start,
                "page_end": page_end,
                "location_note": clean_cell(row["location_note"]),
                "placement_note": clean_cell(row["placement_note"]),
            },
        )
        bump(stats, created)


def article_placement_import_headers(values):
    if not values:
        return ARTICLE_PLACEMENT_HEADERS
    actual = {clean_cell(item) for item in values[0]}
    if "container_id" in actual:
        return ARTICLE_PLACEMENT_HEADERS
    if "journal_issue_id" in actual:
        return LEGACY_ARTICLE_PLACEMENT_HEADERS
    return ARTICLE_PLACEMENT_HEADERS


def resolve_article_container(row):
    container_id = clean_cell(row.get("container_id", ""))
    container_type = clean_cell(row.get("container_type", ""))
    legacy_issue_id = clean_cell(row.get("journal_issue_id", ""))
    legacy_container_work_id = clean_cell(row.get("container_work_id", ""))

    if container_id:
        if container_type == "journal_issue":
            return JournalIssue.objects.filter(journal_issue_id=container_id).first(), None
        if container_type == "container_work":
            return None, Work.objects.filter(work_id=container_id).first()
        issue = JournalIssue.objects.filter(journal_issue_id=container_id).first()
        if issue:
            return issue, None
        return None, Work.objects.filter(work_id=container_id).first()

    issue = JournalIssue.objects.filter(journal_issue_id=legacy_issue_id).first()
    container = Work.objects.filter(work_id=legacy_container_work_id).first()
    return issue, container


def import_work_groups(values, stats):
    for row in data_rows(values, WORK_GROUP_HEADERS):
        group_id = clean_cell(row["group_id"]) or next_id(WorkGroup, "group_id", "group")
        obj, created = WorkGroup.objects.update_or_create(
            group_id=group_id,
            defaults={"title": clean_cell(row["title"]), "group_type": clean_cell(row["group_type"]) or WorkGroup.GroupType.RELATED_GROUP, "note": clean_cell(row["note"])},
        )
        bump(stats, created)


def import_work_group_items(values, stats):
    if not values:
        return
    WorkGroupItem.objects.all().delete()
    stats["relations_replaced"] += 1
    for row in data_rows(values, WORK_GROUP_ITEM_HEADERS):
        if WorkGroup.objects.filter(group_id=row["group_id"]).exists() and Work.objects.filter(work_id=row["work_id"]).exists():
            WorkGroupItem.objects.create(group_id=row["group_id"], work_id=row["work_id"], sort_order=int_or_none(row["sort_order"]) or 0)


def import_work_authors(values, stats):
    if not values:
        return
    WorkAuthor.objects.all().delete()
    stats["relations_replaced"] += 1
    for row in data_rows(values, WORK_AUTHOR_HEADERS):
        if Work.objects.filter(work_id=row["work_id"]).exists() and Author.objects.filter(author_id=row["author_id"]).exists():
            WorkAuthor.objects.create(
                work_id=row["work_id"],
                author_id=row["author_id"],
                sort_order=int_or_none(row["sort_order"]) or 0,
                role=clean_cell(row["role"]),
                source_text=clean_cell(row["source_text"]),
                name_as_printed=clean_cell(row["name_as_printed"]),
                include_in_responsibility=bool_cell(row["include_in_responsibility"]) if row["include_in_responsibility"] else True,
                is_primary_heading=bool_cell(row["is_primary_heading"]),
            )


def import_work_tags(values, stats):
    if not values:
        return
    WorkTag.objects.all().delete()
    stats["relations_replaced"] += 1
    for row in data_rows(values, WORK_TAG_HEADERS):
        if Work.objects.filter(work_id=row["work_id"]).exists() and Tag.objects.filter(tag_id=row["tag_id"]).exists():
            WorkTag.objects.create(work_id=row["work_id"], tag_id=row["tag_id"], sort_order=int_or_none(row["sort_order"]) or 0, source_text=clean_cell(row["source_text"]))


def bump(stats, created):
    stats["created" if created else "updated"] += 1


def next_id(model, field, prefix):
    number = model.objects.count() + 1
    while True:
        candidate = f"{prefix}-{number:06d}"
        if not model.objects.filter(**{field: candidate}).exists():
            return candidate
        number += 1


def backup_sqlite_database(label):
    db_name = settings.DATABASES["default"].get("NAME", "")
    db_path = Path(db_name)
    if not db_path.exists() or db_path.suffix != ".sqlite":
        return None
    timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{db_path.stem}.{label}.{timestamp}{db_path.suffix}"
    copy2(db_path, backup_path)
    return backup_path
