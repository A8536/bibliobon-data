import json
import re
from pathlib import Path
from shutil import copy2

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from .models import Article, ArticlePlacement, Book, Issue, JournalIssue, Language, Source, Work


def build_issue_to_collection_plan(issue):
    articles = list(
        Article.objects.filter(journal_issue=issue)
        .select_related("work", "work__source_section", "work__language", "container_work")
        .prefetch_related("work__authors")
        .order_by("work__source_number", "work__work_id")
    )
    target_issue = related_or_none(issue, "target_issue")
    placements = affected_placements(articles, target_issue)
    collection_data = collection_data_from_issue(issue, target_issue)
    matches = find_collection_candidates(collection_data)
    blockers = []
    if not articles:
        blockers.append("В этом выпуске нет статей для переноса.")
    if len(matches) > 1:
        blockers.append("Найдено несколько похожих сборников. Нужно выбрать вручную, автоматическое преобразование заблокировано.")
    if any(article.container_work_id for article in articles):
        blockers.append("У одной или нескольких статей уже указан сборник. Автоматическая замена запрещена.")

    old_issue_cleanup_candidate = False
    if not blockers:
        old_issue_cleanup_candidate = True

    return {
        "issue": issue,
        "target_issue": target_issue,
        "source_journal_label": issue.journal.title,
        "source_issue_label": describe_journal_issue(issue),
        "collection": collection_data,
        "collection_label": collection_label(collection_data),
        "matches": matches,
        "will_reuse_collection": matches[0] if len(matches) == 1 else None,
        "articles": [
            {
                "article": article,
                "work": article.work,
                "label": work_label(article.work),
                "pages": (article.pages_raw or article.pages or article.work.article_pages or "").strip(),
            }
            for article in articles
        ],
        "placements": placements,
        "placement_count": len(placements),
        "blockers": blockers,
        "can_apply": not blockers,
        "old_issue_cleanup_candidate": old_issue_cleanup_candidate,
    }


def apply_issue_to_collection(issue):
    plan = build_issue_to_collection_plan(issue)
    if not plan["can_apply"]:
        return {"error": "Преобразование заблокировано: " + " ".join(plan["blockers"]), "message": "", "plan": plan}

    backup = backup_sqlite_database("before-issue-to-collection")
    report_data = {}
    with transaction.atomic():
        collection_work, created_collection = get_or_create_collection_work(plan)
        collection_issue, created_issue = get_or_create_collection_target_issue(collection_work, plan)
        moved_articles = []
        moved_placements = []
        for row in plan["articles"]:
            article = row["article"]
            article.container_work = collection_work
            article.collection = None
            article.journal_issue = None
            article.save(update_fields=["container_work", "collection", "journal_issue"])
            moved_articles.append({"article_id": article.article_id, "work_id": article.work_id})

        for placement in plan["placements"]:
            old_issue_id = placement.issue_id
            placement.issue = collection_issue
            placement.save(update_fields=["issue"])
            moved_placements.append({"placement_id": placement.placement_id, "from_issue_id": old_issue_id, "to_issue_id": collection_issue.issue_id})

        cleanup_candidate = issue_cleanup_candidate(issue, plan["target_issue"])
        report_data = {
            "operation": "issue_to_collection",
            "source_issue_id": issue.journal_issue_id,
            "source_issue_label": plan["source_issue_label"],
            "collection_work_id": collection_work.work_id,
            "collection_label": collection_label(plan["collection"]),
            "created_collection": created_collection,
            "collection_issue_id": collection_issue.issue_id,
            "created_collection_issue": created_issue,
            "moved_articles": moved_articles,
            "moved_placements": moved_placements,
            "old_issue_cleanup_candidate": cleanup_candidate,
            "backup_path": str(backup) if backup else "",
        }

    report_path = write_operation_report(report_data)
    message = (
        f"Выпуск преобразован в сборник: {collection_work.work_id}. "
        f"Перенесено статей: {len(report_data['moved_articles'])}; размещений: {len(report_data['moved_placements'])}."
    )
    if backup:
        message += f" Backup: {backup}."
    if report_path:
        message += f" Отчёт: {report_path}."
    return {"error": "", "message": message, "plan": build_issue_to_collection_plan(issue), "report": report_data, "backup_path": backup, "report_path": report_path}


def collection_data_from_issue(issue, target_issue):
    periodical = target_issue.periodical if target_issue and target_issue.periodical_id else None
    title = issue.journal.title.strip()
    place = first_nonempty(
        issue.publication_place,
        target_issue.publication_place if target_issue else "",
        periodical.place if periodical else "",
    )
    year = issue.year or (target_issue.year if target_issue else None) or extract_year(issue.publication_date) or extract_year(issue.publication_details)
    if not place and periodical:
        place = extract_place_from_publication_text(periodical.title, year)
    if not place:
        place = extract_place_from_publication_text(issue.publication_details, year)
    publication_date = str(year) if year else issue.publication_date or (target_issue.publication_date if target_issue else "")
    publication_details = first_nonempty(
        issue.publication_details,
        target_issue.publication_details if target_issue else "",
        collection_label({"title": title, "publication_place": place, "publication_date": publication_date, "year": year}),
    )
    return {
        "title": title,
        "publication_place": place,
        "publication_date": publication_date,
        "year": year,
        "publisher": first_nonempty(issue.publisher, target_issue.publisher if target_issue else ""),
        "publication_details": publication_details,
    }


def find_collection_candidates(collection):
    title_key = normalize(collection["title"])
    place_key = normalize(collection["publication_place"])
    year = collection["year"]
    candidates = []
    queryset = Work.objects.filter(Q(is_container=True) | Q(work_type=Work.WorkType.CONTAINER)).order_by("source_number", "work_id")
    for work in queryset:
        if normalize(work.title) != title_key:
            continue
        work_year = work.inferred_year or extract_year(work.publication_date)
        if year and work_year and int(year) != int(work_year):
            continue
        if place_key and work.publication_place and normalize(work.publication_place) != place_key:
            continue
        candidates.append(work)
    return candidates


def get_or_create_collection_work(plan):
    if plan["will_reuse_collection"]:
        return plan["will_reuse_collection"], False
    sample = plan["articles"][0]["work"]
    data = plan["collection"]
    work = Work.objects.create(
        work_id=next_work_id(),
        source_sequence=None,
        source_number=next_synthetic_source_number(""),
        source_page_marker="",
        source_section=sample.source_section,
        language=sample.language,
        work_type=Work.WorkType.CONTAINER,
        is_container=True,
        raw_author_string="",
        title=data["title"],
        publication_place=data["publication_place"],
        publisher=data["publisher"],
        publication_date=data["publication_date"],
        publication_details=data["publication_details"],
        description_status=Work.DescriptionStatus.NEEDS_REVIEW,
        inferred_year=data["year"],
        notes=f"Created by issue-to-collection conversion from {plan['issue'].journal_issue_id}",
    )
    ensure_book_for_work(work)
    ensure_source_for_collection_work(work, data)
    return work, True


def get_or_create_collection_target_issue(collection_work, plan):
    existing = related_or_none(collection_work, "target_container_issue")
    if existing:
        return existing, False
    data = plan["collection"]
    source = ensure_source_for_collection_work(collection_work, data)
    issue = Issue.objects.create(
        issue_id=next_id(Issue, "issue_id", "issue"),
        legacy_container_work=collection_work,
        issue_type=Issue.IssueType.COLLECTION,
        source=source,
        title=data["title"],
        year=data["year"],
        publication_date=data["publication_date"],
        publication_place=data["publication_place"],
        publisher=data["publisher"],
        publication_details=data["publication_details"],
    )
    return issue, True


def ensure_source_for_collection_work(work, data):
    source = related_or_none(work, "target_source")
    if source:
        return source
    return Source.objects.create(
        source_id=work.work_id if not Source.objects.filter(source_id=work.work_id).exists() else next_id(Source, "source_id", "source"),
        legacy_work=work,
        source_number=work.source_number,
        source_type=Source.SourceType.ISSUE,
        section=work.source_section,
        language=work.language,
        title=work.title,
        publication_place=data["publication_place"],
        publisher=data["publisher"],
        publication_date=data["publication_date"],
        inferred_year=data["year"],
        raw_publication_details=data["publication_details"],
        description_status=Source.DescriptionStatus.NEEDS_REVIEW,
    )


def affected_placements(articles, target_issue):
    article_ids = [article.article_id for article in articles]
    query = Q(legacy_article_id__in=article_ids)
    if target_issue:
        query |= Q(issue=target_issue)
    return list(
        ArticlePlacement.objects.filter(query)
        .select_related("legacy_article", "source", "issue", "issue__periodical", "issue__source")
        .distinct()
        .order_by("source__source_number", "placement_id")
    )


def issue_cleanup_candidate(issue, target_issue):
    if Article.objects.filter(journal_issue=issue).exists():
        return False
    if target_issue and ArticlePlacement.objects.filter(issue=target_issue).exists():
        return False
    return True


def describe_journal_issue(issue):
    bits = [issue.journal.title]
    if issue.year:
        bits.append(str(issue.year))
    if issue.issue_number:
        bits.append(issue_number_label(issue.issue_number))
    if issue.volume:
        bits.append(f"т. {issue.volume}")
    return ", ".join(bits)


def issue_number_label(value):
    value = str(value or "").strip()
    if not value:
        return ""
    if re.match(r"(?i)^вып", value):
        return value
    return f"№ {value}"


def work_label(work):
    authors = "; ".join(author.display_name for author in work.authors.all()) or work.raw_author_string
    bits = []
    if authors:
        bits.append(authors)
    bits.append(work.title)
    year = work.inferred_year or work.publication_date
    if year:
        bits.append(str(year))
    return " — ".join(bits)


def collection_label(data):
    label = data.get("title", "").strip()
    tail = []
    if data.get("publication_place"):
        tail.append(data["publication_place"])
    if data.get("publication_date") or data.get("year"):
        tail.append(str(data.get("publication_date") or data.get("year")))
    if tail:
        label = f"{label}. — {', '.join(tail)}"
    return label


def extract_year(value):
    match = re.search(r"(18|19|20)\d{2}", str(value or ""))
    return int(match.group(0)) if match else None


def extract_place_from_publication_text(value, year=None):
    text = str(value or "").strip()
    if not text:
        return ""
    pattern = r"[—-]\s*([^—,.;]+?)\s*,\s*" + (str(year) if year else r"(18|19|20)\d{2}")
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def normalize(value):
    value = str(value or "").casefold().replace("ё", "е")
    value = re.sub(r"[«»\"'.,:;()\[\]{}]", " ", value)
    value = re.sub(r"[‐‑‒–—-]", "-", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def first_nonempty(*values):
    for value in values:
        value = str(value or "").strip()
        if value:
            return value
    return ""


def ensure_book_for_work(work):
    book = Book.objects.filter(work=work).first()
    if book:
        return book
    book_id = f"book-from-{work.work_id}"
    if Book.objects.filter(book_id=book_id).exists() or len(book_id) > 64:
        book_id = next_id(Book, "book_id", "book")
    return Book.objects.create(book_id=book_id, work=work)


def next_synthetic_source_number(existing_work_id):
    existing = Work.objects.filter(work_id=existing_work_id).values_list("source_number", flat=True).first()
    if existing:
        return existing
    current = Work.objects.filter(source_number__gte=900000000).aggregate(max_number=Max("source_number"))["max_number"]
    return (current or 900000000) + 1


def next_work_id():
    number = Work.objects.count() + 1
    while True:
        candidate = f"work-{number:06d}"
        if not Work.objects.filter(work_id=candidate).exists():
            return candidate
        number += 1


def next_id(model, field_name, prefix):
    last = 0
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    for value in model.objects.values_list(field_name, flat=True):
        match = pattern.match(str(value))
        if match:
            last = max(last, int(match.group(1)))
    return f"{prefix}-{last + 1:06d}"


def related_or_none(obj, name):
    try:
        return getattr(obj, name)
    except ObjectDoesNotExist:
        return None


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


def write_operation_report(data):
    reports_dir = settings.PROJECT_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    report_path = reports_dir / f"issue_to_collection.{timestamp}.json"
    report_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path
