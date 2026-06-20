import json
from pathlib import Path
from shutil import copy2

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from sources.models import Article, Book, Journal, Work


class Command(BaseCommand):
    help = "Converts one or more legacy journals into one collection/container work."

    def add_arguments(self, parser):
        parser.add_argument("--journal-id", action="append", required=True, help="Journal ID to convert. Repeat to merge.")
        parser.add_argument("--title", default="", help="Container title. Defaults to the first journal title.")
        parser.add_argument("--apply", action="store_true", help="Apply changes. Default is dry-run.")
        parser.add_argument("--skip-target-refresh", action="store_true", help="Do not rebuild target Source/Issue tables after apply.")

    def handle(self, *args, **options):
        journal_ids = list(dict.fromkeys(options["journal_id"]))
        journals = list(Journal.objects.filter(journal_id__in=journal_ids).prefetch_related("issues__articles__work"))
        found_ids = {journal.journal_id for journal in journals}
        missing_ids = [journal_id for journal_id in journal_ids if journal_id not in found_ids]
        if missing_ids:
            raise CommandError(f"Journals not found: {', '.join(missing_ids)}")

        plan = build_plan(journals, options["title"])
        self.stdout.write(json.dumps(plan, ensure_ascii=False, indent=2))
        if not options["apply"]:
            self.stdout.write(self.style.WARNING("Dry run: ничего не изменено. Добавьте --apply для записи."))
            return

        backup = backup_sqlite_database("before-journal-to-collection")
        if backup:
            self.stdout.write(self.style.WARNING(f"Backup создан: {backup}"))

        with transaction.atomic():
            result = apply_plan(journals, plan)

        if not options["skip_target_refresh"]:
            call_command("convert_legacy_to_target", "--apply", "--reset", verbosity=0)

        self.stdout.write(self.style.SUCCESS(json.dumps(result, ensure_ascii=False, indent=2)))


def build_plan(journals, title):
    ordered = sorted(journals, key=lambda journal: journal.journal_id)
    first = ordered[0]
    articles = []
    issues = []
    for journal in ordered:
        for issue in journal.issues.all():
            issues.append(
                {
                    "journal_issue_id": issue.journal_issue_id,
                    "journal_id": journal.journal_id,
                    "year": issue.year,
                    "issue_number": issue.issue_number,
                    "volume": issue.volume,
                    "publication_details": issue.publication_details,
                }
            )
            for article in issue.articles.all():
                articles.append(
                    {
                        "article_id": article.article_id,
                        "work_id": article.work_id,
                        "title": article.work.title,
                        "pages": article.pages or article.pages_raw or article.work.article_pages,
                    }
                )

    container_title = title.strip() or first.title
    return {
        "container_work_id": f"work-container-{first.journal_id}",
        "container_title": container_title,
        "journals": [{"journal_id": journal.journal_id, "title": journal.title} for journal in ordered],
        "issues_to_remove": issues,
        "articles_to_move": articles,
        "article_count": len(articles),
    }


def apply_plan(journals, plan):
    if not plan["articles_to_move"]:
        raise CommandError("No articles found; refusing to create an empty collection container.")

    deleted_journal_ids = [journal.journal_id for journal in journals]
    sample_work = Work.objects.get(work_id=plan["articles_to_move"][0]["work_id"])
    issue_details = [
        issue["publication_details"]
        for issue in plan["issues_to_remove"]
        if issue["publication_details"]
    ]
    year = next((issue["year"] for issue in plan["issues_to_remove"] if issue["year"]), None)
    container, created = Work.objects.update_or_create(
        work_id=plan["container_work_id"],
        defaults={
            "source_sequence": None,
            "source_number": next_synthetic_source_number(plan["container_work_id"]),
            "source_page_marker": "",
            "source_section": sample_work.source_section,
            "language": sample_work.language,
            "work_type": Work.WorkType.BOOK,
            "raw_author_string": "",
            "title": plan["container_title"],
            "publication_date": str(year) if year else "",
            "inferred_year": year,
            "publication_details": "\n".join(issue_details),
            "description_status": Work.DescriptionStatus.NEEDS_REVIEW,
            "notes": "Created by convert_journal_to_collection from: "
            + ", ".join(journal.journal_id for journal in journals),
        },
    )
    ensure_book_for_work(container)

    moved = 0
    for journal in journals:
        for issue in list(journal.issues.all()):
            moved += Article.objects.filter(journal_issue=issue).update(journal_issue=None, container_work=container)
            issue.delete()
        journal.delete()

    return {
        "container_work_id": container.work_id,
        "created_container": created,
        "moved_articles": moved,
        "deleted_journals": deleted_journal_ids,
    }


def ensure_book_for_work(work):
    book = Book.objects.filter(work=work).first()
    if book:
        return book
    book_id = f"book-from-{work.work_id}"
    if Book.objects.filter(book_id=book_id).exists():
        book_id = next_synthetic_book_id()
    return Book.objects.create(book_id=book_id, work=work)


def next_synthetic_book_id():
    number = Book.objects.count() + 1
    while True:
        candidate = f"book-{number:06d}"
        if not Book.objects.filter(book_id=candidate).exists():
            return candidate
        number += 1


def next_synthetic_source_number(existing_work_id):
    existing = Work.objects.filter(work_id=existing_work_id).values_list("source_number", flat=True).first()
    if existing:
        return existing
    current = Work.objects.filter(source_number__gte=900000000).aggregate(max_number=Max("source_number"))["max_number"]
    return (current or 900000000) + 1


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
