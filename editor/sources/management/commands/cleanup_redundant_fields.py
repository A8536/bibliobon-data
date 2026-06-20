from pathlib import Path
from shutil import copy2

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from sources.models import Article, JournalIssue, Work


class Command(BaseCommand):
    help = "Reports or clears mechanically redundant structured/raw fields."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Apply cleanup. Default is dry-run.")
        parser.add_argument(
            "--skip-target-refresh",
            action="store_true",
            help="Do not rebuild target Source/Issue tables after apply.",
        )

    def handle(self, *args, **options):
        plan = cleanup_plan()
        self.stdout.write(f"duplicate_extent_physical_description: {len(plan['duplicate_extent_physical_description'])}")
        self.stdout.write(f"duplicate_issue_number_enumeration: {len(plan['duplicate_issue_number_enumeration'])}")
        self.stdout.write(f"duplicate_article_container_place: {len(plan['duplicate_article_container_place'])}")
        self.stdout.write(f"duplicate_article_container_year: {len(plan['duplicate_article_container_year'])}")
        self.stdout.write(f"duplicate_article_container_publication_date: {len(plan['duplicate_article_container_publication_date'])}")
        self.stdout.write(f"duplicate_article_container_publisher: {len(plan['duplicate_article_container_publisher'])}")
        self.stdout.write(f"duplicate_article_container_manufacture_place: {len(plan['duplicate_article_container_manufacture_place'])}")
        self.stdout.write(f"duplicate_article_container_manufacturer: {len(plan['duplicate_article_container_manufacturer'])}")
        self.stdout.write(f"duplicate_article_container_manufacture_date: {len(plan['duplicate_article_container_manufacture_date'])}")
        self.stdout.write(f"duplicate_article_container_copyright_date: {len(plan['duplicate_article_container_copyright_date'])}")
        self.stdout.write(f"duplicate_article_container_extent: {len(plan['duplicate_article_container_extent'])}")
        self.stdout.write(f"duplicate_article_container_physical_description: {len(plan['duplicate_article_container_physical_description'])}")
        self.stdout.write(f"duplicate_article_container_illustrations: {len(plan['duplicate_article_container_illustrations'])}")
        self.stdout.write(f"duplicate_article_container_dimensions: {len(plan['duplicate_article_container_dimensions'])}")
        self.stdout.write(f"duplicate_article_container_accompanying_material: {len(plan['duplicate_article_container_accompanying_material'])}")
        self.stdout.write(f"duplicate_article_container_circulation: {len(plan['duplicate_article_container_circulation'])}")
        self.stdout.write(f"duplicate_article_container_series_statement: {len(plan['duplicate_article_container_series_statement'])}")
        self.stdout.write(f"duplicate_article_container_isbn: {len(plan['duplicate_article_container_isbn'])}")
        self.stdout.write(f"duplicate_article_container_issn: {len(plan['duplicate_article_container_issn'])}")
        self.stdout.write(f"duplicate_work_article_pages: {len(plan['duplicate_work_article_pages'])}")

        if not options["apply"]:
            self.stdout.write(self.style.WARNING("Dry run: ничего не изменено. Добавьте --apply для очистки."))
            return

        backup = backup_sqlite_database("before-cleanup-redundant-fields")
        if backup:
            self.stdout.write(self.style.WARNING(f"Backup создан: {backup}"))

        with transaction.atomic():
            cleared = apply_cleanup(plan)

        if not options["skip_target_refresh"]:
            call_command("convert_legacy_to_target", "--apply", "--reset", verbosity=0)

        self.stdout.write(self.style.SUCCESS(f"Очищено: {cleared}"))


def cleanup_plan():
    duplicate_extent_physical = list(
        Work.objects.exclude(extent="")
        .filter(extent=F("physical_description"))
        .values_list("work_id", flat=True)
    )

    duplicate_issue_enumeration = []
    for issue in JournalIssue.objects.exclude(issue_number="").exclude(enumeration="").only(
        "journal_issue_id",
        "issue_number",
        "enumeration",
    ):
        if normalized_enumeration(issue.enumeration) == issue.issue_number.strip():
            duplicate_issue_enumeration.append(issue.journal_issue_id)

    return {
        "duplicate_extent_physical_description": duplicate_extent_physical,
        "duplicate_issue_number_enumeration": duplicate_issue_enumeration,
        "duplicate_article_container_place": duplicate_article_container_field("publication_place"),
        "duplicate_article_container_year": duplicate_article_container_year(),
        "duplicate_article_container_publication_date": duplicate_article_container_field("publication_date"),
        "duplicate_article_container_publisher": duplicate_article_container_field("publisher"),
        "duplicate_article_container_manufacture_place": duplicate_article_container_field("manufacture_place"),
        "duplicate_article_container_manufacturer": duplicate_article_container_field("manufacturer"),
        "duplicate_article_container_manufacture_date": duplicate_article_container_field("manufacture_date"),
        "duplicate_article_container_copyright_date": duplicate_article_container_field("copyright_date"),
        "duplicate_article_container_extent": duplicate_article_container_field("extent"),
        "duplicate_article_container_physical_description": duplicate_article_container_field("physical_description"),
        "duplicate_article_container_illustrations": duplicate_article_container_field("illustrations"),
        "duplicate_article_container_dimensions": duplicate_article_container_field("dimensions"),
        "duplicate_article_container_accompanying_material": duplicate_article_container_field("accompanying_material"),
        "duplicate_article_container_circulation": duplicate_article_container_field("circulation"),
        "duplicate_article_container_series_statement": duplicate_article_container_field("series_statement"),
        "duplicate_article_container_isbn": duplicate_article_container_field("isbn"),
        "duplicate_article_container_issn": duplicate_article_container_field("issn"),
        "duplicate_work_article_pages": duplicate_work_article_pages(),
    }


def apply_cleanup(plan):
    cleared_physical = Work.objects.filter(
        work_id__in=plan["duplicate_extent_physical_description"],
    ).update(physical_description="")
    cleared_enumeration = JournalIssue.objects.filter(
        journal_issue_id__in=plan["duplicate_issue_number_enumeration"],
    ).update(enumeration="")
    cleared_article_place = Work.objects.filter(
        work_id__in=plan["duplicate_article_container_place"],
    ).update(publication_place="")
    cleared_article_year = Work.objects.filter(
        work_id__in=plan["duplicate_article_container_year"],
    ).update(inferred_year=None)
    cleared_article_publication_date = Work.objects.filter(
        work_id__in=plan["duplicate_article_container_publication_date"],
    ).update(publication_date="")
    cleared_article_publisher = Work.objects.filter(
        work_id__in=plan["duplicate_article_container_publisher"],
    ).update(publisher="")
    cleared_article_manufacture_place = Work.objects.filter(
        work_id__in=plan["duplicate_article_container_manufacture_place"],
    ).update(manufacture_place="")
    cleared_article_manufacturer = Work.objects.filter(
        work_id__in=plan["duplicate_article_container_manufacturer"],
    ).update(manufacturer="")
    cleared_article_manufacture_date = Work.objects.filter(
        work_id__in=plan["duplicate_article_container_manufacture_date"],
    ).update(manufacture_date="")
    cleared_article_copyright_date = Work.objects.filter(
        work_id__in=plan["duplicate_article_container_copyright_date"],
    ).update(copyright_date="")
    cleared_article_extent = Work.objects.filter(
        work_id__in=plan["duplicate_article_container_extent"],
    ).update(extent="")
    cleared_article_physical_description = Work.objects.filter(
        work_id__in=plan["duplicate_article_container_physical_description"],
    ).update(physical_description="")
    cleared_article_illustrations = Work.objects.filter(
        work_id__in=plan["duplicate_article_container_illustrations"],
    ).update(illustrations="")
    cleared_article_dimensions = Work.objects.filter(
        work_id__in=plan["duplicate_article_container_dimensions"],
    ).update(dimensions="")
    cleared_article_accompanying_material = Work.objects.filter(
        work_id__in=plan["duplicate_article_container_accompanying_material"],
    ).update(accompanying_material="")
    cleared_article_circulation = Work.objects.filter(
        work_id__in=plan["duplicate_article_container_circulation"],
    ).update(circulation="")
    cleared_article_series_statement = Work.objects.filter(
        work_id__in=plan["duplicate_article_container_series_statement"],
    ).update(series_statement="")
    cleared_article_isbn = Work.objects.filter(
        work_id__in=plan["duplicate_article_container_isbn"],
    ).update(isbn="")
    cleared_article_issn = Work.objects.filter(
        work_id__in=plan["duplicate_article_container_issn"],
    ).update(issn="")
    cleared_article_pages = Work.objects.filter(
        work_id__in=plan["duplicate_work_article_pages"],
    ).update(article_pages="")
    return {
        "physical_description": cleared_physical,
        "enumeration": cleared_enumeration,
        "article_publication_place": cleared_article_place,
        "article_inferred_year": cleared_article_year,
        "article_publication_date": cleared_article_publication_date,
        "article_publisher": cleared_article_publisher,
        "article_manufacture_place": cleared_article_manufacture_place,
        "article_manufacturer": cleared_article_manufacturer,
        "article_manufacture_date": cleared_article_manufacture_date,
        "article_copyright_date": cleared_article_copyright_date,
        "article_extent": cleared_article_extent,
        "article_physical_description": cleared_article_physical_description,
        "article_illustrations": cleared_article_illustrations,
        "article_dimensions": cleared_article_dimensions,
        "article_accompanying_material": cleared_article_accompanying_material,
        "article_circulation": cleared_article_circulation,
        "article_series_statement": cleared_article_series_statement,
        "article_isbn": cleared_article_isbn,
        "article_issn": cleared_article_issn,
        "article_pages": cleared_article_pages,
    }


def normalized_enumeration(value):
    value = str(value or "").strip()
    for prefix in ["№ ", "N ", "No. ", "No ", "#"]:
        if value.startswith(prefix):
            return value[len(prefix) :].strip()
    return value


def duplicate_article_container_field(field):
    duplicate_work_ids = []
    articles = Article.objects.select_related("work", "journal_issue", "container_work").exclude(**{f"work__{field}": ""})
    for article in articles:
        article_value = str(getattr(article.work, field) or "").strip()
        container_value = ""
        if article.journal_issue_id:
            if hasattr(article.journal_issue, field):
                container_value = str(getattr(article.journal_issue, field) or "").strip()
        elif article.container_work_id:
            container_value = str(getattr(article.container_work, field) or "").strip()
        if container_value and article_value.casefold() == container_value.casefold():
            duplicate_work_ids.append(article.work_id)
    return duplicate_work_ids


def duplicate_article_container_year():
    duplicate_work_ids = []
    articles = Article.objects.select_related("work", "journal_issue", "container_work").filter(work__inferred_year__isnull=False)
    for article in articles:
        container_year = None
        if article.journal_issue_id:
            container_year = article.journal_issue.year
        elif article.container_work_id:
            container_year = article.container_work.inferred_year
        if container_year and article.work.inferred_year == container_year:
            duplicate_work_ids.append(article.work_id)
    return duplicate_work_ids


def duplicate_work_article_pages():
    duplicate_work_ids = []
    articles = Article.objects.select_related("work").exclude(work__article_pages="")
    for article in articles:
        work_pages = article.work.article_pages.strip()
        placement_pages = (article.pages_raw or article.pages or "").strip()
        if placement_pages and work_pages == placement_pages:
            duplicate_work_ids.append(article.work_id)
    return duplicate_work_ids


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
