import csv
from pathlib import Path
from shutil import copy2

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from sources.models import Article, JournalIssue, Work


class Command(BaseCommand):
    help = "Moves article-specific issue fields to articles and clears redundant article fields."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Apply cleanup. Default is dry-run.")
        parser.add_argument("--skip-target-refresh", action="store_true", help="Do not rebuild target tables.")

    def handle(self, *args, **options):
        plan = build_plan()
        write_reports(plan)
        self.stdout.write(f"move_issue_details_to_article: {len(plan['move_issue_details_to_article'])}")
        self.stdout.write(f"clear_issue_details_same_existing: {len(plan['clear_issue_details_same_existing'])}")
        self.stdout.write(f"issue_details_conflicts: {len(plan['issue_details_conflicts'])}")
        self.stdout.write(f"issue_details_ambiguous_multi_article: {len(plan['issue_details_ambiguous_multi_article'])}")
        self.stdout.write(f"clear_article_volume_number: {len(plan['clear_article_volume_number'])}")
        self.stdout.write(f"article_volume_number_differs: {len(plan['article_volume_number_differs'])}")

        if not options["apply"]:
            self.stdout.write(self.style.WARNING("Dry run: ничего не изменено. Добавьте --apply для очистки."))
            return

        backup = backup_sqlite_database("before-normalize-article-issue-fields")
        if backup:
            self.stdout.write(self.style.WARNING(f"Backup создан: {backup}"))

        with transaction.atomic():
            stats = apply_plan(plan)

        if not options["skip_target_refresh"]:
            call_command("convert_legacy_to_target", "--apply", "--reset", verbosity=0)

        self.stdout.write(self.style.SUCCESS(f"Нормализация завершена: {stats}"))


def build_plan():
    plan = {
        "move_issue_details_to_article": [],
        "clear_issue_details_same_existing": [],
        "issue_details_conflicts": [],
        "issue_details_ambiguous_multi_article": [],
        "clear_article_volume_number": [],
        "article_volume_number_differs": [],
    }

    for issue in JournalIssue.objects.exclude(publication_details="").iterator():
        article_ids = list(Article.objects.filter(journal_issue=issue).values_list("article_id", flat=True))
        if len(article_ids) == 1:
            article = Article.objects.select_related("work").get(article_id=article_ids[0])
            work_details = clean(article.work.publication_details)
            issue_details = clean(issue.publication_details)
            row = {
                "journal_issue_id": issue.journal_issue_id,
                "article_id": article.article_id,
                "work_id": article.work_id,
                "issue_details": issue_details,
                "work_details": work_details,
            }
            if not work_details:
                plan["move_issue_details_to_article"].append(row)
            elif work_details == issue_details:
                plan["clear_issue_details_same_existing"].append(row)
            else:
                plan["issue_details_conflicts"].append(row)
        elif len(article_ids) > 1:
            plan["issue_details_ambiguous_multi_article"].append(
                {
                    "journal_issue_id": issue.journal_issue_id,
                    "article_count": len(article_ids),
                    "issue_details": clean(issue.publication_details),
                    "article_ids": "; ".join(article_ids[:20]),
                }
            )

    for article in Article.objects.select_related("work", "journal_issue").exclude(work__volume_number="").filter(journal_issue__isnull=False).iterator():
        work_volume = clean(article.work.volume_number)
        issue_number = clean(article.journal_issue.issue_number)
        row = {
            "article_id": article.article_id,
            "work_id": article.work_id,
            "journal_issue_id": article.journal_issue_id,
            "work_volume_number": work_volume,
            "issue_number": issue_number,
        }
        if normalized_issue_number(work_volume) == normalized_issue_number(issue_number):
            plan["clear_article_volume_number"].append(row)
        else:
            plan["article_volume_number_differs"].append(row)

    return plan


def apply_plan(plan):
    moved = 0
    cleared_same = 0
    for row in plan["move_issue_details_to_article"]:
        Work.objects.filter(work_id=row["work_id"], publication_details="").update(publication_details=row["issue_details"])
        JournalIssue.objects.filter(journal_issue_id=row["journal_issue_id"]).update(publication_details="")
        moved += 1
    for row in plan["clear_issue_details_same_existing"]:
        JournalIssue.objects.filter(journal_issue_id=row["journal_issue_id"]).update(publication_details="")
        cleared_same += 1
    cleared_volume = Work.objects.filter(
        work_id__in=[row["work_id"] for row in plan["clear_article_volume_number"]]
    ).update(volume_number="")
    return {
        "moved_issue_details_to_article": moved,
        "cleared_issue_details_same_existing": cleared_same,
        "cleared_article_volume_number": cleared_volume,
    }


def write_reports(plan):
    reports_dir = settings.PROJECT_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    for key in [
        "issue_details_conflicts",
        "issue_details_ambiguous_multi_article",
        "article_volume_number_differs",
    ]:
        rows = plan[key]
        path = reports_dir / f"{key}.tsv"
        if not rows:
            path.write_text("", encoding="utf-8")
            continue
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)


def clean(value):
    return str(value or "").strip()


def normalized_issue_number(value):
    value = clean(value)
    for prefix in ["№ ", "N ", "No. ", "No ", "#"]:
        if value.startswith(prefix):
            return value[len(prefix) :].strip()
    return value


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
