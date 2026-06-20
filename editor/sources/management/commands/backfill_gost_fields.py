import re

from django.core.management.base import BaseCommand
from django.db import transaction

from sources.models import Article, Author, JournalIssue, Work, WorkAuthor


class Command(BaseCommand):
    help = "Backfills structured ГОСТ fields from existing legacy/editor data."

    @transaction.atomic
    def handle(self, *args, **options):
        stats = {
            "authors": 0,
            "work_authors": 0,
            "works": 0,
            "issues": 0,
            "articles": 0,
        }

        for author in Author.objects.all():
            changed = []
            if not author.heading_name:
                author.heading_name = author.sort_name or author.display_name
                changed.append("heading_name")
            if changed:
                author.save(update_fields=changed)
                stats["authors"] += 1

        primary_seen = set()
        for link in WorkAuthor.objects.select_related("author").order_by("work_id", "sort_order", "id"):
            changed = []
            if not link.name_as_printed:
                link.name_as_printed = link.source_text or link.author.display_name
                changed.append("name_as_printed")
            if link.work_id not in primary_seen:
                primary_seen.add(link.work_id)
                if not link.is_primary_heading:
                    link.is_primary_heading = True
                    changed.append("is_primary_heading")
            if changed:
                link.save(update_fields=changed)
                stats["work_authors"] += 1

        for work in Work.objects.all():
            changed = []
            if not work.responsibility_statement and work.responsibility_note:
                work.responsibility_statement = work.responsibility_note
                changed.append("responsibility_statement")
            if not work.publication_date and work.inferred_year:
                work.publication_date = str(work.inferred_year)
                changed.append("publication_date")
            if not work.extent and work.physical_description:
                work.extent = work.physical_description
                changed.append("extent")
            next_status = "partial" if has_structured_publication(work) else "raw_only"
            if work.description_status != next_status:
                work.description_status = next_status
                changed.append("description_status")
            if changed:
                work.save(update_fields=changed)
                stats["works"] += 1

        for issue in JournalIssue.objects.all():
            changed = []
            if not issue.publication_date and issue.year:
                issue.publication_date = str(issue.year)
                changed.append("publication_date")
            if not issue.chronology:
                issue.chronology = issue.date_text or str(issue.year or "")
                changed.append("chronology")
            if not issue.enumeration:
                issue.enumeration = issue_enumeration(issue)
                changed.append("enumeration")
            if changed:
                issue.save(update_fields=changed)
                stats["issues"] += 1

        for article in Article.objects.select_related("work"):
            changed = []
            raw_pages = article.pages or article.work.article_pages
            if raw_pages and not article.pages_raw:
                article.pages_raw = raw_pages
                changed.append("pages_raw")
            if article.pages_raw and (article.page_start is None or article.page_end is None):
                page_start, page_end = page_bounds(article.pages_raw)
                if page_start is not None and article.page_start != page_start:
                    article.page_start = page_start
                    changed.append("page_start")
                if page_end is not None and article.page_end != page_end:
                    article.page_end = page_end
                    changed.append("page_end")
            if changed:
                article.save(update_fields=changed)
                stats["articles"] += 1

        self.stdout.write(self.style.SUCCESS(f"Backfilled ГОСТ fields: {stats}"))


def has_structured_publication(work):
    return bool(
        work.publication_place
        or work.publisher
        or work.publication_date
        or work.inferred_year
        or work.extent
        or work.isbn
        or work.issn
    )


def issue_enumeration(issue):
    parts = []
    if issue.issue_number:
        parts.append(f"№ {issue.issue_number}")
    if issue.volume:
        parts.append(f"Т. {issue.volume}")
    if issue.gross_number:
        parts.append(f"({issue.gross_number})")
    return ". ".join(parts)


def page_bounds(value):
    numbers = [int(item) for item in re.findall(r"\d+", str(value or ""))]
    if not numbers:
        return None, None
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    return numbers[0], numbers[-1]
