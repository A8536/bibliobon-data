#!/usr/bin/env python3
"""Bootstrap bibliobon-data from the current Django site SQLite database."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = (
    "/Users/oleg/Projects/websites/bibliobon-catalog/app/db.sqlite3"
)


ENTITY_TABLES = {
    "languages": "catalog_language",
    "sections": "catalog_section",
    "tags": "catalog_tag",
    "authors": "catalog_author",
    "journals": "catalog_journal",
    "journal_issues": "catalog_journalissue",
    "works": "catalog_work",
    "books": "catalog_book",
    "articles": "catalog_article",
    "work_groups": "catalog_workgroup",
    "collections": "catalog_collection",
}

RELATION_TABLES = {
    "work_authors": "catalog_workauthor",
    "work_sections": "catalog_worksection",
    "work_tags": "catalog_worktag",
    "work_group_items": "catalog_workgroupitem",
}

STABLE_PREFIXES = {
    "language": "language",
    "section": "section",
    "tag": "tag",
    "author": "author",
    "journal": "journal",
    "journal_issue": "journal-issue",
    "work": "work",
    "book": "book",
    "article": "article",
    "group": "group",
    "collection": "collection",
}


def stable_id(kind: str, source_django_id: int | None) -> str | None:
    if source_django_id is None:
        return None
    return f"{STABLE_PREFIXES[kind]}-{source_django_id:06d}"


def language_stable_id(code: str | None, source_django_id: int | None) -> str | None:
    if code:
        slug = re.sub(r"[^a-z0-9]+", "-", code.lower()).strip("-")
        if slug:
            return f"language-{slug}"
    return stable_id("language", source_django_id)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_all(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def rename_source_id(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["source_django_id"] = row.pop("id")
    return row


def normalize_author_name(value: str) -> str:
    value = value.lower().replace("ё", "е")
    value = re.sub(r"[\W_]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def normalize_publication_details(value: str | None) -> str:
    value = (value or "").lower().replace("ё", "е")
    value = re.sub(r"\s+", " ", value)
    return value.strip(" .;:,")


def build_maps(rows_by_table: dict[str, list[dict[str, Any]]]) -> dict[str, dict[int, str]]:
    language_map = {
        row["id"]: language_stable_id(row.get("code"), row["id"])
        for row in rows_by_table["languages"]
    }
    return {
        "language": language_map,
        "section": {
            row["id"]: stable_id("section", row["id"])
            for row in rows_by_table["sections"]
        },
        "tag": {row["id"]: stable_id("tag", row["id"]) for row in rows_by_table["tags"]},
        "author": {
            row["id"]: stable_id("author", row["id"])
            for row in rows_by_table["authors"]
        },
        "journal": {
            row["id"]: stable_id("journal", row["id"])
            for row in rows_by_table["journals"]
        },
        "journal_issue": {
            row["id"]: stable_id("journal_issue", row["id"])
            for row in rows_by_table["journal_issues"]
        },
        "work": {
            row["id"]: stable_id("work", row["id"]) for row in rows_by_table["works"]
        },
        "book": {
            row["id"]: stable_id("book", row["id"]) for row in rows_by_table["books"]
        },
        "article": {
            row["id"]: stable_id("article", row["id"])
            for row in rows_by_table["articles"]
        },
        "group": {
            row["id"]: stable_id("group", row["id"])
            for row in rows_by_table["work_groups"]
        },
        "collection": {
            row["id"]: stable_id("collection", row["id"])
            for row in rows_by_table["collections"]
        },
    }


def add_fk(
    row: dict[str, Any],
    column: str,
    source_column: str,
    stable_column: str,
    id_map: dict[int, str],
) -> None:
    source_id = row.pop(column)
    row[source_column] = source_id
    row[stable_column] = id_map.get(source_id)


def transform_rows(
    rows_by_table: dict[str, list[dict[str, Any]]],
    id_maps: dict[str, dict[int, str]],
) -> dict[str, list[dict[str, Any]]]:
    transformed: dict[str, list[dict[str, Any]]] = {}

    transformed["languages"] = []
    for source in rows_by_table["languages"]:
        row = rename_source_id(source)
        row["language_id"] = language_stable_id(row.get("code"), row["source_django_id"])
        transformed["languages"].append(row)

    transformed["sections"] = []
    for source in rows_by_table["sections"]:
        row = rename_source_id(source)
        row["section_id"] = id_maps["section"][row["source_django_id"]]
        add_fk(
            row,
            "parent_id",
            "parent_source_django_id",
            "parent_section_id",
            id_maps["section"],
        )
        transformed["sections"].append(row)

    transformed["tags"] = []
    for source in rows_by_table["tags"]:
        row = rename_source_id(source)
        row["tag_id"] = id_maps["tag"][row["source_django_id"]]
        add_fk(
            row,
            "parent_id",
            "parent_source_django_id",
            "parent_tag_id",
            id_maps["tag"],
        )
        transformed["tags"].append(row)

    transformed["authors"] = []
    for source in rows_by_table["authors"]:
        row = rename_source_id(source)
        row["author_id"] = id_maps["author"][row["source_django_id"]]
        transformed["authors"].append(row)

    transformed["journals"] = []
    for source in rows_by_table["journals"]:
        row = rename_source_id(source)
        row["journal_id"] = id_maps["journal"][row["source_django_id"]]
        transformed["journals"].append(row)

    transformed["journal_issues"] = []
    for source in rows_by_table["journal_issues"]:
        row = rename_source_id(source)
        row["journal_issue_id"] = id_maps["journal_issue"][row["source_django_id"]]
        add_fk(
            row,
            "journal_id",
            "journal_source_django_id",
            "journal_id",
            id_maps["journal"],
        )
        transformed["journal_issues"].append(row)

    transformed["works"] = []
    language_codes = {row["id"]: row.get("code") for row in rows_by_table["languages"]}
    for source in rows_by_table["works"]:
        row = rename_source_id(source)
        row["work_id"] = id_maps["work"][row["source_django_id"]]
        add_fk(
            row,
            "source_section_id",
            "source_section_source_django_id",
            "source_section_id",
            id_maps["section"],
        )
        source_language_id = row.pop("language_id")
        row["language_source_django_id"] = source_language_id
        row["language_id"] = id_maps["language"].get(source_language_id)
        row["language_code"] = language_codes.get(source_language_id)
        transformed["works"].append(row)

    transformed["books"] = []
    for source in rows_by_table["books"]:
        row = rename_source_id(source)
        row["book_id"] = id_maps["book"][row["source_django_id"]]
        add_fk(
            row,
            "work_id",
            "work_source_django_id",
            "work_id",
            id_maps["work"],
        )
        transformed["books"].append(row)

    transformed["collections"] = []
    for source in rows_by_table["collections"]:
        row = rename_source_id(source)
        row["collection_id"] = id_maps["collection"][row["source_django_id"]]
        add_fk(
            row,
            "parent_work_id",
            "parent_work_source_django_id",
            "parent_work_id",
            id_maps["work"],
        )
        transformed["collections"].append(row)

    transformed["articles"] = []
    for source in rows_by_table["articles"]:
        row = rename_source_id(source)
        row["article_id"] = id_maps["article"][row["source_django_id"]]
        add_fk(
            row,
            "work_id",
            "work_source_django_id",
            "work_id",
            id_maps["work"],
        )
        add_fk(
            row,
            "container_work_id",
            "container_work_source_django_id",
            "container_work_id",
            id_maps["work"],
        )
        add_fk(
            row,
            "collection_id",
            "collection_source_django_id",
            "collection_id",
            id_maps["collection"],
        )
        add_fk(
            row,
            "journal_issue_id",
            "journal_issue_source_django_id",
            "journal_issue_id",
            id_maps["journal_issue"],
        )
        transformed["articles"].append(row)

    transformed["work_groups"] = []
    for source in rows_by_table["work_groups"]:
        row = rename_source_id(source)
        row["group_id"] = id_maps["group"][row["source_django_id"]]
        transformed["work_groups"].append(row)

    transformed["work_authors"] = []
    for source in rows_by_table["work_authors"]:
        row = rename_source_id(source)
        add_fk(
            row,
            "work_id",
            "work_source_django_id",
            "work_id",
            id_maps["work"],
        )
        add_fk(
            row,
            "author_id",
            "author_source_django_id",
            "author_id",
            id_maps["author"],
        )
        transformed["work_authors"].append(row)

    transformed["work_sections"] = []
    for source in rows_by_table["work_sections"]:
        row = rename_source_id(source)
        add_fk(
            row,
            "work_id",
            "work_source_django_id",
            "work_id",
            id_maps["work"],
        )
        add_fk(
            row,
            "section_id",
            "section_source_django_id",
            "section_id",
            id_maps["section"],
        )
        transformed["work_sections"].append(row)

    transformed["work_tags"] = []
    for source in rows_by_table["work_tags"]:
        row = rename_source_id(source)
        add_fk(
            row,
            "work_id",
            "work_source_django_id",
            "work_id",
            id_maps["work"],
        )
        add_fk(row, "tag_id", "tag_source_django_id", "tag_id", id_maps["tag"])
        transformed["work_tags"].append(row)

    transformed["work_group_items"] = []
    for source in rows_by_table["work_group_items"]:
        row = rename_source_id(source)
        add_fk(
            row,
            "group_id",
            "group_source_django_id",
            "group_id",
            id_maps["group"],
        )
        add_fk(
            row,
            "work_id",
            "work_source_django_id",
            "work_id",
            id_maps["work"],
        )
        transformed["work_group_items"].append(row)

    return transformed


def rows_from_query(conn: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(query)]


def build_reports(
    conn: sqlite3.Connection,
    transformed: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    counts = {
        name: len(rows)
        for name, rows in sorted(transformed.items())
    }

    article_container_issues = {
        "articles_without_container": rows_from_query(
            conn,
            """
            SELECT a.id AS article_source_django_id, a.work_id AS work_source_django_id,
                   w.title, w.host_title
            FROM catalog_article a
            JOIN catalog_work w ON w.id = a.work_id
            WHERE a.journal_issue_id IS NULL
              AND a.container_work_id IS NULL
              AND a.collection_id IS NULL
            ORDER BY a.id
            """,
        ),
        "articles_with_journal_issue_and_container_work": rows_from_query(
            conn,
            """
            SELECT a.id AS article_source_django_id, a.work_id AS work_source_django_id,
                   w.title, a.journal_issue_id, a.container_work_id
            FROM catalog_article a
            JOIN catalog_work w ON w.id = a.work_id
            WHERE a.journal_issue_id IS NOT NULL
              AND a.container_work_id IS NOT NULL
            ORDER BY a.id
            """,
        ),
        "articles_with_host_title_without_normalized_container": rows_from_query(
            conn,
            """
            SELECT a.id AS article_source_django_id, a.work_id AS work_source_django_id,
                   w.title, w.host_title, a.collection_id
            FROM catalog_article a
            JOIN catalog_work w ON w.id = a.work_id
            WHERE TRIM(w.host_title) != ''
              AND a.journal_issue_id IS NULL
              AND a.container_work_id IS NULL
            ORDER BY a.id
            """,
        ),
    }

    empty_containers = {
        "empty_journals": rows_from_query(
            conn,
            """
            SELECT j.id AS journal_source_django_id, j.title
            FROM catalog_journal j
            LEFT JOIN catalog_journalissue i ON i.journal_id = j.id
            WHERE i.id IS NULL
            ORDER BY j.title
            """,
        ),
        "empty_journal_issues": rows_from_query(
            conn,
            """
            SELECT i.id AS journal_issue_source_django_id, i.journal_id AS journal_source_django_id,
                   j.title AS journal_title, i.year, i.issue_number, i.volume
            FROM catalog_journalissue i
            JOIN catalog_journal j ON j.id = i.journal_id
            LEFT JOIN catalog_article a ON a.journal_issue_id = i.id
            WHERE a.id IS NULL
            ORDER BY j.title, i.year, i.issue_number, i.volume
            """,
        ),
    }

    duplicate_journal_issues = rows_from_query(
        conn,
        """
        SELECT journal_id AS journal_source_django_id, year, issue_number, volume,
               COUNT(*) AS duplicate_count,
               GROUP_CONCAT(id) AS issue_source_django_ids
        FROM catalog_journalissue
        GROUP BY journal_id, year, issue_number, volume
        HAVING COUNT(*) > 1
        ORDER BY duplicate_count DESC, journal_id
        """,
    )

    authors_by_normalized_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for author in transformed["authors"]:
        key = normalize_author_name(author["display_name"])
        if key:
            authors_by_normalized_name[key].append(
                {
                    "author_id": author["author_id"],
                    "source_django_id": author["source_django_id"],
                    "display_name": author["display_name"],
                    "sort_name": author["sort_name"],
                }
            )
    duplicate_authors = [
        {"normalized_name": key, "authors": authors}
        for key, authors in sorted(authors_by_normalized_name.items())
        if len(authors) > 1
    ]

    legacy_collection_usage = rows_from_query(
        conn,
        """
        SELECT a.id AS article_source_django_id, a.work_id AS work_source_django_id,
               w.title AS article_title, a.collection_id AS collection_source_django_id,
               c.title AS collection_title
        FROM catalog_article a
        JOIN catalog_work w ON w.id = a.work_id
        JOIN catalog_collection c ON c.id = a.collection_id
        ORDER BY c.title, w.title
        """,
    )

    publication_details_overlap = []
    work_by_source_id = {row["source_django_id"]: row for row in transformed["works"]}
    collection_by_source_id = {
        row["source_django_id"]: row for row in transformed["collections"]
    }
    issue_by_source_id = {
        row["source_django_id"]: row for row in transformed["journal_issues"]
    }
    for article in transformed["articles"]:
        article_work = work_by_source_id.get(article["work_source_django_id"])
        if not article_work:
            continue
        article_details = normalize_publication_details(
            article_work.get("publication_details")
        )
        if not article_details:
            continue
        candidates = []
        if article["container_work_source_django_id"]:
            container = work_by_source_id.get(article["container_work_source_django_id"])
            candidates.append(("container_work", container))
        if article["collection_source_django_id"]:
            collection = collection_by_source_id.get(article["collection_source_django_id"])
            candidates.append(("collection", collection))
        if article["journal_issue_source_django_id"]:
            issue = issue_by_source_id.get(article["journal_issue_source_django_id"])
            candidates.append(("journal_issue", issue))
        for container_type, container in candidates:
            if not container:
                continue
            container_details = normalize_publication_details(
                container.get("publication_details")
            )
            if not container_details:
                continue
            if (
                article_details == container_details
                or article_details in container_details
                or container_details in article_details
            ):
                publication_details_overlap.append(
                    {
                        "article_id": article["article_id"],
                        "article_source_django_id": article["source_django_id"],
                        "work_id": article["work_id"],
                        "work_source_django_id": article["work_source_django_id"],
                        "container_type": container_type,
                        "container_source_django_id": container["source_django_id"],
                        "article_publication_details": article_work[
                            "publication_details"
                        ],
                        "container_publication_details": container[
                            "publication_details"
                        ],
                    }
                )

    return {
        "counts": counts,
        "article_container_issues": article_container_issues,
        "empty_containers": empty_containers,
        "duplicate_journal_issues": duplicate_journal_issues,
        "duplicate_author_names": duplicate_authors,
        "legacy_collection_usage": legacy_collection_usage,
        "publication_details_overlap": publication_details_overlap,
    }


def write_summary(report_dir: Path, manifest: dict[str, Any], reports: dict[str, Any]) -> None:
    counts = reports["counts"]
    article_issues = reports["article_container_issues"]
    empty = reports["empty_containers"]
    lines = [
        "# Bootstrap Summary",
        "",
        f"Generated at: `{manifest['generated_at']}`",
        f"Source database: `{manifest['source']['path']}`",
        f"Source SHA-256: `{manifest['source']['sha256']}`",
        "",
        "## Counts",
        "",
    ]
    for name, count in counts.items():
        lines.append(f"- `{name}`: {count}")
    lines.extend(
        [
            "",
            "## Diagnostics",
            "",
            "- `articles_without_container`: "
            f"{len(article_issues['articles_without_container'])}",
            "- `articles_with_journal_issue_and_container_work`: "
            f"{len(article_issues['articles_with_journal_issue_and_container_work'])}",
            "- `articles_with_host_title_without_normalized_container`: "
            f"{len(article_issues['articles_with_host_title_without_normalized_container'])}",
            f"- `empty_journals`: {len(empty['empty_journals'])}",
            f"- `empty_journal_issues`: {len(empty['empty_journal_issues'])}",
            f"- `duplicate_journal_issues`: {len(reports['duplicate_journal_issues'])}",
            f"- `duplicate_author_names`: {len(reports['duplicate_author_names'])}",
            f"- `legacy_collection_usage`: {len(reports['legacy_collection_usage'])}",
            "- `publication_details_overlap`: "
            f"{len(reports['publication_details_overlap'])}",
            "",
        ]
    )
    (report_dir / "bootstrap_summary.md").write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export current Bibliobon Django SQLite data into JSONL files."
    )
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="Path to db.sqlite3")
    parser.add_argument("--data-dir", default=PROJECT_ROOT / "data", type=Path)
    parser.add_argument("--reports-dir", default=PROJECT_ROOT / "reports", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = Path(args.source).expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"Source database does not exist: {source}")

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data_dir = args.data_dir.resolve()
    reports_dir = args.reports_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    with connect(source) as conn:
        rows_by_table = {
            name: fetch_all(conn, table)
            for name, table in {**ENTITY_TABLES, **RELATION_TABLES}.items()
        }
        id_maps = build_maps(rows_by_table)
        transformed = transform_rows(rows_by_table, id_maps)
        reports = build_reports(conn, transformed)

    data_files = {}
    for name, rows in sorted(transformed.items()):
        path = data_dir / f"{name}.jsonl"
        write_jsonl(path, rows)
        data_files[name] = {
            "path": str(path.relative_to(PROJECT_ROOT)),
            "row_count": len(rows),
        }

    report_files = {
        "counts": "reports/bootstrap_counts.json",
        "article_container_issues": "reports/article_container_issues.json",
        "empty_containers": "reports/empty_containers.json",
        "duplicate_journal_issues": "reports/duplicate_journal_issues.json",
        "duplicate_author_names": "reports/duplicate_author_names.json",
        "legacy_collection_usage": "reports/legacy_collection_usage.json",
        "publication_details_overlap": "reports/publication_details_overlap.json",
        "summary": "reports/bootstrap_summary.md",
    }
    write_json(reports_dir / "bootstrap_counts.json", reports["counts"])
    write_json(
        reports_dir / "article_container_issues.json",
        reports["article_container_issues"],
    )
    write_json(reports_dir / "empty_containers.json", reports["empty_containers"])
    write_json(
        reports_dir / "duplicate_journal_issues.json",
        reports["duplicate_journal_issues"],
    )
    write_json(
        reports_dir / "duplicate_author_names.json",
        reports["duplicate_author_names"],
    )
    write_json(
        reports_dir / "legacy_collection_usage.json",
        reports["legacy_collection_usage"],
    )
    write_json(
        reports_dir / "publication_details_overlap.json",
        reports["publication_details_overlap"],
    )

    manifest = {
        "producer": "bibliobon-data",
        "generated_at": generated_at,
        "source": {
            "path": str(source),
            "sha256": sha256_file(source),
        },
        "stable_id_scheme": {
            "description": (
                "Initial bootstrap IDs are deterministic compatibility IDs derived "
                "from source Django primary keys. They can be replaced by curated "
                "stable IDs later through an explicit migration."
            ),
            "patterns": {
                "work_id": "work-000001",
                "author_id": "author-000001",
                "section_id": "section-000001",
                "tag_id": "tag-000001",
                "journal_id": "journal-000001",
                "journal_issue_id": "journal-issue-000001",
                "group_id": "group-000001",
            },
        },
        "data_files": data_files,
        "report_files": report_files,
    }
    write_json(data_dir / "build_manifest.json", manifest)
    write_summary(reports_dir, manifest, reports)

    print(f"Wrote {len(data_files)} JSONL exports to {data_dir}")
    print(f"Wrote diagnostics to {reports_dir}")
    print(f"Wrote manifest to {data_dir / 'build_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
