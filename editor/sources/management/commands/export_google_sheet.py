from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sources.google_sheets import build_export_values, get_sheets_service, write_sheet_values
from sources.models import Article, Author, Journal, JournalIssue, Work


class Command(BaseCommand):
    help = "Exports editor data to Google Sheets."

    def add_arguments(self, parser):
        parser.add_argument("--spreadsheet-id", default=settings.GOOGLE_SHEETS_SPREADSHEET_ID)
        parser.add_argument("--credentials", default=settings.GOOGLE_SHEETS_CREDENTIALS)

    def handle(self, *args, **options):
        if not options["spreadsheet_id"]:
            raise CommandError("Нужен --spreadsheet-id или GOOGLE_SHEETS_SPREADSHEET_ID.")
        if not options["credentials"]:
            raise CommandError("Нужен --credentials или GOOGLE_SHEETS_CREDENTIALS.")
        try:
            service = get_sheets_service(options["credentials"])
            write_sheet_values(service, options["spreadsheet_id"], build_export_values())
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Экспорт завершен: "
                f"works={Work.objects.count()}, authors={Author.objects.count()}, "
                f"journals={Journal.objects.count()}, issues={JournalIssue.objects.count()}, "
                f"article_placements={Article.objects.count()}."
            )
        )
