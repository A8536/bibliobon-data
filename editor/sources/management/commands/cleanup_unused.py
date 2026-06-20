from pathlib import Path
from shutil import copy2

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from sources.models import Author, Collection, Journal, JournalIssue, Tag, WorkGroup


class Command(BaseCommand):
    help = "Reports or deletes unused editor data."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Delete unused rows. Default is dry-run report.")

    def handle(self, *args, **options):
        plan = cleanup_plan()
        for key, rows in plan.items():
            self.stdout.write(f"{key}: {len(rows)}")

        if not options["apply"]:
            self.stdout.write(self.style.WARNING("Dry run: ничего не удалено. Добавьте --apply для удаления."))
            return

        backup = backup_sqlite_database("before-cleanup-unused")
        if backup:
            self.stdout.write(self.style.WARNING(f"Backup создан: {backup}"))

        deleted = apply_cleanup(plan)
        self.stdout.write(self.style.SUCCESS(f"Удалено: {deleted}"))


def cleanup_plan():
    return {
        "unused_authors": list(
            Author.objects.annotate(work_count=Count("works", distinct=True))
            .filter(work_count=0)
            .values_list("author_id", flat=True)
        ),
        "unused_tags": list(
            Tag.objects.annotate(work_count=Count("works", distinct=True), child_count=Count("children", distinct=True))
            .filter(work_count=0, child_count=0)
            .values_list("tag_id", flat=True)
        ),
        "empty_journal_issues": list(
            JournalIssue.objects.annotate(article_count=Count("articles", distinct=True))
            .filter(article_count=0)
            .values_list("journal_issue_id", flat=True)
        ),
        "empty_journals": list(
            Journal.objects.annotate(issue_count=Count("issues", distinct=True))
            .filter(issue_count=0)
            .values_list("journal_id", flat=True)
        ),
        "empty_work_groups": list(
            WorkGroup.objects.annotate(item_count=Count("items", distinct=True))
            .filter(item_count=0)
            .values_list("group_id", flat=True)
        ),
        "unused_legacy_collections": list(
            Collection.objects.annotate(article_count=Count("articles", distinct=True))
            .filter(article_count=0, parent_work__isnull=True)
            .values_list("collection_id", flat=True)
        ),
    }


@transaction.atomic
def apply_cleanup(plan):
    deleted = {}
    deleted["unused_authors"] = Author.objects.filter(author_id__in=plan["unused_authors"]).delete()[0]
    deleted["unused_tags"] = Tag.objects.filter(tag_id__in=plan["unused_tags"]).delete()[0]
    deleted["empty_journal_issues"] = JournalIssue.objects.filter(journal_issue_id__in=plan["empty_journal_issues"]).delete()[0]
    empty_journal_ids = set(plan["empty_journals"])
    empty_journal_ids.update(
        Journal.objects.annotate(issue_count=Count("issues", distinct=True))
        .filter(issue_count=0)
        .values_list("journal_id", flat=True)
    )
    deleted["empty_journals"] = Journal.objects.filter(journal_id__in=empty_journal_ids).delete()[0]
    deleted["empty_work_groups"] = WorkGroup.objects.filter(group_id__in=plan["empty_work_groups"]).delete()[0]
    deleted["unused_legacy_collections"] = Collection.objects.filter(collection_id__in=plan["unused_legacy_collections"]).delete()[0]
    return deleted


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
