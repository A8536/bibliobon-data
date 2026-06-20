import hashlib
import json
import sqlite3
from pathlib import Path
from shutil import copy2

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from sources.models import (
    ArticlePlacement,
    Author,
    Issue,
    Language,
    Periodical,
    Section,
    Source,
    SourceAuthor,
    SourceGroup,
    SourceGroupItem,
    SourceTag,
    Tag,
)


CONTRACT_VERSION = "0.3.0"
ARTIFACT_SCHEMA_VERSION = 3


class Command(BaseCommand):
    help = "Exports site-consumable data/bibliobon.sqlite and data/site_contract.json."

    def add_arguments(self, parser):
        parser.add_argument("--output", default=settings.PROJECT_ROOT / "data" / "bibliobon.sqlite")
        parser.add_argument("--contract", default=settings.PROJECT_ROOT / "data" / "site_contract.json")
        parser.add_argument("--manifest", default=settings.PROJECT_ROOT / "data" / "build_manifest.json")
        parser.add_argument("--skip-target-refresh", action="store_true")
        parser.add_argument("--no-backup", action="store_true")

    def handle(self, *args, **options):
        output_path = Path(options["output"])
        contract_path = Path(options["contract"])
        manifest_path = Path(options["manifest"])

        if not options["skip_target_refresh"]:
            call_command("convert_legacy_to_target", "--apply", "--reset", verbosity=0)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        backups = []
        if not options["no_backup"]:
            for path in (output_path, contract_path, manifest_path):
                backup = backup_existing(path)
                if backup:
                    backups.append(str(backup))

        tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        if tmp_path.exists():
            tmp_path.unlink()
        export_sqlite(tmp_path)
        tmp_path.replace(output_path)

        contract = build_contract(output_path)
        write_json(contract_path, contract)
        manifest = build_manifest(output_path, contract_path, contract, backups)
        write_json(manifest_path, manifest)

        self.stdout.write(self.style.SUCCESS(f"Wrote {output_path}"))
        self.stdout.write(self.style.SUCCESS(f"Wrote {contract_path}"))
        self.stdout.write(self.style.SUCCESS(f"Wrote {manifest_path}"))
        if backups:
            self.stdout.write(self.style.WARNING("Backups:"))
            for backup in backups:
                self.stdout.write(f"  {backup}")


def export_sqlite(path):
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        create_schema(conn)
        insert_metadata(conn)
        insert_languages(conn)
        insert_sections(conn)
        insert_tags(conn)
        insert_authors(conn)
        insert_sources(conn)
        insert_source_authors(conn)
        insert_source_tags(conn)
        insert_periodicals(conn)
        insert_issues(conn)
        insert_article_placements(conn)
        insert_source_groups(conn)
        insert_source_group_items(conn)
        create_indexes(conn)
        conn.execute(f"PRAGMA user_version = {ARTIFACT_SCHEMA_VERSION}")
        conn.commit()


def create_schema(conn):
    statements = [
        """
        CREATE TABLE export_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE languages (
            language_id TEXT PRIMARY KEY,
            source_django_id INTEGER,
            code TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            sort_order INTEGER NOT NULL
        )
        """,
        """
        CREATE TABLE sections (
            section_id TEXT PRIMARY KEY,
            source_django_id INTEGER,
            source_code TEXT NOT NULL,
            parent_id TEXT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            note TEXT NOT NULL,
            sort_order INTEGER NOT NULL
        )
        """,
        """
        CREATE TABLE tags (
            tag_id TEXT PRIMARY KEY,
            source_django_id INTEGER,
            title TEXT NOT NULL,
            tag_type TEXT NOT NULL,
            parent_id TEXT,
            description TEXT NOT NULL,
            sort_order INTEGER NOT NULL
        )
        """,
        """
        CREATE TABLE authors (
            author_id TEXT PRIMARY KEY,
            source_django_id INTEGER,
            display_name TEXT NOT NULL,
            heading_name TEXT NOT NULL,
            sort_name TEXT NOT NULL,
            aliases TEXT NOT NULL,
            person_dates TEXT NOT NULL,
            authority_note TEXT NOT NULL,
            note TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE sources (
            source_id TEXT PRIMARY KEY,
            source_django_id INTEGER,
            legacy_work_id TEXT,
            source_sequence INTEGER,
            source_number INTEGER NOT NULL,
            source_page_marker TEXT NOT NULL,
            source_type TEXT NOT NULL,
            section_id TEXT,
            language_id TEXT NOT NULL,
            raw_author_string TEXT NOT NULL,
            title TEXT NOT NULL,
            parallel_title TEXT NOT NULL,
            subtitle TEXT NOT NULL,
            title_remainder TEXT NOT NULL,
            volume_number TEXT NOT NULL,
            part_number TEXT NOT NULL,
            part_title TEXT NOT NULL,
            responsibility_statement TEXT NOT NULL,
            edition_statement TEXT NOT NULL,
            additional_edition_statement TEXT NOT NULL,
            publication_place TEXT NOT NULL,
            publisher TEXT NOT NULL,
            publication_date TEXT NOT NULL,
            inferred_year INTEGER,
            manufacture_place TEXT NOT NULL,
            manufacturer TEXT NOT NULL,
            manufacture_date TEXT NOT NULL,
            copyright_date TEXT NOT NULL,
            extent TEXT NOT NULL,
            illustrations TEXT NOT NULL,
            dimensions TEXT NOT NULL,
            accompanying_material TEXT NOT NULL,
            circulation TEXT NOT NULL,
            series_statement TEXT NOT NULL,
            notes TEXT NOT NULL,
            bibliography_note TEXT NOT NULL,
            index_note TEXT NOT NULL,
            contents_note TEXT NOT NULL,
            isbn TEXT NOT NULL,
            issn TEXT NOT NULL,
            doi TEXT NOT NULL,
            url TEXT NOT NULL,
            access_date TEXT,
            content_type TEXT NOT NULL,
            media_type TEXT NOT NULL,
            carrier_type TEXT NOT NULL,
            raw_publication_details TEXT NOT NULL,
            raw_host_title TEXT NOT NULL,
            public_review TEXT NOT NULL,
            data_source TEXT NOT NULL,
            first_seen_at TEXT,
            updated_at TEXT,
            description_status TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE source_authors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            author_id TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            role TEXT NOT NULL,
            source_text TEXT NOT NULL,
            name_as_printed TEXT NOT NULL,
            include_in_responsibility INTEGER NOT NULL,
            is_primary_heading INTEGER NOT NULL,
            created_at TEXT,
            updated_at TEXT
        )
        """,
        """
        CREATE TABLE source_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            tag_id TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            source_text TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        )
        """,
        """
        CREATE TABLE periodicals (
            periodical_id TEXT PRIMARY KEY,
            source_django_id INTEGER,
            legacy_journal_id TEXT,
            title TEXT NOT NULL,
            parallel_title TEXT NOT NULL,
            title_remainder TEXT NOT NULL,
            responsibility_statement TEXT NOT NULL,
            place TEXT NOT NULL,
            publisher TEXT NOT NULL,
            issn TEXT NOT NULL,
            periodicity TEXT NOT NULL,
            numbering_start TEXT NOT NULL,
            numbering_end TEXT NOT NULL,
            start_year INTEGER,
            end_year INTEGER,
            title_history_note TEXT NOT NULL,
            description TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE issues (
            issue_id TEXT PRIMARY KEY,
            source_django_id INTEGER,
            legacy_journal_issue_id TEXT,
            legacy_container_work_id TEXT,
            issue_type TEXT NOT NULL,
            periodical_id TEXT,
            source_id TEXT,
            title TEXT NOT NULL,
            parallel_title TEXT NOT NULL,
            title_remainder TEXT NOT NULL,
            responsibility_statement TEXT NOT NULL,
            year INTEGER,
            publication_date TEXT NOT NULL,
            issue_number TEXT NOT NULL,
            volume TEXT NOT NULL,
            part_number TEXT NOT NULL,
            gross_number TEXT NOT NULL,
            chronology TEXT NOT NULL,
            enumeration TEXT NOT NULL,
            publication_place TEXT NOT NULL,
            publisher TEXT NOT NULL,
            raw_publication_details TEXT NOT NULL,
            issn TEXT NOT NULL,
            isbn TEXT NOT NULL,
            notes TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE article_placements (
            placement_id TEXT PRIMARY KEY,
            source_django_id INTEGER,
            legacy_article_id TEXT,
            source_id TEXT NOT NULL,
            issue_id TEXT NOT NULL,
            pages_raw TEXT NOT NULL,
            page_start INTEGER,
            page_end INTEGER,
            location_note TEXT NOT NULL,
            placement_note TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        )
        """,
        """
        CREATE TABLE source_groups (
            group_id TEXT PRIMARY KEY,
            source_django_id INTEGER,
            legacy_work_group_id TEXT,
            title TEXT NOT NULL,
            group_type TEXT NOT NULL,
            note TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE source_group_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            created_at TEXT,
            updated_at TEXT
        )
        """,
    ]
    for statement in statements:
        conn.execute(statement)


def insert_metadata(conn):
    rows = [
        ("contract_version", CONTRACT_VERSION),
        ("artifact_schema_version", str(ARTIFACT_SCHEMA_VERSION)),
        ("generated_at", timezone.now().isoformat()),
        ("generator", "editor.sources.export_site_artifact"),
    ]
    conn.executemany("INSERT INTO export_metadata (key, value) VALUES (?, ?)", rows)


def insert_languages(conn):
    rows = [
        (obj.language_id, obj.source_django_id, obj.code, obj.title, obj.description, obj.sort_order)
        for obj in Language.objects.order_by("sort_order", "title", "language_id")
    ]
    conn.executemany("INSERT INTO languages VALUES (?, ?, ?, ?, ?, ?)", rows)


def insert_sections(conn):
    rows = [
        (obj.section_id, obj.source_django_id, obj.source_code, obj.parent_id, obj.title, obj.description, obj.note, obj.sort_order)
        for obj in Section.objects.order_by("sort_order", "source_code", "section_id")
    ]
    conn.executemany("INSERT INTO sections VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)


def insert_tags(conn):
    rows = [
        (obj.tag_id, obj.source_django_id, obj.title, obj.tag_type, obj.parent_id, obj.description, obj.sort_order)
        for obj in Tag.objects.order_by("sort_order", "title", "tag_id")
    ]
    conn.executemany("INSERT INTO tags VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def insert_authors(conn):
    rows = [
        (
            obj.author_id,
            obj.source_django_id,
            obj.display_name,
            obj.heading_name,
            obj.sort_name,
            obj.aliases,
            obj.person_dates,
            obj.authority_note,
            obj.note,
        )
        for obj in Author.objects.order_by("sort_name", "display_name", "author_id")
    ]
    conn.executemany("INSERT INTO authors VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


def insert_sources(conn):
    rows = [source_row(obj) for obj in Source.objects.select_related("legacy_work", "section", "language").order_by("source_sequence", "source_number", "source_id")]
    placeholders = ", ".join(["?"] * len(source_row(Source.objects.first())))
    conn.executemany(f"INSERT INTO sources VALUES ({placeholders})", rows)


def source_row(obj):
    return (
        obj.source_id,
        obj.source_django_id,
        obj.legacy_work_id,
        obj.source_sequence,
        obj.source_number,
        obj.source_page_marker,
        obj.source_type,
        obj.section_id,
        obj.language_id,
        obj.raw_author_string,
        obj.title,
        obj.parallel_title,
        obj.subtitle,
        obj.title_remainder,
        obj.volume_number,
        obj.part_number,
        obj.part_title,
        obj.responsibility_statement,
        obj.edition_statement,
        obj.additional_edition_statement,
        obj.publication_place,
        obj.publisher,
        obj.publication_date,
        obj.inferred_year,
        obj.manufacture_place,
        obj.manufacturer,
        obj.manufacture_date,
        obj.copyright_date,
        obj.extent,
        obj.illustrations,
        obj.dimensions,
        obj.accompanying_material,
        obj.circulation,
        obj.series_statement,
        obj.notes,
        obj.bibliography_note,
        obj.index_note,
        obj.contents_note,
        obj.isbn,
        obj.issn,
        obj.doi,
        obj.url,
        obj.access_date.isoformat() if obj.access_date else None,
        obj.content_type,
        obj.media_type,
        obj.carrier_type,
        obj.raw_publication_details,
        obj.raw_host_title,
        obj.public_review,
        obj.data_source,
        obj.first_seen_at.isoformat() if obj.first_seen_at else None,
        obj.updated_at.isoformat() if obj.updated_at else None,
        obj.description_status,
    )


def insert_source_authors(conn):
    rows = [
        (
            obj.source_id,
            obj.author_id,
            obj.sort_order,
            obj.role,
            obj.source_text,
            obj.name_as_printed,
            int(obj.include_in_responsibility),
            int(obj.is_primary_heading),
            obj.created_at.isoformat() if obj.created_at else None,
            obj.updated_at.isoformat() if obj.updated_at else None,
        )
        for obj in SourceAuthor.objects.select_related("source", "author").order_by("source_id", "sort_order", "id")
    ]
    conn.executemany(
        """
        INSERT INTO source_authors (
            source_id, author_id, sort_order, role, source_text,
            name_as_printed, include_in_responsibility, is_primary_heading,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def insert_source_tags(conn):
    rows = [
        (
            obj.source_id,
            obj.tag_id,
            obj.sort_order,
            obj.source_text,
            obj.created_at.isoformat() if obj.created_at else None,
            obj.updated_at.isoformat() if obj.updated_at else None,
        )
        for obj in SourceTag.objects.select_related("source", "tag").order_by("source_id", "sort_order", "id")
    ]
    conn.executemany("INSERT INTO source_tags (source_id, tag_id, sort_order, source_text, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)", rows)


def insert_periodicals(conn):
    rows = [
        (
            obj.periodical_id,
            obj.source_django_id,
            obj.legacy_journal_id,
            obj.title,
            obj.parallel_title,
            obj.title_remainder,
            obj.responsibility_statement,
            obj.place,
            obj.publisher,
            obj.issn,
            obj.periodicity,
            obj.numbering_start,
            obj.numbering_end,
            obj.start_year,
            obj.end_year,
            obj.title_history_note,
            obj.description,
        )
        for obj in Periodical.objects.select_related("legacy_journal").order_by("title", "periodical_id")
    ]
    conn.executemany("INSERT INTO periodicals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


def insert_issues(conn):
    rows = [
        (
            obj.issue_id,
            obj.source_django_id,
            obj.legacy_journal_issue_id,
            obj.legacy_container_work_id,
            obj.issue_type,
            obj.periodical_id,
            obj.source_id,
            obj.title,
            obj.parallel_title,
            obj.title_remainder,
            obj.responsibility_statement,
            obj.year,
            obj.publication_date,
            obj.issue_number,
            obj.volume,
            obj.part_number,
            obj.gross_number,
            obj.chronology,
            obj.enumeration,
            obj.publication_place,
            obj.publisher,
            obj.publication_details,
            obj.issn,
            obj.isbn,
            obj.notes,
        )
        for obj in Issue.objects.select_related("legacy_journal_issue", "legacy_container_work", "periodical", "source").order_by("periodical_id", "year", "issue_number", "title", "issue_id")
    ]
    conn.executemany("INSERT INTO issues VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


def insert_article_placements(conn):
    rows = [
        (
            obj.placement_id,
            obj.source_django_id,
            obj.legacy_article_id,
            obj.source_id,
            obj.issue_id,
            obj.pages_raw,
            obj.page_start,
            obj.page_end,
            obj.location_note,
            obj.placement_note,
            obj.created_at.isoformat() if obj.created_at else None,
            obj.updated_at.isoformat() if obj.updated_at else None,
        )
        for obj in ArticlePlacement.objects.select_related("legacy_article", "source", "issue").order_by("issue_id", "page_start", "source_id")
    ]
    conn.executemany("INSERT INTO article_placements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


def insert_source_groups(conn):
    rows = [
        (obj.group_id, obj.source_django_id, obj.legacy_work_group_id, obj.title, obj.group_type, obj.note)
        for obj in SourceGroup.objects.select_related("legacy_work_group").order_by("title", "group_id")
    ]
    conn.executemany("INSERT INTO source_groups VALUES (?, ?, ?, ?, ?, ?)", rows)


def insert_source_group_items(conn):
    rows = [
        (
            obj.group_id,
            obj.source_id,
            obj.sort_order,
            obj.created_at.isoformat() if obj.created_at else None,
            obj.updated_at.isoformat() if obj.updated_at else None,
        )
        for obj in SourceGroupItem.objects.select_related("group", "source").order_by("group_id", "sort_order", "source_id")
    ]
    conn.executemany("INSERT INTO source_group_items (group_id, source_id, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?)", rows)


def create_indexes(conn):
    indexes = [
        "CREATE INDEX idx_sources_source_type ON sources(source_type)",
        "CREATE INDEX idx_sources_source_number ON sources(source_number)",
        "CREATE INDEX idx_sources_legacy_work_id ON sources(legacy_work_id)",
        "CREATE INDEX idx_source_authors_source_id ON source_authors(source_id)",
        "CREATE INDEX idx_source_authors_author_id ON source_authors(author_id)",
        "CREATE INDEX idx_source_tags_source_id ON source_tags(source_id)",
        "CREATE INDEX idx_periodicals_title ON periodicals(title)",
        "CREATE INDEX idx_issues_periodical_id ON issues(periodical_id)",
        "CREATE INDEX idx_issues_source_id ON issues(source_id)",
        "CREATE INDEX idx_article_placements_source_id ON article_placements(source_id)",
        "CREATE INDEX idx_article_placements_issue_id ON article_placements(issue_id)",
        "CREATE INDEX idx_source_group_items_group_id ON source_group_items(group_id)",
        "CREATE INDEX idx_source_group_items_source_id ON source_group_items(source_id)",
    ]
    for statement in indexes:
        conn.execute(statement)


def build_contract(sqlite_path):
    tables = {
        table: table_columns(table)
        for table in [
            "languages",
            "sections",
            "tags",
            "authors",
            "sources",
            "source_authors",
            "source_tags",
            "periodicals",
            "issues",
            "article_placements",
            "source_groups",
            "source_group_items",
        ]
    }
    return {
        "contract_name": "bibliobon_site_data",
        "contract_version": CONTRACT_VERSION,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "generated_at": timezone.now().isoformat(),
        "artifact": {
            "path": "data/bibliobon.sqlite",
            "sha256": sha256_file(sqlite_path),
            "bytes": sqlite_path.stat().st_size,
        },
        "tables": tables,
        "counts": artifact_counts(),
        "rules": [
            "sources.source_id is the stable source key.",
            "periodicals are continuing serial identities and are not citeable sources by themselves.",
            "issues are concrete containers; issues.source_id points to the citeable source when the container has its own record.",
            "article_placements links article sources to issue containers.",
            "legacy_* columns are compatibility references and must not be used as canonical IDs.",
        ],
    }


def table_columns(table):
    columns_by_table = {
        "languages": ["language_id", "source_django_id", "code", "title", "description", "sort_order"],
        "sections": ["section_id", "source_django_id", "source_code", "parent_id", "title", "description", "note", "sort_order"],
        "tags": ["tag_id", "source_django_id", "title", "tag_type", "parent_id", "description", "sort_order"],
        "authors": ["author_id", "source_django_id", "display_name", "heading_name", "sort_name", "aliases", "person_dates", "authority_note", "note"],
        "sources": [
            "source_id",
            "source_django_id",
            "legacy_work_id",
            "source_sequence",
            "source_number",
            "source_page_marker",
            "source_type",
            "section_id",
            "language_id",
            "raw_author_string",
            "title",
            "parallel_title",
            "subtitle",
            "title_remainder",
            "volume_number",
            "part_number",
            "part_title",
            "responsibility_statement",
            "edition_statement",
            "additional_edition_statement",
            "publication_place",
            "publisher",
            "publication_date",
            "inferred_year",
            "manufacture_place",
            "manufacturer",
            "manufacture_date",
            "copyright_date",
            "extent",
            "illustrations",
            "dimensions",
            "accompanying_material",
            "circulation",
            "series_statement",
            "notes",
            "bibliography_note",
            "index_note",
            "contents_note",
            "isbn",
            "issn",
            "doi",
            "url",
            "access_date",
            "content_type",
            "media_type",
            "carrier_type",
            "raw_publication_details",
            "raw_host_title",
            "public_review",
            "data_source",
            "first_seen_at",
            "updated_at",
            "description_status",
        ],
        "source_authors": ["id", "source_id", "author_id", "sort_order", "role", "source_text", "name_as_printed", "include_in_responsibility", "is_primary_heading", "created_at", "updated_at"],
        "source_tags": ["id", "source_id", "tag_id", "sort_order", "source_text", "created_at", "updated_at"],
        "periodicals": ["periodical_id", "source_django_id", "legacy_journal_id", "title", "parallel_title", "title_remainder", "responsibility_statement", "place", "publisher", "issn", "periodicity", "numbering_start", "numbering_end", "start_year", "end_year", "title_history_note", "description"],
        "issues": ["issue_id", "source_django_id", "legacy_journal_issue_id", "legacy_container_work_id", "issue_type", "periodical_id", "source_id", "title", "parallel_title", "title_remainder", "responsibility_statement", "year", "publication_date", "issue_number", "volume", "part_number", "gross_number", "chronology", "enumeration", "publication_place", "publisher", "raw_publication_details", "issn", "isbn", "notes"],
        "article_placements": ["placement_id", "source_django_id", "legacy_article_id", "source_id", "issue_id", "pages_raw", "page_start", "page_end", "location_note", "placement_note", "created_at", "updated_at"],
        "source_groups": ["group_id", "source_django_id", "legacy_work_group_id", "title", "group_type", "note"],
        "source_group_items": ["id", "group_id", "source_id", "sort_order", "created_at", "updated_at"],
    }
    return columns_by_table[table]


def artifact_counts():
    return {
        "languages": Language.objects.count(),
        "sections": Section.objects.count(),
        "tags": Tag.objects.count(),
        "authors": Author.objects.count(),
        "sources": Source.objects.count(),
        "source_authors": SourceAuthor.objects.count(),
        "source_tags": SourceTag.objects.count(),
        "periodicals": Periodical.objects.count(),
        "issues": Issue.objects.count(),
        "article_placements": ArticlePlacement.objects.count(),
        "source_groups": SourceGroup.objects.count(),
        "source_group_items": SourceGroupItem.objects.count(),
    }


def build_manifest(sqlite_path, contract_path, contract, backups):
    return {
        "generated_at": timezone.now().isoformat(),
        "generator": "editor.sources.export_site_artifact",
        "contract_version": contract["contract_version"],
        "artifact_schema_version": contract["artifact_schema_version"],
        "files": {
            "bibliobon.sqlite": {
                "path": "data/bibliobon.sqlite",
                "bytes": sqlite_path.stat().st_size,
                "sha256": sha256_file(sqlite_path),
            },
            "site_contract.json": {
                "path": "data/site_contract.json",
                "bytes": contract_path.stat().st_size,
                "sha256": sha256_file(contract_path),
            },
        },
        "counts": contract["counts"],
        "backups": backups,
    }


def backup_existing(path):
    if not path.exists():
        return None
    timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{path.stem}.before-site-artifact-export.{timestamp}{path.suffix}"
    copy2(path, backup_path)
    return backup_path


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
