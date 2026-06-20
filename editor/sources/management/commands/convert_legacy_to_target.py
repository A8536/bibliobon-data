import json
from pathlib import Path
from shutil import copy2

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from sources.models import (
    Article,
    ArticlePlacement,
    Collection,
    Issue,
    Journal,
    JournalIssue,
    Periodical,
    Source,
    SourceAuthor,
    SourceGroup,
    SourceGroupItem,
    SourceTag,
    Work,
    WorkAuthor,
    WorkGroup,
    WorkGroupItem,
    WorkTag,
)

TARGET_PROVENANCE = {}


class Command(BaseCommand):
    help = "Converts compatibility editor data into the target Source/Issue model."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write target rows. Default is report-only.")
        parser.add_argument("--reset", action="store_true", help="Clear existing target rows before writing.")

    def handle(self, *args, **options):
        report = build_report()
        write_report(report)
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))

        if not options["apply"]:
            self.stdout.write(self.style.WARNING("Report-only: target tables were not changed. Add --apply to convert."))
            return

        backup = backup_sqlite_database("before-target-conversion")
        if backup:
            self.stdout.write(self.style.WARNING(f"Backup создан: {backup}"))

        with transaction.atomic():
            global TARGET_PROVENANCE
            TARGET_PROVENANCE = collect_target_provenance()
            if options["reset"]:
                reset_target_tables()
            stats = apply_conversion()

        post_report = build_report()
        post_report["last_apply"] = stats
        write_report(post_report)
        self.stdout.write(self.style.SUCCESS(json.dumps(stats, ensure_ascii=False, indent=2)))


def build_report():
    articles = Article.objects.select_related("journal_issue", "container_work", "collection")
    container_work_ids = list(
        Work.objects.annotate(article_count=Count("contained_articles", distinct=True))
        .filter(Q(article_count__gt=0) | Q(is_container=True) | Q(work_type=Work.WorkType.CONTAINER))
        .values_list("work_id", flat=True)
    )
    legacy_collection_count = Collection.objects.annotate(article_count=Count("articles", distinct=True)).filter(article_count__gt=0).count()
    return {
        "legacy": {
            "works": Work.objects.count(),
            "work_authors": WorkAuthor.objects.count(),
            "work_tags": WorkTag.objects.count(),
            "journals": Journal.objects.count(),
            "journal_issues": JournalIssue.objects.count(),
            "articles": articles.count(),
            "work_groups": WorkGroup.objects.count(),
            "work_group_items": WorkGroupItem.objects.count(),
            "container_works": len(container_work_ids),
            "legacy_collections_with_articles": legacy_collection_count,
        },
        "target_current": {
            "sources": Source.objects.count(),
            "source_authors": SourceAuthor.objects.count(),
            "source_tags": SourceTag.objects.count(),
            "periodicals": Periodical.objects.count(),
            "issues": Issue.objects.count(),
            "article_placements": ArticlePlacement.objects.count(),
            "source_groups": SourceGroup.objects.count(),
            "source_group_items": SourceGroupItem.objects.count(),
        },
        "conversion_plan": {
            "sources_from_works": Work.objects.count(),
            "periodicals_from_journals": Journal.objects.count(),
            "issues_from_journal_issues": JournalIssue.objects.count(),
            "issues_from_container_works": len(container_work_ids),
            "article_placements_from_articles": articles.count(),
            "source_groups_from_work_groups": WorkGroup.objects.count(),
        },
        "diagnostics": {
            "articles_without_container": articles.filter(
                journal_issue__isnull=True,
                container_work__isnull=True,
                collection__isnull=True,
            ).count(),
            "articles_with_both_main_containers": articles.filter(
                journal_issue__isnull=False,
                container_work__isnull=False,
            ).count(),
            "legacy_collection_articles": articles.filter(collection__isnull=False).count(),
            "standalone_records_that_look_like_articles": Work.objects.filter(
                article__isnull=True,
                host_title__gt="",
            ).count(),
        },
    }


def apply_conversion():
    stats = {
        "sources": 0,
        "source_authors": 0,
        "source_tags": 0,
        "periodicals": 0,
        "issues": 0,
        "article_placements": 0,
        "source_groups": 0,
        "source_group_items": 0,
        "skipped_article_placements": 0,
    }

    for work in Work.objects.select_related("language", "source_section").iterator():
        Source.objects.update_or_create(source_id=work.work_id, defaults=source_defaults(work))
        stats["sources"] += 1

    for relation in WorkAuthor.objects.select_related("work", "author").iterator():
        SourceAuthor.objects.update_or_create(
            source_id=relation.work_id,
            author=relation.author,
            role=relation.role,
            defaults={
                "sort_order": relation.sort_order,
                "source_text": relation.source_text,
                "name_as_printed": relation.name_as_printed,
                "include_in_responsibility": relation.include_in_responsibility,
                "is_primary_heading": relation.is_primary_heading,
                **timestamp_defaults("source_author", (relation.work_id, relation.author_id, relation.role)),
            },
        )
        stats["source_authors"] += 1

    for relation in WorkTag.objects.select_related("work", "tag").iterator():
        SourceTag.objects.update_or_create(
            source_id=relation.work_id,
            tag=relation.tag,
            defaults={
                "sort_order": relation.sort_order,
                "source_text": relation.source_text,
                **timestamp_defaults("source_tag", (relation.work_id, relation.tag_id)),
            },
        )
        stats["source_tags"] += 1

    for journal in Journal.objects.iterator():
        Periodical.objects.update_or_create(periodical_id=journal.journal_id, defaults=periodical_defaults(journal))
        stats["periodicals"] += 1

    for issue in JournalIssue.objects.select_related("journal").iterator():
        Issue.objects.update_or_create(issue_id=issue.journal_issue_id, defaults=journal_issue_defaults(issue))
        stats["issues"] += 1

    container_works = (
        Work.objects.annotate(article_count=Count("contained_articles", distinct=True))
        .filter(Q(article_count__gt=0) | Q(is_container=True) | Q(work_type=Work.WorkType.CONTAINER))
        .select_related("language", "source_section")
    )
    for work in container_works.iterator():
        Issue.objects.update_or_create(issue_id=container_issue_id(work), defaults=container_work_issue_defaults(work))
        stats["issues"] += 1

    for collection in Collection.objects.annotate(article_count=Count("articles", distinct=True)).filter(article_count__gt=0).iterator():
        Issue.objects.update_or_create(issue_id=collection_issue_id(collection), defaults=legacy_collection_issue_defaults(collection))
        stats["issues"] += 1

    for article in Article.objects.select_related("work", "journal_issue", "container_work", "collection").iterator():
        issue_id = placement_issue_id(article)
        if not issue_id:
            stats["skipped_article_placements"] += 1
            continue
        ArticlePlacement.objects.update_or_create(
            placement_id=article.article_id,
            defaults={
                "source_django_id": article.source_django_id,
                "legacy_article": article,
                "source_id": article.work_id,
                "issue_id": issue_id,
                "pages_raw": article.pages_raw or article.pages or article.work.article_pages,
                "page_start": article.work.page_start or article.page_start,
                "page_end": article.work.page_end or article.page_end,
                "location_note": article.location_note,
                "placement_note": article.placement_note,
                **timestamp_defaults("article_placement", article.article_id),
            },
        )
        stats["article_placements"] += 1

    for group in WorkGroup.objects.iterator():
        SourceGroup.objects.update_or_create(group_id=group.group_id, defaults=source_group_defaults(group))
        stats["source_groups"] += 1

    for item in WorkGroupItem.objects.select_related("group", "work").iterator():
        SourceGroupItem.objects.update_or_create(
            group_id=item.group_id,
            source_id=item.work_id,
            defaults={
                "sort_order": item.sort_order,
                **timestamp_defaults("source_group_item", (item.group_id, item.work_id)),
            },
        )
        stats["source_group_items"] += 1

    return stats


def source_defaults(work):
    now = timezone.now()
    provenance = TARGET_PROVENANCE.get("source", {}).get(work.work_id, {})
    return {
        "source_django_id": work.source_django_id,
        "legacy_work": work,
        "source_sequence": work.source_sequence,
        "source_number": work.source_number,
        "source_page_marker": work.source_page_marker,
        "source_type": source_type_for_work(work),
        "section": work.source_section,
        "language": work.language,
        "raw_author_string": work.raw_author_string,
        "title": work.title,
        "parallel_title": work.parallel_title,
        "subtitle": work.subtitle,
        "title_remainder": work.title_remainder,
        "volume_number": work.volume_number,
        "part_number": work.part_number,
        "part_title": work.part_title,
        "responsibility_statement": work.responsibility_statement or work.responsibility_note,
        "edition_statement": work.edition_statement,
        "additional_edition_statement": work.additional_edition_statement,
        "publication_place": work.publication_place,
        "publisher": work.publisher,
        "publication_date": work.publication_date,
        "inferred_year": work.inferred_year,
        "manufacture_place": work.manufacture_place,
        "manufacturer": work.manufacturer,
        "manufacture_date": work.manufacture_date,
        "copyright_date": work.copyright_date,
        "extent": work.extent or work.physical_description,
        "illustrations": work.illustrations,
        "dimensions": work.dimensions,
        "accompanying_material": work.accompanying_material,
        "circulation": work.circulation,
        "series_statement": work.series_statement,
        "notes": work.notes,
        "bibliography_note": work.bibliography_note,
        "index_note": work.index_note,
        "contents_note": work.contents_note,
        "isbn": work.isbn,
        "issn": work.issn,
        "doi": work.doi,
        "url": work.url,
        "access_date": work.access_date,
        "content_type": work.content_type,
        "media_type": work.media_type,
        "carrier_type": work.carrier_type,
        "raw_publication_details": work.publication_details,
        "raw_host_title": work.host_title,
        "public_review": work.public_review,
        "data_source": provenance.get("data_source") or "editor",
        "first_seen_at": provenance.get("first_seen_at") or now,
        "updated_at": provenance.get("updated_at") or now,
        "description_status": work.description_status,
    }


def collect_target_provenance():
    return {
        "source": {
            obj.source_id: {
                "data_source": obj.data_source,
                "first_seen_at": obj.first_seen_at,
                "updated_at": obj.updated_at,
            }
            for obj in Source.objects.only("source_id", "data_source", "first_seen_at", "updated_at").iterator()
        },
        "source_author": {
            (obj.source_id, obj.author_id, obj.role): relation_timestamps(obj)
            for obj in SourceAuthor.objects.only("source_id", "author_id", "role", "created_at", "updated_at").iterator()
        },
        "source_tag": {
            (obj.source_id, obj.tag_id): relation_timestamps(obj)
            for obj in SourceTag.objects.only("source_id", "tag_id", "created_at", "updated_at").iterator()
        },
        "article_placement": {
            obj.placement_id: relation_timestamps(obj)
            for obj in ArticlePlacement.objects.only("placement_id", "created_at", "updated_at").iterator()
        },
        "source_group_item": {
            (obj.group_id, obj.source_id): relation_timestamps(obj)
            for obj in SourceGroupItem.objects.only("group_id", "source_id", "created_at", "updated_at").iterator()
        },
    }


def relation_timestamps(obj):
    return {
        "created_at": obj.created_at,
        "updated_at": obj.updated_at,
    }


def timestamp_defaults(kind, key):
    now = timezone.now()
    existing = TARGET_PROVENANCE.get(kind, {}).get(key, {})
    return {
        "created_at": existing.get("created_at") or now,
        "updated_at": existing.get("updated_at") or now,
    }


def source_type_for_work(work):
    if work.work_type == Work.WorkType.ARTICLE:
        return Source.SourceType.ARTICLE
    if work.work_type == Work.WorkType.CONTAINER:
        return Source.SourceType.ISSUE
    if work.work_type == Work.WorkType.BOOK:
        return Source.SourceType.MONOGRAPH
    return Source.SourceType.UNKNOWN


def periodical_defaults(journal):
    return {
        "source_django_id": journal.source_django_id,
        "legacy_journal": journal,
        "title": journal.title,
        "parallel_title": journal.parallel_title,
        "title_remainder": journal.title_remainder,
        "responsibility_statement": journal.responsibility_statement,
        "place": journal.place,
        "publisher": journal.publisher,
        "issn": journal.issn,
        "periodicity": journal.periodicity,
        "numbering_start": journal.numbering_start,
        "numbering_end": journal.numbering_end,
        "start_year": journal.start_year,
        "end_year": journal.end_year,
        "title_history_note": journal.title_history_note,
        "description": journal.description,
    }


def journal_issue_defaults(issue):
    return {
        "source_django_id": issue.source_django_id,
        "legacy_journal_issue": issue,
        "issue_type": Issue.IssueType.PERIODICAL_ISSUE,
        "periodical_id": issue.journal_id,
        "source": None,
        "title": issue.title,
        "parallel_title": issue.parallel_title,
        "title_remainder": issue.title_remainder,
        "responsibility_statement": issue.responsibility_statement,
        "year": issue.year,
        "publication_date": issue.publication_date,
        "issue_number": issue.issue_number,
        "volume": issue.volume,
        "part_number": issue.part_number,
        "gross_number": issue.gross_number,
        "chronology": issue.chronology or issue.date_text,
        "enumeration": issue.enumeration,
        "publication_place": issue.publication_place,
        "publisher": issue.publisher,
        "publication_details": issue.publication_details,
        "issn": issue.issn,
        "isbn": issue.isbn,
        "notes": issue.notes,
    }


def container_work_issue_defaults(work):
    return {
        "source_django_id": work.source_django_id,
        "legacy_container_work": work,
        "issue_type": Issue.IssueType.COLLECTION,
        "periodical": None,
        "source_id": work.work_id,
        "title": work.title,
        "parallel_title": work.parallel_title,
        "title_remainder": work.title_remainder or work.subtitle,
        "responsibility_statement": work.responsibility_statement or work.responsibility_note,
        "year": work.inferred_year,
        "publication_date": work.publication_date,
        "issue_number": "",
        "volume": work.volume_number,
        "part_number": work.part_number,
        "gross_number": "",
        "chronology": "",
        "enumeration": work.volume_number or work.part_number,
        "publication_place": work.publication_place,
        "publisher": work.publisher,
        "publication_details": work.publication_details,
        "issn": work.issn,
        "isbn": work.isbn,
        "notes": work.notes,
    }


def legacy_collection_issue_defaults(collection):
    source_id = collection.parent_work_id if collection.parent_work_id else None
    return {
        "source_django_id": collection.source_django_id,
        "legacy_container_work": collection.parent_work,
        "issue_type": Issue.IssueType.COLLECTION,
        "periodical": None,
        "source_id": source_id,
        "title": collection.title,
        "year": collection.year,
        "publication_date": str(collection.year) if collection.year else "",
        "publication_place": collection.place,
        "publisher": collection.publisher,
        "publication_details": collection.publication_details,
        "notes": collection.source_text,
    }


def source_group_defaults(group):
    return {
        "source_django_id": group.source_django_id,
        "legacy_work_group": group,
        "title": group.title,
        "group_type": group.group_type,
        "note": group.note,
    }


def placement_issue_id(article):
    if article.journal_issue_id:
        return article.journal_issue_id
    if article.container_work_id:
        return container_issue_id(article.container_work)
    if article.collection_id:
        if article.collection.parent_work_id:
            return container_issue_id(article.collection.parent_work)
        return collection_issue_id(article.collection)
    return None


def container_issue_id(work):
    return f"issue-from-{work.work_id}"


def collection_issue_id(collection):
    return f"issue-from-{collection.collection_id}"


def reset_target_tables():
    ArticlePlacement.objects.all().delete()
    SourceGroupItem.objects.all().delete()
    SourceTag.objects.all().delete()
    SourceAuthor.objects.all().delete()
    Issue.objects.all().delete()
    SourceGroup.objects.all().delete()
    Periodical.objects.all().delete()
    Source.objects.all().delete()


def write_report(report):
    reports_dir = settings.PROJECT_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / "target_conversion_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
