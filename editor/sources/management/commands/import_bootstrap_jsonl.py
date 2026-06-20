import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from sources.models import (
    Article,
    Author,
    Book,
    Collection,
    Journal,
    JournalIssue,
    Language,
    Section,
    Tag,
    Work,
    WorkAuthor,
    WorkGroup,
    WorkGroupItem,
    WorkTag,
)


class Command(BaseCommand):
    help = "Imports bootstrap JSONL files from bibliobon-data/data into the editor database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--data-dir",
            default=settings.PROJECT_ROOT / "data",
            type=Path,
            help="Directory with bootstrap JSONL files.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        data_dir = options["data_dir"]
        rows = {name: read_jsonl(data_dir / f"{name}.jsonl") for name in FILES}

        for row in rows["languages"]:
            Language.objects.update_or_create(
                language_id=row["language_id"],
                defaults=fields(row, ["source_django_id", "code", "title", "description", "sort_order"]),
            )

        for row in rows["sections"]:
            Section.objects.update_or_create(
                section_id=row["section_id"],
                defaults={
                    **fields(row, ["source_django_id", "source_code", "title", "description", "note", "sort_order"]),
                    "parent": None,
                },
            )
        for row in rows["sections"]:
            if row.get("parent_section_id"):
                Section.objects.filter(section_id=row["section_id"]).update(parent_id=row["parent_section_id"])

        for row in rows["tags"]:
            Tag.objects.update_or_create(
                tag_id=row["tag_id"],
                defaults={
                    **fields(row, ["source_django_id", "title", "tag_type", "description", "sort_order"]),
                    "parent": None,
                },
            )
        for row in rows["tags"]:
            if row.get("parent_tag_id"):
                Tag.objects.filter(tag_id=row["tag_id"]).update(parent_id=row["parent_tag_id"])

        for row in rows["authors"]:
            Author.objects.update_or_create(
                author_id=row["author_id"],
                defaults={
                    **fields(row, ["source_django_id", "display_name", "sort_name", "aliases", "note"]),
                    "heading_name": row.get("sort_name") or row.get("display_name") or "",
                },
            )

        for row in rows["journals"]:
            Journal.objects.update_or_create(
                journal_id=row["journal_id"],
                defaults=fields(row, ["source_django_id", "title", "place", "description"]),
            )

        for row in rows["journal_issues"]:
            JournalIssue.objects.update_or_create(
                journal_issue_id=row["journal_issue_id"],
                defaults={
                    **fields(row, ["source_django_id", "year", "issue_number", "volume", "date_text", "publication_details"]),
                    "publication_date": str(row.get("year") or ""),
                    "chronology": row.get("date_text") or str(row.get("year") or ""),
                    "enumeration": issue_enumeration(row),
                    "journal_id": row["journal_id"],
                },
            )

        for row in rows["works"]:
            Work.objects.update_or_create(
                work_id=row["work_id"],
                defaults={
                    **fields(
                        row,
                        [
                            "source_django_id",
                            "source_sequence",
                            "source_number",
                            "source_page_marker",
                            "work_type",
                            "raw_author_string",
                            "title",
                            "subtitle",
                            "volume_number",
                            "responsibility_note",
                            "host_title",
                            "publication_place",
                            "publisher",
                            "physical_description",
                            "article_pages",
                            "publication_details",
                            "public_review",
                            "inferred_year",
                        ],
                    ),
                    "source_section_id": row.get("source_section_id"),
                    "language_id": row["language_id"],
                    "responsibility_statement": row.get("responsibility_note") or "",
                    "publication_date": str(row.get("inferred_year") or ""),
                    "extent": row.get("physical_description") or "",
                    "description_status": "partial" if row.get("publication_place") or row.get("publisher") or row.get("inferred_year") else "raw_only",
                },
            )

        for row in rows["books"]:
            Book.objects.update_or_create(
                book_id=row["book_id"],
                defaults={
                    **fields(row, ["source_django_id", "edition", "page_count", "isbn"]),
                    "work_id": row["work_id"],
                },
            )

        for row in rows["collections"]:
            Collection.objects.update_or_create(
                collection_id=row["collection_id"],
                defaults={
                    **fields(row, ["source_django_id", "title", "publication_details", "year", "place", "publisher", "source_text"]),
                    "parent_work_id": row.get("parent_work_id"),
                },
            )

        for row in rows["articles"]:
            Article.objects.update_or_create(
                article_id=row["article_id"],
                defaults={
                    **fields(row, ["source_django_id", "pages"]),
                    "pages_raw": row.get("pages") or "",
                    **page_bounds(row.get("pages") or ""),
                    "work_id": row["work_id"],
                    "container_work_id": row.get("container_work_id"),
                    "collection_id": row.get("collection_id"),
                    "journal_issue_id": row.get("journal_issue_id"),
                },
            )

        for row in rows["work_groups"]:
            WorkGroup.objects.update_or_create(
                group_id=row["group_id"],
                defaults=fields(row, ["source_django_id", "title", "group_type", "note"]),
            )

        WorkAuthor.objects.all().delete()
        for row in rows["work_authors"]:
            WorkAuthor.objects.create(
                work_id=row["work_id"],
                author_id=row["author_id"],
                sort_order=row.get("sort_order") or 0,
                role=row.get("role") or "",
                source_text=row.get("source_text") or "",
                name_as_printed=row.get("source_text") or "",
            )

        WorkTag.objects.all().delete()
        for row in rows["work_tags"]:
            WorkTag.objects.create(
                work_id=row["work_id"],
                tag_id=row["tag_id"],
                sort_order=row.get("sort_order") or 0,
                source_text=row.get("source_text") or "",
            )

        WorkGroupItem.objects.all().delete()
        for row in rows["work_group_items"]:
            WorkGroupItem.objects.create(
                group_id=row["group_id"],
                work_id=row["work_id"],
                sort_order=row.get("sort_order") or 0,
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {Work.objects.count()} works, "
                f"{Author.objects.count()} authors, "
                f"{Article.objects.count()} article links."
            )
        )


FILES = [
    "languages",
    "sections",
    "tags",
    "authors",
    "journals",
    "journal_issues",
    "works",
    "books",
    "collections",
    "articles",
    "work_groups",
    "work_authors",
    "work_tags",
    "work_group_items",
]


def read_jsonl(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def fields(row, names):
    return {name: row.get(name) for name in names}


def issue_enumeration(row):
    parts = []
    if row.get("issue_number"):
        parts.append(f"№ {row['issue_number']}")
    if row.get("volume"):
        parts.append(f"Т. {row['volume']}")
    return ". ".join(parts)


def page_bounds(value):
    import re

    numbers = [int(item) for item in re.findall(r"\d+", str(value or ""))]
    if not numbers:
        return {"page_start": None, "page_end": None}
    if len(numbers) == 1:
        return {"page_start": numbers[0], "page_end": numbers[0]}
    return {"page_start": numbers[0], "page_end": numbers[-1]}
