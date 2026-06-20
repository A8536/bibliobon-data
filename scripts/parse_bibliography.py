#!/usr/bin/env python3
"""Create staging parser runs from raw Bibliobon bibliography records."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PARSER_VERSION = "bibliobon-parser-0.1"

DEFAULT_RUNS_DIR = PROJECT_ROOT / "data" / "parser_runs"
DEFAULT_NORMALIZED_DIR = PROJECT_ROOT / "source" / "normalized_text"


@dataclass
class WarningItem:
    code: str
    message: str
    fragment: str = ""
    severity: str = "warning"


@dataclass
class RawRecord:
    raw_record_id: str
    source_input_path: str
    source_record_index: int
    source_line_start: int | None
    source_line_end: int | None
    raw_text: str
    normalized_text: str
    source_sha256: str
    ai_markup: dict[str, Any] | None = None


@dataclass
class ParseResult:
    candidate: dict[str, Any]
    warnings: list[WarningItem] = field(default_factory=list)


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as fh:
        for line_no, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
            if not isinstance(row, dict):
                raise SystemExit(f"{path}:{line_no}: each JSONL row must be an object")
            row.setdefault("_line_no", line_no)
            rows.append(row)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def clean_record_text(value: str) -> str:
    value = value.replace("\ufeff", "")
    value = value.replace("\u00a0", " ")
    value = value.replace("–", "-").replace("—", "-")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\s*\n\s*", " ", value)
    return value.strip()


def normalize_compare_text(value: str | None) -> str:
    value = (value or "").lower().replace("ё", "е")
    value = value.replace("\u00ad", "")
    value = value.replace("_", " ")
    value = re.sub(r"[^\w\s]+", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_container_title(value: str | None) -> str:
    value = normalize_compare_text(value)
    value = re.sub(r"\b(журнал|газета|еженедельник|альманах|сборник|вестник)\b", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_for_id(value: str) -> str:
    value = value.lower().replace("ё", "е")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def split_txt_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    buffer: list[tuple[int, str]] = []

    def flush() -> None:
        nonlocal buffer
        text = clean_record_text("\n".join(line for _, line in buffer))
        if text:
            records.append(
                {
                    "raw_text": text,
                    "source_line_start": buffer[0][0],
                    "source_line_end": buffer[-1][0],
                }
            )
        buffer = []

    with path.open("r", encoding="utf-8-sig") as fh:
        for line_no, line in enumerate(fh, start=1):
            stripped = line.rstrip("\n")
            if not stripped.strip():
                flush()
                continue
            match = re.match(r"^\s*(?:\d+[.)]|\[\d+\])\s+(.+)$", stripped)
            if match and buffer:
                flush()
                stripped = match.group(1)
            elif match:
                stripped = match.group(1)
            buffer.append((line_no, stripped))
    flush()
    return records


def source_value(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None


def load_raw_records(input_path: Path, batch_id: str | None) -> list[RawRecord]:
    source_hash = sha256_file(input_path)
    suffix = input_path.suffix.lower()
    if suffix == ".jsonl":
        source_rows = []
        for row in read_jsonl(input_path):
            raw_text = source_value(row, "raw_text", "normalized_text", "text", "record")
            if not isinstance(raw_text, str) or not raw_text.strip():
                continue
            source_rows.append(
                {
                    "raw_text": raw_text,
                    "source_line_start": row.get("source_line_start") or row.get("_line_no"),
                    "source_line_end": row.get("source_line_end") or row.get("_line_no"),
                    "source_record_id": row.get("raw_record_id") or row.get("record_id"),
                    "ai_markup": extract_ai_markup(row),
                }
            )
    elif suffix == ".txt":
        source_rows = split_txt_records(input_path)
    else:
        raise SystemExit("Input must be .txt or .jsonl")

    records: list[RawRecord] = []
    for index, row in enumerate(source_rows, start=1):
        raw_text = str(row["raw_text"])
        normalized = clean_record_text(raw_text)
        stable_base = f"{batch_id or input_path.stem}:{index}:{normalize_for_id(normalized)}"
        raw_record_id = row.get("source_record_id") or f"raw-{sha256_text(stable_base)[:16]}"
        records.append(
            RawRecord(
                raw_record_id=raw_record_id,
                source_input_path=str(input_path),
                source_record_index=index,
                source_line_start=as_optional_int(row.get("source_line_start")),
                source_line_end=as_optional_int(row.get("source_line_end")),
                raw_text=raw_text,
                normalized_text=normalized,
                source_sha256=source_hash,
                ai_markup=row.get("ai_markup"),
            )
        )
    return records


AI_MARKUP_FIELDS = {
    "record_type",
    "authors",
    "title",
    "subtitle",
    "title_remainder",
    "responsibility_statement",
    "publication_place",
    "publisher",
    "publication_date",
    "extent",
    "notes",
    "warnings",
    "confidence",
    "periodical_title",
    "issue_year",
    "issue_number",
    "article_pages",
}


def extract_ai_markup(row: dict[str, Any]) -> dict[str, Any] | None:
    explicit = row.get("ai_markup")
    if isinstance(explicit, dict):
        return explicit
    fields = {name: row.get(name) for name in AI_MARKUP_FIELDS if name in row}
    return fields or None


def as_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_first(pattern: str, text: str, flags: int = re.IGNORECASE) -> str | None:
    match = re.search(pattern, text, flags)
    if not match:
        return None
    return clean_record_text(match.group(1))


def extract_year(text: str) -> int | None:
    years = [int(value) for value in re.findall(r"(?<!\d)(1[5-9]\d{2}|20\d{2})(?!\d)", text)]
    if not years:
        return None
    return years[-1]


def extract_publication_date(text: str) -> str | None:
    date = extract_first(r"(?<!\d)((?:1[5-9]\d{2}|20\d{2})(?:\s*[-/]\s*(?:1[5-9]\d{2}|20\d{2}))?)(?!\d)", text)
    return date


def extract_identifiers(text: str) -> dict[str, str | None]:
    return {
        "isbn": extract_first(r"\bISBN\s+([0-9XХxх][0-9XХxх\- ]{8,})", text),
        "issn": extract_first(r"\bISSN\s+([0-9XХxх]{4}-?[0-9XХxх]{4})", text),
        "doi": extract_first(r"\bDOI[:\s]+([^\s.;]+(?:/[^\s.;]+)*)", text),
        "url": extract_first(r"\b(?:URL|Режим доступа)\s*[:.-]?\s*(https?://[^\s]+)", text),
        "access_date": extract_first(r"дата обращения\s*[:.-]\s*([0-9]{1,2}[.][0-9]{1,2}[.][0-9]{2,4})", text),
    }


def strip_identifier_text(text: str) -> str:
    text = re.sub(r"\bISBN\s+[0-9XХxх][0-9XХxх\- ]{8,}", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bISSN\s+[0-9XХxх]{4}-?[0-9XХxх]{4}", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bDOI[:\s]+[^\s.;]+(?:/[^\s.;]+)*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:URL|Режим доступа)\s*[:.-]?\s*https?://[^\s]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"дата обращения\s*[:.-]\s*[0-9]{1,2}[.][0-9]{1,2}[.][0-9]{2,4}", "", text, flags=re.IGNORECASE)
    return clean_record_text(text.strip(" .;-"))


def extract_braced_public_review(text: str) -> tuple[str, str | None]:
    reviews = [clean_record_text(match.group(1).strip(" .;")) for match in re.finditer(r"\{([^{}]+)\}", text)]
    cleaned = clean_record_text(re.sub(r"\s*\{[^{}]+\}", "", text).strip(" .;"))
    return cleaned, "; ".join(review for review in reviews if review) or None


def split_responsibility(text: str) -> tuple[str, str | None]:
    if " / " not in text:
        return text, None
    title, responsibility = text.split(" / ", 1)
    return clean_record_text(title), clean_record_text(responsibility.strip(" ."))


def parse_author_title(text: str) -> tuple[str | None, str, list[dict[str, Any]], list[WarningItem]]:
    warnings: list[WarningItem] = []
    authors: list[dict[str, Any]] = []
    name = r"(?:[А-ЯЁA-Z][а-яёa-z-]+,?\s+(?:[А-ЯA-Z]\.\s*){1,2}|(?:[А-ЯA-Z]\.){2})"
    match = re.match(
        rf"^((?:{name})(?:\s*(?:;|,|\s+и\s+)\s*(?:{name}))*)\s+(.+)$",
        text,
    )
    if match and looks_like_author_segment(match.group(1)):
        raw_authors = clean_record_text(match.group(1).strip(" ,;"))
        title_text = clean_record_text(match.group(2))
        authors = parse_authors(raw_authors)
        if not authors:
            warnings.append(
                WarningItem("author_parse_failed", "Author-like segment was not split into names.", raw_authors)
            )
        return raw_authors, title_text, authors, warnings
    return None, text, authors, warnings


def looks_like_author_segment(value: str) -> bool:
    value = value.strip()
    if not value or len(value) > 180:
        return False
    if re.search(r"\b(ред|сост|пер|под ред|коллектив|министерство|государственная дума)\b", value, re.IGNORECASE):
        return False
    if re.search(r"[А-ЯЁA-Z][а-яёa-z-]+,\s*[А-ЯA-Z]\.", value):
        return True
    if re.search(r"^[А-ЯЁA-Z][а-яёa-z-]+(?:\s+[А-ЯA-Z]\.){1,2}", value):
        return True
    if re.search(r"^(?:[А-ЯA-Z]\.){2}$", value):
        return True
    return False


def parse_authors(raw_authors: str) -> list[dict[str, Any]]:
    name_pattern = r"(?:[А-ЯЁA-Z][а-яёa-z-]+,?\s+(?:[А-ЯA-Z]\.\s*){1,2}|(?:[А-ЯA-Z]\.){2})"
    matches = [clean_record_text(match.group(0).strip(" ,;")) for match in re.finditer(name_pattern, raw_authors)]
    chunks = matches or re.split(r"\s*(?:;|\s+и\s+|&)\s*", raw_authors)
    authors: list[dict[str, Any]] = []
    for order, chunk in enumerate([clean_record_text(item) for item in chunks if item.strip()], start=1):
        chunk = clean_record_text(chunk.replace(", ", " "))
        authors.append(
            {
                "display_name": chunk,
                "heading_name": chunk,
                "role": "author",
                "sort_order": order,
                "source_text": chunk,
                "name_as_printed": chunk,
            }
        )
    return authors


def parse_title_fields(text: str) -> dict[str, str | None]:
    fields: dict[str, str | None] = {
        "title": None,
        "parallel_title": None,
        "subtitle": None,
        "title_remainder": None,
        "responsibility_statement": None,
    }
    text, responsibility = split_responsibility(text)
    fields["responsibility_statement"] = responsibility
    if " = " in text:
        text, parallel = text.split(" = ", 1)
        fields["parallel_title"] = clean_record_text(parallel.strip(" ."))
    if " : " in text:
        title, subtitle = text.split(" : ", 1)
        fields["title"] = clean_record_text(title.strip(" ."))
        fields["subtitle"] = clean_record_text(subtitle.strip(" ."))
    elif ". " in text:
        title, subtitle = text.split(". ", 1)
        if 2 <= len(title.split()) <= 4 and len(subtitle.split()) >= 2:
            fields["title"] = clean_record_text(title.strip(" ."))
            fields["subtitle"] = clean_record_text(subtitle.strip(" ."))
        else:
            fields["title"] = clean_record_text(text.strip(" ."))
    else:
        fields["title"] = clean_record_text(text.strip(" ."))
    return fields


def parse_page_bounds(pages_raw: str | None) -> tuple[int | None, int | None]:
    if not pages_raw:
        return None, None
    match = re.search(r"(\d+)\s*[-–—]\s*(\d+)", pages_raw)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.search(r"\b(\d+)\b", pages_raw)
    if match:
        page = int(match.group(1))
        return page, page
    return None, None


def clean_issue_number(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = clean_record_text(value).strip(" .;,")
    return cleaned or None


def extract_pages(text: str) -> tuple[str, str | None]:
    page_patterns = [
        r"(?:^|[.]\s*)(С\.\s*\[?[IVXLCDM\d]+(?:\s*[-–—]\s*\[?[IVXLCDM\d]+\]?)?(?:,\s*[IVXLCDM\d]+(?:\s*[-–—]\s*[IVXLCDM\d]+)?)*)\.?\s*$",
        r"(?:^|[.]\s*)(P\.\s*\d+(?:\s*[-–—]\s*\d+)?)\.?\s*$",
    ]
    for pattern in page_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            pages = clean_record_text(match.group(1))
            remainder = clean_record_text((text[: match.start()] + text[match.end() :]).strip(" .;"))
            return remainder, pages
    return text, None


def parse_container(text: str) -> tuple[dict[str, Any], dict[str, Any] | None, list[WarningItem]]:
    warnings: list[WarningItem] = []
    text = strip_identifier_text(text)
    text, pages_raw = extract_pages(text)
    page_start, page_end = parse_page_bounds(pages_raw)
    segments = [segment for segment in split_gost_segments(text) if segment]
    title_segment = segments[0] if segments else text
    publication_segment = first_segment(segments[1:], has_publication_statement)
    publication_date = extract_publication_date(text)

    issue: dict[str, Any] = {
        "title": None,
        "publication_date": publication_date,
        "year": extract_year(publication_date or "") or extract_year(text),
        "issue_number": clean_issue_number(extract_first(r"(?:№|N|No\.?)\s*([0-9А-ЯA-Zа-яa-z./-]+)", text)),
        "volume": extract_first(r"(?:Т\.|Том|Vol\.?)\s*([0-9А-ЯA-Zа-яa-z./-]+)", text),
        "part_number": extract_first(r"(?:Ч\.|Часть)\s*([0-9А-ЯA-Zа-яa-z./-]+)", text),
        "date_text": extract_first(r"([0-9]{1,2}\s+[а-яё]+\s+(?:1[5-9]\d{2}|20\d{2})\s*г?\.?)", text),
        "publication_place": None,
        "publisher": None,
        "raw_publication_details": text,
    }

    title = title_segment
    title = re.sub(r"\b(?:1[5-9]\d{2}|20\d{2})\b", "", title)
    title = re.sub(r"(?:№|N|No\.?)\s*[0-9А-ЯA-Zа-яa-z./-]+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"(?:Т\.|Том|Vol\.?)\s*[0-9А-ЯA-Zа-яa-z./-]+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"(?:Ч\.|Часть)\s*[0-9А-ЯA-Zа-яa-z./-]+", "", title, flags=re.IGNORECASE)
    title = clean_record_text(title.strip(" .,-"))

    container_kind = classify_container_kind(text, title)
    periodical: dict[str, Any] | None = None
    if container_kind == "periodical_issue":
        periodical = {"title": title or None, "issn": extract_identifiers(text).get("issn")}
        if not periodical["title"]:
            warnings.append(WarningItem("container_title_missing", "Periodical title was not recognized.", text))
    else:
        issue["title"] = title or None
        issue.update(parse_publication_statement(publication_segment or text))
        if not issue["title"]:
            warnings.append(WarningItem("container_title_missing", "Container title was not recognized.", text))

    placement = {
        "pages_raw": pages_raw,
        "page_start": page_start,
        "page_end": page_end,
        "location_note": None,
        "placement_note": None,
    }
    return {"container_kind": container_kind, "periodical": periodical, "issue": issue, "article_placement": placement}, periodical, warnings


def is_periodical_container(text: str) -> bool:
    return classify_container_kind(text) == "periodical_issue"


def classify_container_kind(text: str, title: str | None = None) -> str:
    haystack = normalize_compare_text(" ".join(part for part in [title or "", text] if part))
    if re.search(r"\b(сборник|материалы|труды|конференц|чтени[яй]|симпозиум|семинар)\b", haystack):
        return "collection_work"
    if re.search(r"\b(журнал|газета|вестник|бюллетень|альманах)\b", haystack):
        return "periodical_issue"
    if re.search(r"(?:^|\s)(№|N|No\.?)\s*\d+", text, re.IGNORECASE):
        return "periodical_issue"
    return "collection_work"


def parse_publication_statement(text: str) -> dict[str, str | None]:
    result = {"publication_place": None, "publisher": None}
    match = re.search(
        r"(.{1,120}?)\s*:\s*(.{1,160}?),\s*(?:ценз\.\s*)?(?:1[5-9]\d{2}|20\d{2})",
        text,
    )
    if match:
        result["publication_place"] = clean_record_text(match.group(1).strip(" .;"))
        result["publisher"] = clean_record_text(match.group(2).strip(" .;"))
    else:
        place_match = re.search(r"^(.{1,80}?),\s*(?:1[5-9]\d{2}|20\d{2})(?:\.|$)", text)
        if place_match:
            result["publication_place"] = clean_record_text(place_match.group(1).strip(" .;"))
        elif re.search(r"\b(?:Б\.?\s*м\.?|Б\.м\.)\b", text, re.IGNORECASE):
            result["publication_place"] = "Б.м."
    return result


def parse_standalone(text: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[WarningItem]]:
    warnings: list[WarningItem] = []
    identifiers = extract_identifiers(text)
    work_text, public_review = extract_braced_public_review(text)
    work_text = strip_identifier_text(work_text)
    raw_author_string, title_text, authors, author_warnings = parse_author_title(work_text)
    warnings.extend(author_warnings)

    segments = [segment for segment in split_gost_segments(title_text) if segment]
    main_segment = segments[0] if segments else title_text
    tail_segments = segments[1:]
    publication_segment = first_segment(tail_segments, has_publication_statement)
    physical_segment = first_segment(tail_segments, has_physical_description)
    embedded_publication = split_embedded_publication(main_segment)
    if embedded_publication:
        main_segment, embedded_segment = embedded_publication
        publication_segment = publication_segment or embedded_segment
    note_segments = [
        segment
        for segment in tail_segments
        if segment != publication_segment and segment != physical_segment
    ]

    publication = parse_publication_statement(publication_segment or work_text)
    publication_date = extract_publication_date(publication_segment or work_text)
    year = extract_year(work_text)
    extent = extract_first(
        r"((?:\[[0-9]+\]|[0-9])[\d\[\],\s]*(?:с\.?|т\.?)(?:,\s*\[[0-9]+\]\s*л\.?\s*[^.;]+)?)",
        physical_segment or work_text,
    )
    circulation = extract_first(r"([0-9 ]+\s*экз\.)", physical_segment or work_text)
    series_statement = extract_first(r"\(([^()]{3,160})\)\s*$", work_text)
    title_fields = parse_title_fields(clean_record_text(main_segment.strip(" .;-")))
    if not title_fields["title"]:
        warnings.append(WarningItem("title_missing", "Main title was not recognized.", work_text))
    if not year:
        warnings.append(WarningItem("year_missing", "Publication year was not recognized.", work_text))
    if not publication["publication_place"] and publication_segment:
        warnings.append(
            WarningItem("publication_place_missing", "Publication place was not recognized.", publication_segment)
        )

    if re.search(r"\b(электронный ресурс|URL|Режим доступа|https?://)\b", text, re.IGNORECASE):
        carrier_type = "online" if identifiers.get("url") else "electronic"
        media_type = "electronic"
    else:
        carrier_type = None
        media_type = None

    source = {
        "source_type": guess_source_type(work_text),
        "title": title_fields["title"],
        "parallel_title": title_fields["parallel_title"],
        "subtitle": title_fields["subtitle"],
        "title_remainder": title_fields["title_remainder"],
        "raw_author_string": raw_author_string,
        "responsibility_statement": title_fields["responsibility_statement"],
        "edition_statement": extract_first(r"((?:\d+-е|[0-9]+-е)\s+изд\.[^.]*|Изд\.\s*[^.]*)", work_text),
        "publication_place": publication["publication_place"],
        "publisher": publication["publisher"],
        "publication_date": publication_date,
        "year": year,
        "extent": extent,
        "illustrations": extract_first(r"\b(ил\.|табл\.|портр\.|диагр\.)\b", work_text),
        "dimensions": extract_first(r"([0-9]{2,3}\s*см)", work_text),
        "circulation": circulation,
        "series_statement": series_statement,
        "notes": "; ".join(note_segments) or None,
        "public_review": public_review,
        "raw_publication_details": work_text,
        "isbn": identifiers.get("isbn"),
        "issn": identifiers.get("issn"),
        "doi": identifiers.get("doi"),
        "url": identifiers.get("url"),
        "access_date": identifiers.get("access_date"),
        "content_type": "Текст" if work_text else None,
        "media_type": media_type,
        "carrier_type": carrier_type,
    }
    return source, authors, warnings


def split_gost_segments(text: str) -> list[str]:
    parts = [clean_record_text(segment.strip(" .")) for segment in re.split(r"\s+-\s+", text)]
    merged: list[str] = []
    index = 0
    while index < len(parts):
        current = parts[index]
        next_part = parts[index + 1] if index + 1 < len(parts) else ""
        if (
            next_part
            and not has_publication_statement(next_part)
            and not has_physical_description(next_part)
            and not re.match(r"^(?:[ТT]\.|Том|Ч\.|Часть|Вып\.|№|N|No\.?)\b", next_part, re.IGNORECASE)
            and len(current.split()) <= 8
            and len(next_part.split()) <= 14
        ):
            current = f"{current} - {next_part}"
            index += 1
        merged.append(current)
        index += 1
    return merged


def split_embedded_publication(text: str) -> tuple[str, str] | None:
    match = re.match(
        r"^(.+?)\.\s+((?:[А-ЯЁA-Z][^:]{0,120}|Б\.?\s*м\.?)\s*:\s*.+,\s*(?:ценз\.\s*)?(?:1[5-9]\d{2}|20\d{2}))$",
        text,
    )
    if not match:
        return None
    return clean_record_text(match.group(1)), clean_record_text(match.group(2))


def first_segment(segments: list[str], predicate: Any) -> str | None:
    for segment in segments:
        if predicate(segment):
            return segment
    return None


def has_publication_statement(value: str) -> bool:
    return bool(
        re.search(r":\s*.+,\s*(?:ценз\.\s*)?(?:1[5-9]\d{2}|20\d{2})", value)
        or re.search(r"(?:^|,\s*)(?:1[5-9]\d{2}|20\d{2})(?:\.|$)", value)
        or re.search(r"\b(?:Б\.?\s*м\.?|Б\.м\.)\b", value, re.IGNORECASE)
    )


def has_physical_description(value: str) -> bool:
    return bool(re.search(r"\b(?:с\.?|л\.?|т\.?|экз\.?)\b", value, re.IGNORECASE))


def guess_source_type(text: str) -> str:
    if re.search(r"\b(федеральный закон|постановление|приказ|ГОСТ|закон)\b", text, re.IGNORECASE):
        return "legal_document"
    if re.search(r"\b(электронный ресурс|URL|Режим доступа|https?://)\b", text, re.IGNORECASE):
        return "electronic_resource"
    if re.search(r"\b(доклад|материалы конференции|тезисы)\b", text, re.IGNORECASE):
        return "conference_material"
    return "book"


def parse_record(record: RawRecord, run_id: str) -> ParseResult:
    if record.ai_markup:
        return parse_ai_marked_record(record, run_id)

    text = record.normalized_text
    warnings: list[WarningItem] = []
    if not text:
        warnings.append(WarningItem("empty_record", "Record is empty.", severity="error"))

    article_container_parts = re.split(r"\s+//\s+", text, maxsplit=1)
    if len(article_container_parts) == 2:
        return parse_article_container_record(record, run_id, article_container_parts[0], article_container_parts[1])
    else:
        source, authors, source_warnings = parse_standalone(text)
        warnings.extend(source_warnings)
        container = {"periodical": None, "issue": None, "article_placement": None}
        if re.search(r"\bС\.\s*\d+", text) and source["source_type"] != "article":
            warnings.append(
                WarningItem(
                    "possible_article_without_container_separator",
                    "Page range found without // container separator.",
                    text,
                )
            )

    confidence = score_confidence(source, container, warnings)
    description_status = status_from_confidence(confidence, warnings)
    candidate_id = f"candidate-{sha256_text(run_id + ':' + record.raw_record_id)[:16]}"
    candidate = {
        "candidate_id": candidate_id,
        "raw_record_id": record.raw_record_id,
        "parser_version": PARSER_VERSION,
        "source": source,
        "authors": authors,
        "periodical": container.get("periodical"),
        "issue": container.get("issue"),
        "container_kind": container.get("container_kind"),
        "article_placement": container.get("article_placement"),
        "confidence": confidence,
        "description_status": description_status,
        "warning_codes": [warning.code for warning in warnings],
        "raw_text": record.normalized_text,
    }
    return ParseResult(candidate=candidate, warnings=warnings)


def parse_article_container_record(record: RawRecord, run_id: str, article_text: str, container_text: str) -> list[ParseResult]:
    article_source, article_authors, article_warnings = parse_article_part(article_text)
    container, _periodical, container_warnings = parse_container(container_text)
    article_warnings.append(
        WarningItem(
            "article_container_split",
            "Source record was split into independent article and container candidates.",
            record.normalized_text,
            severity="info",
        )
    )

    article_confidence = score_confidence(article_source, {"periodical": None, "issue": None, "article_placement": None}, article_warnings)
    article_candidate = {
        "candidate_id": f"candidate-{sha256_text(run_id + ':' + record.raw_record_id + ':article')[:16]}",
        "raw_record_id": record.raw_record_id,
        "candidate_part": "article",
        "related_candidate_part": "container",
        "parser_version": PARSER_VERSION,
        "source": article_source,
        "authors": article_authors,
        "periodical": None,
        "issue": None,
        "container_kind": container.get("container_kind"),
        "article_placement": None,
        "confidence": article_confidence,
        "description_status": status_from_confidence(article_confidence, article_warnings),
        "warning_codes": [warning.code for warning in article_warnings],
        "raw_text": clean_record_text(article_text),
        "source_raw_text": record.normalized_text,
    }

    container_source = source_from_container_candidate(container, container_text)
    container_confidence = score_confidence(container_source, {"periodical": None, "issue": None, "article_placement": None}, container_warnings)
    container_candidate = {
        "candidate_id": f"candidate-{sha256_text(run_id + ':' + record.raw_record_id + ':container')[:16]}",
        "raw_record_id": record.raw_record_id,
        "candidate_part": "container",
        "related_candidate_part": "article",
        "parser_version": PARSER_VERSION,
        "source": container_source,
        "authors": [],
        "periodical": container.get("periodical"),
        "issue": container.get("issue"),
        "container_kind": container.get("container_kind"),
        "article_placement": None,
        "confidence": container_confidence,
        "description_status": status_from_confidence(container_confidence, container_warnings),
        "warning_codes": [warning.code for warning in container_warnings],
        "raw_text": clean_record_text(container_text),
        "source_raw_text": record.normalized_text,
    }
    return [
        ParseResult(candidate=article_candidate, warnings=article_warnings),
        ParseResult(candidate=container_candidate, warnings=container_warnings),
    ]


def source_from_container_candidate(container: dict[str, Any], container_text: str) -> dict[str, Any]:
    periodical = container.get("periodical") or {}
    issue = container.get("issue") or {}
    title = periodical.get("title") or issue.get("title")
    publication_date = issue.get("publication_date")
    source_type = "issue" if container.get("container_kind") == "periodical_issue" else "collection"
    return {
        "source_type": source_type,
        "title": title,
        "parallel_title": None,
        "subtitle": None,
        "title_remainder": None,
        "responsibility_statement": None,
        "raw_author_string": None,
        "edition_statement": None,
        "publication_place": issue.get("publication_place"),
        "publisher": issue.get("publisher"),
        "publication_date": publication_date,
        "year": issue.get("year"),
        "extent": None,
        "illustrations": None,
        "dimensions": None,
        "series_statement": None,
        "notes": None,
        "isbn": None,
        "issn": periodical.get("issn"),
        "doi": None,
        "url": None,
        "access_date": None,
        "content_type": "Текст",
        "media_type": None,
        "carrier_type": None,
        "circulation": None,
        "raw_publication_details": clean_record_text(container_text),
    }


def parse_ai_marked_record(record: RawRecord, run_id: str) -> ParseResult:
    markup = record.ai_markup or {}
    warnings: list[WarningItem] = []
    record_type = clean_record_text(str(markup.get("record_type") or "book")).lower()
    source_type = {
        "book": "book",
        "article": "article",
        "journal_article": "article",
        "collection_article": "article",
        "conference_material": "conference_material",
        "legal_document": "legal_document",
        "electronic_resource": "electronic_resource",
        "unknown": "unknown",
    }.get(record_type, "unknown")
    if source_type == "unknown":
        warnings.append(WarningItem("ai_record_type_unknown", "AI markup did not identify a supported record type.", record_type))

    notes = normalize_ai_notes(markup.get("notes"))
    for warning_text in normalize_ai_notes(markup.get("warnings")):
        warnings.append(WarningItem("ai_markup_warning", warning_text, warning_text))

    publication_date = as_optional_text(markup.get("publication_date"))
    year = extract_year(publication_date or "") or extract_year(record.normalized_text)
    title = as_optional_text(markup.get("title"))
    if not title:
        warnings.append(WarningItem("title_missing", "AI markup did not return a title.", record.normalized_text))

    source = {
        "source_type": source_type,
        "title": title,
        "parallel_title": None,
        "subtitle": as_optional_text(markup.get("subtitle")),
        "title_remainder": as_optional_text(markup.get("title_remainder")),
        "responsibility_statement": as_optional_text(markup.get("responsibility_statement")),
        "raw_author_string": "; ".join(author["display_name"] for author in normalize_ai_authors(markup.get("authors"))),
        "edition_statement": None,
        "publication_place": as_optional_text(markup.get("publication_place")),
        "publisher": as_optional_text(markup.get("publisher")),
        "publication_date": publication_date,
        "year": year,
        "extent": as_optional_text(markup.get("extent")),
        "illustrations": None,
        "dimensions": None,
        "series_statement": None,
        "notes": "; ".join(notes) if notes else None,
        "public_review": as_optional_text(markup.get("public_review")),
        "isbn": None,
        "issn": None,
        "doi": None,
        "url": None,
        "access_date": None,
        "content_type": "Текст",
        "media_type": None,
        "carrier_type": None,
        "circulation": None,
        "raw_publication_details": record.normalized_text,
    }
    authors = normalize_ai_authors(markup.get("authors"))
    container = ai_container_from_markup(markup, source_type)
    confidence = normalize_ai_confidence(markup.get("confidence"))
    if warnings:
        confidence = min(confidence, 0.75)
    description_status = status_from_confidence(confidence, warnings)
    candidate_id = f"candidate-{sha256_text(run_id + ':' + record.raw_record_id)[:16]}"
    candidate = {
        "candidate_id": candidate_id,
        "raw_record_id": record.raw_record_id,
        "parser_version": PARSER_VERSION + "+ai-markup",
        "source": source,
        "authors": authors,
        "periodical": container.get("periodical"),
        "issue": container.get("issue"),
        "container_kind": container.get("container_kind"),
        "article_placement": container.get("article_placement"),
        "confidence": confidence,
        "description_status": description_status,
        "warning_codes": [warning.code for warning in warnings],
        "raw_text": record.normalized_text,
        "ai_markup": markup,
    }
    return ParseResult(candidate=candidate, warnings=warnings)


def as_optional_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = clean_record_text(str(value).strip(" .;"))
    return text or None


def normalize_ai_notes(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [clean_record_text(str(item)) for item in value if clean_record_text(str(item))]
    text = clean_record_text(str(value))
    return [text] if text else []


def normalize_ai_authors(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    raw_items = value if isinstance(value, list) else [value]
    authors: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items, start=1):
        if isinstance(item, dict):
            display_name = as_optional_text(item.get("display_name") or item.get("name") or item.get("name_as_printed"))
            role = as_optional_text(item.get("role")) or "author"
            source_text = as_optional_text(item.get("source_text")) or display_name
        else:
            display_name = as_optional_text(item)
            role = "author"
            source_text = display_name
        if not display_name:
            continue
        authors.append(
            {
                "display_name": display_name,
                "heading_name": display_name,
                "name_as_printed": display_name,
                "role": role,
                "sort_order": index,
                "source_text": source_text,
            }
        )
    return authors


def normalize_ai_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.85
    if confidence > 1:
        confidence = confidence / 100
    return round(min(max(confidence, 0), 1), 2)


def ai_container_from_markup(markup: dict[str, Any], source_type: str) -> dict[str, Any]:
    if source_type != "article":
        return {"container_kind": None, "periodical": None, "issue": None, "article_placement": None}
    periodical_title = as_optional_text(markup.get("periodical_title"))
    issue_year = extract_year(str(markup.get("issue_year") or "")) if markup.get("issue_year") else None
    issue_number = as_optional_text(markup.get("issue_number"))
    pages = as_optional_text(markup.get("article_pages"))
    container_kind = "periodical_issue" if periodical_title or issue_number else None
    return {
        "container_kind": container_kind,
        "periodical": {"title": periodical_title} if periodical_title else None,
        "issue": {"year": issue_year, "issue_number": issue_number} if issue_year or issue_number else None,
        "article_placement": {"pages_raw": pages, "page_start": None, "page_end": None} if pages else None,
    }


def parse_article_part(text: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[WarningItem]]:
    source, authors, warnings = parse_standalone(text)
    warnings = [warning for warning in warnings if warning.code != "year_missing"]
    source["source_type"] = "article"
    for field_name in ("publication_place", "publisher", "publication_date", "year", "extent", "circulation"):
        source[field_name] = None
    source["raw_publication_details"] = text
    return source, authors, warnings


def score_confidence(source: dict[str, Any], container: dict[str, Any], warnings: list[WarningItem]) -> float:
    score = 0.35
    if source.get("title"):
        score += 0.25
    if source.get("year") or source.get("publication_date"):
        score += 0.1
    if source.get("source_type") == "article":
        if container.get("periodical") or container.get("issue"):
            score += 0.2
        if (container.get("article_placement") or {}).get("pages_raw"):
            score += 0.05
    elif source.get("publication_place") or source.get("publisher"):
        score += 0.15
    if source.get("isbn") or source.get("issn") or source.get("doi") or source.get("url"):
        score += 0.05
    score -= min(0.25, len(warnings) * 0.05)
    return round(max(0.0, min(1.0, score)), 2)


def status_from_confidence(confidence: float, warnings: list[WarningItem]) -> str:
    if any(warning.severity == "error" for warning in warnings):
        return "raw_only"
    if confidence >= 0.75 and not warnings:
        return "parsed"
    if confidence >= 0.5:
        return "partial"
    return "needs_review"


def warning_rows(run_id: str, results: list[ParseResult]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        for warning in result.warnings:
            rows.append(
                {
                    "run_id": run_id,
                    "candidate_id": result.candidate["candidate_id"],
                    "raw_record_id": result.candidate["raw_record_id"],
                    "severity": warning.severity,
                    "code": warning.code,
                    "message": warning.message,
                    "fragment": warning.fragment,
                }
            )
    return rows


def write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, dialect="excel-tab", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_review_html(
    path: Path,
    run_dir: Path,
    review_pairs: list[dict[str, Any]],
    review_new: list[dict[str, Any]],
    container_resolution_rows: list[dict[str, Any]],
) -> None:
    full_pairs: list[dict[str, Any]] = []
    partial_pairs: list[dict[str, Any]] = []
    for pair in review_pairs:
        candidate_fields = display_fields_from_candidate(pair["candidate"])
        editor_fields = display_fields_from_editor(pair["editor"])
        if changed_display_fields(candidate_fields, editor_fields):
            partial_pairs.append(pair)
        else:
            full_pairs.append(pair)

    container_by_raw_record = {row.get("raw_record_id"): row for row in container_resolution_rows}
    full_pair_items = "\n".join(render_match_pair(pair, container_by_raw_record) for pair in full_pairs)
    partial_pair_items = "\n".join(render_match_pair(pair, container_by_raw_record) for pair in partial_pairs)
    new_items = "\n".join(render_new_candidate(item, container_by_raw_record) for item in review_new)
    doc = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Parser review {html.escape(run_dir.name)}</title>
  <style>
    :root {{
      --catalog-bg: #f4f4f4;
      --catalog-surface: #ffffff;
      --catalog-soft: #f6f6f6;
      --catalog-border: #dddddd;
      --catalog-ink: #242424;
      --catalog-muted: #6f6f6f;
      --catalog-accent: #be373b;
      --field-mark-bg: #fff1f1;
      --font-family: "San Francisco", "Roboto", Arial, sans-serif;
      --heading-font-family: "Scada", var(--font-family);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--catalog-bg);
      color: var(--catalog-ink);
      font: 13px/1.15 var(--font-family);
    }}
    .wrap {{
      width: min(1230px, calc(100% - 32px));
      min-height: 100vh;
      margin: 0 auto;
      padding: 26px 30px 56px;
      background: var(--catalog-surface);
    }}
    h1 {{
      margin: 0;
      color: #242424;
      font-family: var(--heading-font-family);
      font-size: 42px;
      font-weight: 400;
      line-height: 1.12;
      letter-spacing: 0;
      text-transform: uppercase;
    }}
    h2 {{
      margin: 0;
      font-family: var(--heading-font-family);
      font-size: 24px;
      font-weight: 400;
      letter-spacing: 0;
    }}
    .lede {{
      max-width: 780px;
      margin: 12px 0 0;
      color: var(--catalog-muted);
      font-size: 17px;
      line-height: 1.35;
    }}
    .review-nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 18px 0 24px;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--catalog-border);
    }}
    .review-nav a {{
      min-height: 34px;
      padding: 6px 12px;
      border: 1px solid var(--catalog-border);
      color: var(--catalog-ink);
      background: #fff;
      font-weight: 700;
      text-decoration: none;
    }}
    .stage-actions {{
      display: flex;
      gap: 8px;
      justify-content: flex-end;
      margin: -10px 0 22px;
    }}
    .stage-link {{
      min-height: 34px;
      padding: 7px 12px;
      border: 1px solid var(--catalog-ink);
      color: #fff;
      background: var(--catalog-ink);
      font-weight: 700;
      text-decoration: none;
    }}
    .review-section {{
      margin-top: 30px;
      border: 1px solid var(--catalog-border);
      background: #fff;
    }}
    .results-header {{
      padding: 16px 18px;
      border-bottom: 1px solid var(--catalog-border);
      color: var(--catalog-muted);
      background: #fff;
    }}
    .results-count {{ margin-top: 6px; font-size: 14px; }}
    .bibliography-list {{
      margin: 0;
      padding: 0;
      list-style: none;
      background: #fff;
    }}
    .bibliography-item {{
      padding: 14px 18px 15px;
      border-bottom: 1px solid var(--catalog-border);
    }}
    .bibliography-item:last-child {{ border-bottom: 0; }}
    .pair-layout {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 142px;
      gap: 16px;
      align-items: stretch;
    }}
    .citation-row {{
      color: var(--catalog-ink);
      font-size: 10pt;
      line-height: 1.22;
      padding: 2px 4px;
      cursor: pointer;
        white-space: nowrap;     /* Запрещает перенос строк */
  overflow: hidden;        /* Прячет текст, который вышел за границы */
  text-overflow: clip;     /* Просто обрезает текст (по умолчанию) */

    }}
    .citation-row + .citation-row {{ margin-top: 3px; }}
    .citation-row.is-selected-new {{
      background: #ffe7ed;
    }}
    .citation-row.is-selected-old {{
      background: #e6f4ea;
    }}
    .work-number {{
      color: var(--catalog-muted);
      font-weight: normal;
      margin-right: .35em;
    }}
    .candidate-citation {{ color: var(--catalog-muted); }}
    .field-author {{ font-weight: 700; }}
    .record-prefix {{
      color: var(--catalog-ink);
      font-weight: 800;
    }}
    .field {{
      border-bottom: 1px solid transparent;
      text-underline-offset: 3px;
    }}
    .field-diff {{
      color: var(--catalog-accent);
      background: var(--field-mark-bg);
      font-weight: 800;
      border-bottom-color: var(--catalog-accent);
    }}
    .field-boundary {{
      border-bottom: 1px solid #9aa0a6;
      text-decoration: underline;
      text-decoration-color: #9aa0a6;
      text-underline-offset: 3px;
    }}
    .field-note {{
      margin-top: 8px;
      color: var(--catalog-muted);
      font-size: 13px;
    }}
    .field-note strong {{ color: var(--catalog-accent); }}
    .pair-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 9px;
      color: var(--catalog-muted);
      font-size: 13px;
    }}
    .score {{
      color: var(--catalog-accent);
      font-weight: 800;
    }}
    .pair-actions {{
      display: flex;
      align-items: stretch;
      justify-content: stretch;
    }}
    .service-button {{
      min-height: 34px;
      padding: 6px 10px;
      border: 1px solid var(--catalog-border);
      border-radius: 0;
      color: var(--catalog-muted);
      background: #fff;
      cursor: default;
      font: inherit;
      font-weight: 700;
      text-align: center;
      text-decoration: none;
    }}
    .split-button {{
      display: flex;
      align-items: center;
      justify-content: center;
      width: 100%;
      height: 100%;
      min-height: calc(2.44em + 7px);
    }}
    .split-button.is-accepted {{
      border-color: #188038;
      color: #188038;
    }}
    .split-button.is-split {{
      border-color: #c5221f;
      color: #c5221f;
    }}
    .secondary-button {{
      border-color: var(--catalog-border);
      color: var(--catalog-ink);
    }}
    .new-record .citation-row {{
      padding-right: 16px;
    }}
    .empty-state {{
      padding: 18px;
      color: var(--catalog-muted);
    }}
    code {{ background: #f1f3f4; padding: 1px 4px; }}
  </style>
</head>
<body>
  <main class="wrap">
    <h1>Разбор парсинга</h1>
    <p class="lede">Run <code>{html.escape(run_dir.name)}</code>. Страница показывает основные совпадения с <code>match_score >= 0.7</code> и слабые кандидаты для записей с <code>confidence >= 0.7</code>.</p>
    <nav class="review-nav">
      <a href="#full-matches">Полное совпадение: {len(full_pairs)}</a>
      <a href="#matches">Совпадение: {len(partial_pairs)}</a>
      <a href="#new">Новые: {len(review_new)}</a>
    </nav>
    <div class="stage-actions">
      <a class="stage-link" href="../review_containers.html/">Вернуться к контейнерам</a>
      <a class="stage-link" href="../review_authors.html/">К авторам</a>
      <a class="stage-link" href="../review_stage2.html/">Перейти ко второму этапу</a>
    </div>
    <section id="full-matches" class="review-section">
      <div class="results-header">
        <h2>Полное совпадение</h2>
        <div class="results-count">Пар без отличий в отображаемых полях: <strong>{len(full_pairs)}</strong></div>
      </div>
      <ul class="bibliography-list">
        {full_pair_items or '<li class="empty-state">Нет полных совпадений.</li>'}
      </ul>
    </section>
    <section id="matches" class="review-section">
      <div class="results-header">
        <h2>Совпадение</h2>
        <div class="results-count">Пар с сильным совпадением и отличиями полей: <strong>{len(partial_pairs)}</strong></div>
      </div>
      <ul class="bibliography-list">
        {partial_pair_items or '<li class="empty-state">Нет совпадений с отличиями полей.</li>'}
      </ul>
    </section>
    <section id="new" class="review-section">
      <div class="results-header">
        <h2>Новые</h2>
        <div class="results-count">Записей без сильного совпадения: <strong>{len(review_new)}</strong></div>
      </div>
      <ul class="bibliography-list">
        {new_items or '<li class="empty-state">Нет новых записей.</li>'}
      </ul>
    </section>
  </main>
  <script>
    var stateUrl = "../state/";

    function itemId(item) {{
      if (!item.dataset.reviewId) {{
        item.dataset.reviewId = "stage1-item-" + Array.prototype.indexOf.call(document.querySelectorAll(".bibliography-item"), item);
      }}
      return item.dataset.reviewId;
    }}

    function csrfToken() {{
      var cookies = document.cookie.split(";").map(function (item) {{ return item.trim(); }});
      for (var i = 0; i < cookies.length; i += 1) {{
        if (cookies[i].indexOf("bibliobon_data_editor_csrftoken=") === 0) {{
          return decodeURIComponent(cookies[i].split("=").slice(1).join("="));
        }}
        if (cookies[i].indexOf("csrftoken=") === 0) {{
          return decodeURIComponent(cookies[i].split("=").slice(1).join("="));
        }}
      }}
      return "";
    }}

    function postReviewState(payload) {{
      fetch(stateUrl, {{
        method: "POST",
        headers: {{
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken()
        }},
        body: JSON.stringify(payload)
      }}).catch(function () {{}});
    }}

    function saveStageOneItem(item, decision, selected) {{
      postReviewState({{
        stage: "stage1",
        item_id: itemId(item),
        decision: decision,
        selected: selected || "",
        candidate_id: item.dataset.candidateId || "",
        editor_source_id: item.dataset.editorSourceId || "",
        match_score: item.dataset.matchScore || ""
      }});
    }}

    function saveStageOneState() {{
      var decisions = [];
      document.querySelectorAll(".bibliography-item").forEach(function (item) {{
        var button = item.querySelector(".split-button");
        if (button && button.classList.contains("is-split")) {{
          decisions.push({{ id: itemId(item), decision: "new" }});
        }} else if (button && button.classList.contains("is-accepted")) {{
          var selected = item.querySelector(".is-selected-new, .is-selected-old");
          decisions.push({{
            id: itemId(item),
            decision: selected && selected.classList.contains("is-selected-old") ? "keep_old" : "keep_new"
          }});
        }}
      }});
      localStorage.setItem("bibliobon-parser-stage1-decisions", JSON.stringify(decisions));
    }}

    document.addEventListener("click", function (event) {{
      var splitButton = event.target.closest(".split-button");
      if (splitButton) {{
        var splitItem = splitButton.closest(".bibliography-item");
        if (!splitItem) return;
        splitItem.querySelectorAll(".citation-row").forEach(function (candidate) {{
          candidate.classList.remove("is-selected-new", "is-selected-old");
        }});
        splitButton.classList.toggle("is-split");
        splitButton.classList.remove("is-accepted");
        splitButton.textContent = "Разделить";
        saveStageOneItem(splitItem, splitButton.classList.contains("is-split") ? "new" : "clear", "");
        saveStageOneState();
        return;
      }}

      var stageLink = event.target.closest(".stage-link");
      if (stageLink) {{
        saveStageOneState();
        return;
      }}

      var row = event.target.closest(".citation-row");
      if (!row) return;
      var item = row.closest(".bibliography-item");
      if (!item) return;
      var wasSelected = row.classList.contains("is-selected-new") || row.classList.contains("is-selected-old");
      item.querySelectorAll(".citation-row").forEach(function (candidate) {{
        candidate.classList.remove("is-selected-new", "is-selected-old");
      }});
      var button = item.querySelector(".split-button");
      if (wasSelected) {{
        if (button) {{
          button.classList.remove("is-accepted", "is-split");
          button.textContent = "Разделить";
        }}
        saveStageOneItem(item, "clear", "");
        saveStageOneState();
        return;
      }}
      var selected = "";
      if (row.classList.contains("candidate-citation")) {{
        row.classList.add("is-selected-new");
        selected = "new";
      }} else {{
        row.classList.add("is-selected-old");
        selected = "old";
      }}
      if (button) {{
        button.classList.remove("is-split");
        button.classList.add("is-accepted");
        button.textContent = "Принять";
      }}
      saveStageOneItem(item, selected === "old" ? "keep_old" : "keep_new", selected);
      saveStageOneState();
    }});
  </script>
</body>
</html>
"""
    path.write_text(doc, encoding="utf-8")


def write_container_review_html(path: Path, run_dir: Path, container_resolution_rows: list[dict[str, Any]]) -> None:
    raw_record_examples = load_raw_record_examples(run_dir)
    grouped_rows = group_container_resolution_rows(container_resolution_rows)
    attach_source_examples_to_container_groups(grouped_rows, raw_record_examples)
    ready_rows = [row for row in grouped_rows if row.get("status") == "ready"]
    review_rows = [row for row in grouped_rows if row.get("status") != "ready"]
    ready_items = "\n".join(render_container_stage0_group(row) for row in ready_rows)
    review_items = "\n".join(render_container_stage0_group(row) for row in review_rows)
    doc = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Контейнеры {html.escape(run_dir.name)}</title>
  <style>
    :root {{
      --catalog-bg: #f4f4f4;
      --catalog-surface: #ffffff;
      --catalog-border: #dddddd;
      --catalog-ink: #242424;
      --catalog-muted: #6f6f6f;
      --catalog-accent: #be373b;
      --font-family: "San Francisco", "Roboto", Arial, sans-serif;
      --heading-font-family: "Scada", var(--font-family);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--catalog-bg);
      color: var(--catalog-ink);
      font: 16px/1.35 var(--font-family);
    }}
    .wrap {{
      width: min(1230px, calc(100% - 32px));
      min-height: 100vh;
      margin: 0 auto;
      padding: 26px 30px 56px;
      background: var(--catalog-surface);
    }}
    h1 {{
      margin: 0;
      font-family: var(--heading-font-family);
      font-size: 42px;
      font-weight: 400;
      line-height: 1.12;
      letter-spacing: 0;
      text-transform: uppercase;
    }}
    h2 {{
      margin: 0;
      font-family: var(--heading-font-family);
      font-size: 24px;
      font-weight: 400;
      letter-spacing: 0;
    }}
    .lede {{
      max-width: 850px;
      margin: 12px 0 0;
      color: var(--catalog-muted);
      font-size: 17px;
    }}
    .stage-actions {{
      display: flex;
      justify-content: flex-end;
      margin: 18px 0 22px;
    }}
    .stage-link {{
      min-height: 34px;
      padding: 7px 12px;
      border: 1px solid var(--catalog-ink);
      color: #fff;
      background: var(--catalog-ink);
      font-weight: 700;
      text-decoration: none;
    }}
    .review-section {{
      margin-top: 24px;
      border: 1px solid var(--catalog-border);
      background: #fff;
    }}
    .results-header {{
      padding: 16px 18px;
      border-bottom: 1px solid var(--catalog-border);
      color: var(--catalog-muted);
      background: #fff;
    }}
    .bibliography-list {{
      margin: 0;
      padding: 0;
      list-style: none;
    }}
    .bibliography-item {{
      padding: 14px 18px 15px;
      border-bottom: 1px solid var(--catalog-border);
    }}
    .bibliography-item:last-child {{ border-bottom: 0; }}
    .pair-layout {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 260px;
      gap: 16px;
      align-items: stretch;
    }}
    .citation-row {{
      padding: 2px 4px;
      color: var(--catalog-ink);
      font-size: 10pt;
      line-height: 1.22;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: clip;
    }}
    .candidate-citation {{ color: var(--catalog-muted); }}
    .record-prefix {{
      color: var(--catalog-ink);
      font-weight: 800;
    }}
    .field {{
      border-bottom: 1px solid #9aa0a6;
      text-decoration: underline;
      text-decoration-color: #9aa0a6;
      text-underline-offset: 3px;
    }}
    .field-note {{
      margin-top: 8px;
      color: var(--catalog-muted);
      font-size: 13px;
    }}
    .pair-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 9px;
      color: var(--catalog-muted);
      font-size: 13px;
    }}
    .pair-actions {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
      align-content: center;
    }}
    .issue-list {{
      display: grid;
      gap: 10px;
      margin-top: 10px;
    }}
    .issue-item {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 260px;
      gap: 16px;
      padding-top: 10px;
      border-top: 1px solid var(--catalog-border);
    }}
    .choice-panel {{
      display: none;
      margin-top: 10px;
      padding: 10px;
      border: 1px solid var(--catalog-border);
      background: #fff;
    }}
    .choice-panel.is-open {{ display: block; }}
    .choice-search {{
      width: 100%;
      min-height: 32px;
      margin-bottom: 8px;
      padding: 6px 8px;
      border: 1px solid var(--catalog-border);
      font: inherit;
    }}
    .choice-list {{
      display: grid;
      gap: 6px;
      max-height: 230px;
      overflow: auto;
    }}
    .choice-button {{
      text-align: left;
      font-weight: 400;
    }}
    .service-button {{
      min-height: 34px;
      padding: 6px 10px;
      border: 1px solid var(--catalog-border);
      border-radius: 0;
      color: var(--catalog-muted);
      background: #fff;
      cursor: pointer;
      font: inherit;
      font-weight: 700;
      text-align: center;
    }}
    .service-button.is-selected {{
      border-color: #188038;
      color: #188038;
    }}
    .service-button.is-danger {{
      border-color: var(--catalog-accent);
      color: var(--catalog-accent);
    }}
    .empty-state {{
      padding: 18px;
      color: var(--catalog-muted);
    }}
    code {{ background: #f1f3f4; padding: 1px 4px; }}
  </style>
</head>
<body>
  <main class="wrap">
    <h1>Контейнеры</h1>
    <p class="lede">Run <code>{html.escape(run_dir.name)}</code>. Сначала подтверждаются журналы, выпуски и сборники, чтобы на следующем этапе статьи уже показывались с правильной привязкой.</p>
    <div class="stage-actions">
      <a class="stage-link" href="../review_authors.html/">Перейти к авторам</a>
    </div>
    <section class="review-section">
      <div class="results-header">
        <h2>Нужно подтвердить</h2>
        <div>Контейнеры, где нужно выбрать существующую запись или разрешить создание новой: <strong>{len(review_rows)}</strong></div>
      </div>
      <ul class="bibliography-list">
        {review_items or '<li class="empty-state">Нет контейнеров, требующих ручного решения.</li>'}
      </ul>
    </section>
    <section class="review-section">
      <div class="results-header">
        <h2>Найдены автоматически</h2>
        <div>Контейнеры, где журнал/выпуск или сборник уже найден в базе: <strong>{len(ready_rows)}</strong></div>
      </div>
      <ul class="bibliography-list">
        {ready_items or '<li class="empty-state">Нет автоматически найденных контейнеров.</li>'}
      </ul>
    </section>
  </main>
  <script>
    var stateUrl = "../state/";

    function csrfToken() {{
      var cookies = document.cookie.split(";").map(function (item) {{ return item.trim(); }});
      for (var i = 0; i < cookies.length; i += 1) {{
        if (cookies[i].indexOf("bibliobon_data_editor_csrftoken=") === 0) {{
          return decodeURIComponent(cookies[i].split("=").slice(1).join("="));
        }}
        if (cookies[i].indexOf("csrftoken=") === 0) {{
          return decodeURIComponent(cookies[i].split("=").slice(1).join("="));
        }}
      }}
      return "";
    }}

    function postReviewState(payload) {{
      return fetch(stateUrl, {{
        method: "POST",
        headers: {{
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken()
        }},
        body: JSON.stringify(payload)
      }}).then(function (response) {{
        if (!response.ok) throw new Error("state save failed");
        return response;
      }}).catch(function () {{
        document.body.classList.add("state-save-error");
        alert("Не удалось сохранить решение. Обновите страницу и попробуйте ещё раз.");
      }});
    }}

    function restoreStage0State() {{
      fetch(stateUrl)
        .then(function (response) {{ return response.ok ? response.json() : null; }})
        .then(function (state) {{
          if (!state || !state.stage0) return;
          Object.keys(state.stage0).forEach(function (itemId) {{
            var saved = state.stage0[itemId];
            var selector = '.service-button[data-item-id="' + CSS.escape(itemId) + '"][data-decision="' + CSS.escape(saved.decision || "") + '"]';
            var candidates = Array.prototype.slice.call(document.querySelectorAll(selector));
            var button = candidates.find(function (candidate) {{
              if (saved.issue_id && candidate.dataset.issueId !== saved.issue_id) return false;
              if (saved.periodical_id && candidate.dataset.periodicalId !== saved.periodical_id) return false;
              if (saved.collection_id && candidate.dataset.collectionId !== saved.collection_id) return false;
              return true;
            }}) || candidates[0];
            if (button) markDecision(button, true);
          }});
        }})
        .catch(function () {{}});
    }}

    function markDecision(button, skipSave) {{
      var item = button.closest(".bibliography-item");
      var scope = button.closest(".issue-item") || item;
      scope.querySelectorAll(".service-button").forEach(function (candidate) {{
        candidate.classList.remove("is-selected");
      }});
      button.classList.add("is-selected");
      if (skipSave) return;
      postReviewState({{
        stage: "stage0",
        item_id: button.dataset.itemId || item.dataset.candidateId || "",
        decision: button.dataset.decision || "",
        candidate_id: button.dataset.candidateId || item.dataset.candidateId || "",
        container_kind: item.dataset.containerKind || "",
        container_action: item.dataset.containerAction || "",
        periodical_id: button.dataset.periodicalId || item.dataset.periodicalId || "",
        issue_id: button.dataset.issueId || item.dataset.issueId || "",
        collection_id: button.dataset.collectionId || item.dataset.collectionId || ""
      }});
    }}

    document.addEventListener("click", function (event) {{
      var panelButton = event.target.closest(".service-button[data-open-panel]");
      if (panelButton) {{
        var scope = panelButton.closest(".issue-item") || panelButton.closest(".bibliography-item");
        var panel = scope.querySelector(".choice-panel");
        if (panel) panel.classList.toggle("is-open");
        return;
      }}
      var button = event.target.closest(".service-button[data-decision]");
      if (!button) return;
      markDecision(button);
    }});

    document.addEventListener("input", function (event) {{
      var search = event.target.closest(".choice-search");
      if (!search) return;
      var query = search.value.toLowerCase();
      var panel = search.closest(".choice-panel");
      panel.querySelectorAll(".choice-button").forEach(function (button) {{
        button.style.display = button.textContent.toLowerCase().indexOf(query) === -1 ? "none" : "";
      }});
    }});
    restoreStage0State();
  </script>
</body>
</html>
"""
    path.write_text(doc, encoding="utf-8")


def write_stage3_review_html(
    path: Path,
    run_dir: Path,
    review_new: list[dict[str, Any]],
    proposed_rows: list[dict[str, Any]],
    container_resolution_rows: list[dict[str, Any]],
    author_resolution_rows: list[dict[str, Any]],
    candidates_by_id: dict[str, dict[str, Any]],
    review_pairs: list[dict[str, Any]],
) -> None:
    new_books = [
        candidate
        for candidate in review_new
        if candidate.get("candidate_part") != "container"
    ]
    supplemented_ids = []
    for row in proposed_rows:
        candidate_id = row.get("candidate_id")
        if candidate_id and candidate_id not in supplemented_ids:
            supplemented_ids.append(candidate_id)
    supplemented = [candidates_by_id[candidate_id] for candidate_id in supplemented_ids if candidate_id in candidates_by_id]
    supplemented_id_set = set(supplemented_ids)
    journal_rows = [
        row
        for row in container_resolution_rows
        if row.get("action") in {"confirm_create_periodical", "create_periodical", "confirm_create_issue", "create_issue"}
    ]
    collection_rows = [
        row
        for row in container_resolution_rows
        if row.get("action") in {"confirm_create_collection", "create_collection"}
    ]
    author_rows = [
        row
        for row in author_resolution_rows
        if row.get("action") in {"confirm_create_author", "create_author"}
    ]
    groups = [
        ("Уже есть в базе", render_stage3_existing_items(review_pairs, supplemented_id_set) or '<li class="empty-state">Нет записей, уже найденных в базе.</li>'),
        ("Новые книги", render_stage3_candidate_items(new_books) or '<li class="empty-state">Нет новых книг.</li>'),
        ("Данные дополнены", render_stage3_candidate_items(supplemented, proposed_rows) or '<li class="empty-state">Нет дополнений к существующим записям.</li>'),
        ("Журналы", render_stage3_container_items(journal_rows) or '<li class="empty-state">Нет новых журналов или выпусков.</li>'),
        ("Сборники", render_stage3_container_items(collection_rows) or '<li class="empty-state">Нет новых сборников.</li>'),
        ("Авторы", render_stage3_author_items(author_rows) or '<li class="empty-state">Нет новых авторов.</li>'),
    ]
    sections = "\n".join(
        f"""
    <section class="review-section">
      <div class="results-header">
        <h2>{html.escape(title)}</h2>
      </div>
      <ul class="bibliography-list">
        {items}
      </ul>
    </section>
"""
        for title, items in groups
    )
    doc = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Третий этап {html.escape(run_dir.name)}</title>
  <style>
    :root {{
      --catalog-bg: #f4f4f4;
      --catalog-surface: #ffffff;
      --catalog-border: #dddddd;
      --catalog-ink: #242424;
      --catalog-muted: #6f6f6f;
      --catalog-accent: #be373b;
      --font-family: "San Francisco", "Roboto", Arial, sans-serif;
      --heading-font-family: "Scada", var(--font-family);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--catalog-bg);
      color: var(--catalog-ink);
      font: 13px/1.15 var(--font-family);
    }}
    .wrap {{
      width: min(1230px, calc(100% - 32px));
      min-height: 100vh;
      margin: 0 auto;
      padding: 26px 30px 56px;
      background: var(--catalog-surface);
    }}
    h1 {{
      margin: 0;
      color: #242424;
      font-family: var(--heading-font-family);
      font-size: 42px;
      font-weight: 400;
      line-height: 1.12;
      letter-spacing: 0;
      text-transform: uppercase;
    }}
    h2 {{
      margin: 0;
      font-family: var(--heading-font-family);
      font-size: 24px;
      font-weight: 400;
      letter-spacing: 0;
    }}
    .lede {{
      max-width: 860px;
      margin: 12px 0 0;
      color: var(--catalog-muted);
      font-size: 17px;
      line-height: 1.35;
    }}
    .review-nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 18px 0 24px;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--catalog-border);
    }}
    .review-nav a {{
      min-height: 34px;
      padding: 6px 12px;
      border: 1px solid var(--catalog-border);
      color: var(--catalog-ink);
      background: #fff;
      font-weight: 700;
      text-decoration: none;
    }}
    .review-section {{
      margin-top: 24px;
      border: 1px solid var(--catalog-border);
      background: #fff;
    }}
    .results-header {{
      padding: 16px 18px;
      border-bottom: 1px solid var(--catalog-border);
      color: var(--catalog-muted);
      background: #fff;
    }}
    .bibliography-list {{
      margin: 0;
      padding: 0;
      list-style: none;
    }}
    .bibliography-item {{
      padding: 12px 18px;
      border-bottom: 1px solid var(--catalog-border);
    }}
    .bibliography-item:last-child {{ border-bottom: 0; }}
    .citation-row {{
      color: var(--catalog-ink);
      font-size: 10pt;
      line-height: 1.22;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: clip;
    }}
    .field-note {{
      margin-top: 7px;
      color: var(--catalog-muted);
      font-size: 13px;
    }}
    .record-prefix {{
      color: var(--catalog-ink);
      font-weight: 800;
    }}
    .field {{ border-bottom: 1px solid transparent; }}
    .apply-row {{
      display: flex;
      justify-content: flex-end;
      gap: 10px;
      flex-wrap: wrap;
      padding: 18px;
      border-top: 1px solid var(--catalog-border);
    }}
    .service-button {{
      min-height: 34px;
      padding: 7px 12px;
      border: 1px solid #188038;
      border-radius: 0;
      color: #188038;
      background: #fff;
      cursor: pointer;
      font: inherit;
      font-weight: 700;
      text-align: center;
      text-decoration: none;
    }}
    .service-button.secondary {{
      border-color: var(--catalog-border);
      color: var(--catalog-ink);
    }}
    .empty-state {{
      padding: 18px;
      color: var(--catalog-muted);
    }}
    code {{ background: #f1f3f4; padding: 1px 4px; }}
  </style>
</head>
<body>
  <main class="wrap">
    <h1>Третий этап</h1>
    <p class="lede">Run <code>{html.escape(run_dir.name)}</code>. Ниже показан список staging-изменений, которые будут подготовлены к внесению в редакторскую базу после apply-шагa с backup.</p>
    <nav class="review-nav">
      <a href="../review_stage2.html/">Назад ко второму этапу</a>
      <a href="../review_report.html/">К первому этапу</a>
      <a href="../review_containers.html/">К контейнерам</a>
      <a href="../review_authors.html/">К авторам</a>
    </nav>
    {sections}
    <section class="review-section">
      <div class="results-header">
        <h2>Внесение в базу</h2>
      </div>
      <form class="apply-row" method="post" action="../apply/">
        <input type="hidden" name="csrfmiddlewaretoken" value="">
        <button class="service-button secondary" type="submit" name="mode" value="changed_only">Внести только изменённые записи</button>
        <button class="service-button" type="submit" name="mode" value="changed_and_new">Внести изменённые и новые записи</button>
      </form>
    </section>
  </main>
  <script>
    var csrf = document.cookie.split("; ").find(function (item) {{
      return item.indexOf("bibliobon_data_editor_csrftoken=") === 0;
    }});
    if (csrf) {{
      document.querySelectorAll('input[name="csrfmiddlewaretoken"]').forEach(function (input) {{
        input.value = decodeURIComponent(csrf.split("=").slice(1).join("="));
      }});
    }}
  </script>
</body>
</html>
"""
    path.write_text(doc, encoding="utf-8")


def write_stage2_review_html(
    path: Path,
    run_dir: Path,
    review_pairs: list[dict[str, Any]],
    review_new: list[dict[str, Any]],
) -> None:
    items = "\n".join(render_stage2_pair(pair) for pair in review_pairs)
    items += "\n".join(render_stage2_new(candidate) for candidate in review_new)
    count = len(review_pairs) + len(review_new)
    doc = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Второй этап {html.escape(run_dir.name)}</title>
  <style>
    :root {{ --catalog-bg:#f4f4f4; --catalog-surface:#fff; --catalog-border:#ddd; --catalog-ink:#242424; --catalog-muted:#6f6f6f; --catalog-accent:#be373b; --font-family:"San Francisco","Roboto",Arial,sans-serif; --heading-font-family:"Scada",var(--font-family); }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; min-height:100vh; background:var(--catalog-bg); color:var(--catalog-ink); font:13px/1.15 var(--font-family); }}
    .wrap {{ width:min(1230px,calc(100% - 32px)); min-height:100vh; margin:0 auto; padding:26px 30px 56px; background:var(--catalog-surface); }}
    h1 {{ margin:0; font-family:var(--heading-font-family); font-size:42px; font-weight:400; line-height:1.12; text-transform:uppercase; }}
    h2 {{ margin:0; font-family:var(--heading-font-family); font-size:24px; font-weight:400; }}
    .lede {{ max-width:860px; margin:12px 0 0; color:var(--catalog-muted); font-size:17px; line-height:1.35; }}
    .review-nav,.stage-actions {{ display:flex; gap:8px; justify-content:flex-end; margin:18px 0 22px; }}
    .review-nav {{ justify-content:flex-start; padding-bottom:18px; border-bottom:1px solid var(--catalog-border); }}
    .review-nav a,.stage-link {{ min-height:34px; padding:7px 12px; border:1px solid var(--catalog-border); color:var(--catalog-ink); background:#fff; font-weight:700; text-decoration:none; }}
    .stage-link {{ border-color:var(--catalog-ink); color:#fff; background:var(--catalog-ink); }}
    .review-section {{ margin-top:30px; border:1px solid var(--catalog-border); background:#fff; }}
    .results-header {{ padding:16px 18px; border-bottom:1px solid var(--catalog-border); color:var(--catalog-muted); }}
    .bibliography-list {{ margin:0; padding:0; list-style:none; }}
    .bibliography-item {{ padding:14px 18px 15px; border-bottom:1px solid var(--catalog-border); }}
    .bibliography-item:last-child {{ border-bottom:0; }}
    .pair-layout {{ display:grid; grid-template-columns:minmax(0,1fr) 142px; gap:16px; align-items:stretch; }}
    .citation-row {{ color:var(--catalog-ink); font-size:10pt; line-height:1.22; padding:2px 4px; cursor:pointer; white-space:nowrap; overflow:hidden; text-overflow:clip; }}
    .candidate-citation {{ color:var(--catalog-muted); }}
    .citation-row.is-selected-new {{ background:#ffe7ed; }}
    .citation-row.is-selected-old {{ background:#e6f4ea; }}
    .field-author,.record-prefix {{ font-weight:800; }}
    .field-note,.pair-meta {{ margin-top:8px; color:var(--catalog-muted); font-size:13px; }}
    .service-button {{ min-height:34px; padding:6px 10px; border:1px solid var(--catalog-border); color:var(--catalog-muted); background:#fff; cursor:pointer; font:inherit; font-weight:700; }}
    .status-box {{ display:flex; align-items:center; justify-content:center; width:100%; height:100%; min-height:calc(2.44em + 7px); }}
    .status-box.is-changed {{ border-color:#188038; color:#188038; }}
    .empty-state {{ padding:18px; color:var(--catalog-muted); }}
    .modal-backdrop {{ position:fixed; inset:0; display:none; align-items:flex-start; justify-content:center; padding:34px 18px; background:rgba(36,36,36,.28); z-index:10; }}
    .modal-backdrop.is-open {{ display:flex; }}
    .modal {{ width:min(760px,100%); max-height:calc(100vh - 68px); overflow:auto; border:1px solid var(--catalog-border); background:#fff; box-shadow:0 18px 48px rgba(0,0,0,.18); }}
    .modal-header,.modal-actions {{ padding:16px 18px; border-bottom:1px solid var(--catalog-border); }}
    .modal-actions {{ display:flex; justify-content:flex-end; gap:8px; border-top:1px solid var(--catalog-border); border-bottom:0; }}
    .modal-body {{ padding:18px; }}
    .form-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px 16px; }}
    .field-control {{ display:grid; gap:5px; }}
    .field-control.full {{ grid-column:1 / -1; }}
    label {{ color:var(--catalog-muted); font-size:12px; font-weight:700; text-transform:uppercase; }}
    input,textarea,select {{ width:100%; min-height:34px; padding:7px 9px; border:1px solid var(--catalog-border); font:inherit; }}
    textarea {{ min-height:70px; resize:vertical; }}
    .accept-button {{ border-color:#188038; color:#188038; }}
  </style>
</head>
<body>
  <main class="wrap">
    <h1>Второй этап разбора</h1>
    <p class="lede">Run <code>{html.escape(run_dir.name)}</code>. Здесь остаются записи, которые не были полностью закрыты на первом этапе. Клик по строке открывает форму правки.</p>
    <nav class="review-nav"><a href="../review_report.html/">Назад к первому этапу</a><a href="#records">Неразобранные: {count}</a></nav>
    <div class="stage-actions"><a class="stage-link" href="../review_stage3.html/">Перейти к третьему этапу</a></div>
    <section id="records" class="review-section">
      <div class="results-header"><h2>Неразобранные записи</h2></div>
      <ul class="bibliography-list">{items or '<li class="empty-state">Нет записей для второго этапа.</li>'}</ul>
    </section>
  </main>
  <div class="modal-backdrop" id="edit-modal" aria-hidden="true">
    <div class="modal">
      <div class="modal-header"><h2>Редактирование записи</h2></div>
      <div class="modal-body">
        <div class="form-grid">
          <div class="field-control"><label>Тип</label><select id="source-type"><option value="book">Книга</option><option value="article">Статья</option><option value="container">Журнал/сборник</option></select></div>
          <div class="field-control"><label>Авторы</label><input id="authors"></div>
          <div class="field-control full"><label>Заглавие</label><textarea id="title"></textarea></div>
          <div class="field-control full"><label>Подзаголовок</label><textarea id="subtitle"></textarea></div>
          <div class="field-control full"><label>Ответственность</label><textarea id="responsibility"></textarea></div>
          <div class="field-control"><label>Место</label><input id="place"></div>
          <div class="field-control"><label>Издательство</label><input id="publisher"></div>
          <div class="field-control"><label>Дата</label><input id="date"></div>
          <div class="field-control"><label>Объём</label><input id="extent"></div>
          <div class="field-control full"><label>Примечание</label><textarea id="notes"></textarea></div>
        </div>
      </div>
      <div class="modal-actions"><button class="service-button" type="button" id="cancel-edit">Отменить</button><button class="service-button accept-button" type="button" id="accept-edit">Принять</button></div>
    </div>
  </div>
  <script>
    var stateUrl = "../state/";
    var modal = document.getElementById("edit-modal");
    var activeItem = null;
    var activeSelected = "";
    var dirty = false;
    var fields = {{
      sourceType: document.getElementById("source-type"), authors: document.getElementById("authors"), title: document.getElementById("title"),
      subtitle: document.getElementById("subtitle"), responsibility: document.getElementById("responsibility"), place: document.getElementById("place"),
      publisher: document.getElementById("publisher"), date: document.getElementById("date"), extent: document.getElementById("extent"), notes: document.getElementById("notes")
    }};
    function csrfToken() {{ var c=document.cookie.split(";").map(function(i){{return i.trim();}}); for (var i=0;i<c.length;i++) {{ if (c[i].indexOf("bibliobon_data_editor_csrftoken=")===0 || c[i].indexOf("csrftoken=")===0) return decodeURIComponent(c[i].split("=").slice(1).join("=")); }} return ""; }}
    function itemId(item) {{ return item.dataset.reviewId || item.dataset.candidateId || ""; }}
    function fieldValues() {{ var v={{}}; Object.keys(fields).forEach(function(k){{ v[k]=fields[k].value; }}); return v; }}
    function postReviewState(payload) {{ return fetch(stateUrl, {{ method:"POST", headers:{{"Content-Type":"application/json","X-CSRFToken":csrfToken()}}, body:JSON.stringify(payload) }}).then(function(r){{ if(!r.ok) throw new Error("save failed"); return r; }}).catch(function(){{ alert("Не удалось сохранить правку. Обновите страницу и попробуйте ещё раз."); }}); }}
    function markChanged(item) {{ var s=item.querySelector(".status-box"); if(s) {{ s.classList.add("is-changed"); s.textContent="Изменено"; }} }}
    function restoreState() {{ fetch(stateUrl).then(function(r){{return r.ok?r.json():null;}}).then(function(state){{ if(!state||!state.stage2)return; document.querySelectorAll(".bibliography-item").forEach(function(item){{ if(state.stage2[itemId(item)]) markChanged(item); }}); }}).catch(function(){{}}); }}
    document.addEventListener("click", function(event) {{
      var status = event.target.closest(".status-box");
      if (status) {{
        var item = status.closest(".bibliography-item");
        if (status.classList.contains("is-changed")) {{
          status.classList.remove("is-changed"); status.textContent = "Без изменений";
          postReviewState({{stage:"stage2", action:"clear", item_id:itemId(item), candidate_id:item.dataset.candidateId||"", editor_source_id:item.dataset.editorSourceId||""}});
        }}
        return;
      }}
      var row = event.target.closest(".citation-row");
      if (!row) return;
      activeItem = row.closest(".bibliography-item");
      activeSelected = row.classList.contains("candidate-citation") ? "new" : "old";
      activeItem.querySelectorAll(".citation-row").forEach(function(r){{ r.classList.remove("is-selected-new","is-selected-old"); }});
      row.classList.add(activeSelected === "new" ? "is-selected-new" : "is-selected-old");
      Object.keys(fields).forEach(function(k){{ fields[k].value = row.dataset[k] || ""; }});
      dirty = false; modal.classList.add("is-open"); modal.setAttribute("aria-hidden","false");
    }});
    Object.keys(fields).forEach(function(k){{ fields[k].addEventListener("input", function(){{ dirty=true; }}); fields[k].addEventListener("change", function(){{ dirty=true; }}); }});
    document.getElementById("cancel-edit").addEventListener("click", function(){{ modal.classList.remove("is-open"); }});
    document.getElementById("accept-edit").addEventListener("click", function(){{ if(activeItem && dirty) {{ markChanged(activeItem); postReviewState({{stage:"stage2", action:"save", item_id:itemId(activeItem), selected:activeSelected, candidate_id:activeItem.dataset.candidateId||"", editor_source_id:activeItem.dataset.editorSourceId||"", values:fieldValues()}}); }} modal.classList.remove("is-open"); }});
    document.addEventListener("keydown", function(event){{ if(!modal.classList.contains("is-open"))return; if(event.key==="Escape"){{ event.preventDefault(); modal.classList.remove("is-open"); }} if(event.key==="Enter" && event.target.tagName!=="TEXTAREA"){{ event.preventDefault(); document.getElementById("accept-edit").click(); }} }});
    restoreState();
  </script>
</body>
</html>"""
    path.write_text(doc, encoding="utf-8")


def write_author_review_html(path: Path, run_dir: Path, author_resolution_rows: list[dict[str, Any]]) -> None:
    ready_rows = [row for row in author_resolution_rows if row.get("status") == "ready"]
    review_rows = [row for row in author_resolution_rows if row.get("status") != "ready"]
    ready_items = "\n".join(render_author_stage_group(row) for row in ready_rows)
    review_items = "\n".join(render_author_stage_group(row) for row in review_rows)
    doc = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Авторы {html.escape(run_dir.name)}</title>
  <style>
    :root {{ --catalog-bg:#f4f4f4; --catalog-surface:#fff; --catalog-border:#ddd; --catalog-ink:#242424; --catalog-muted:#6f6f6f; --catalog-accent:#be373b; --font-family:"San Francisco","Roboto",Arial,sans-serif; --heading-font-family:"Scada",var(--font-family); }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; min-height:100vh; background:var(--catalog-bg); color:var(--catalog-ink); font:16px/1.35 var(--font-family); }}
    .wrap {{ width:min(1230px,calc(100% - 32px)); min-height:100vh; margin:0 auto; padding:26px 30px 56px; background:var(--catalog-surface); }}
    h1 {{ margin:0; font-family:var(--heading-font-family); font-size:42px; font-weight:400; line-height:1.12; text-transform:uppercase; }}
    h2 {{ margin:0; font-family:var(--heading-font-family); font-size:24px; font-weight:400; }}
    .lede {{ max-width:860px; margin:12px 0 0; color:var(--catalog-muted); font-size:17px; }}
    .review-nav,.stage-actions {{ display:flex; flex-wrap:wrap; gap:8px; margin:18px 0 22px; }}
    .review-nav {{ padding-bottom:18px; border-bottom:1px solid var(--catalog-border); }}
    .stage-actions {{ justify-content:flex-end; }}
    .review-nav a,.stage-link {{ min-height:34px; padding:7px 12px; border:1px solid var(--catalog-border); color:var(--catalog-ink); background:#fff; font-weight:700; text-decoration:none; }}
    .stage-link {{ border-color:var(--catalog-ink); color:#fff; background:var(--catalog-ink); }}
    .review-section {{ margin-top:24px; border:1px solid var(--catalog-border); background:#fff; }}
    .results-header {{ padding:16px 18px; border-bottom:1px solid var(--catalog-border); color:var(--catalog-muted); }}
    .bibliography-list {{ margin:0; padding:0; list-style:none; }}
    .bibliography-item {{ padding:14px 18px 15px; border-bottom:1px solid var(--catalog-border); }}
    .bibliography-item:last-child {{ border-bottom:0; }}
    .pair-layout {{ display:grid; grid-template-columns:minmax(0,1fr) 260px; gap:16px; align-items:stretch; }}
    .citation-row {{ padding:2px 4px; color:var(--catalog-ink); font-size:10pt; line-height:1.22; white-space:nowrap; overflow:hidden; text-overflow:clip; }}
    .candidate-citation {{ color:var(--catalog-muted); }}
    .record-prefix {{ color:var(--catalog-ink); font-weight:800; }}
    .field {{ border-bottom:1px solid #9aa0a6; text-decoration:underline; text-decoration-color:#9aa0a6; text-underline-offset:3px; }}
    .field-note,.pair-meta {{ margin-top:8px; color:var(--catalog-muted); font-size:13px; }}
    .pair-actions {{ display:grid; grid-template-columns:1fr; gap:8px; align-content:center; }}
    .choice-panel {{ display:none; margin-top:10px; padding:10px; border:1px solid var(--catalog-border); background:#fff; }}
    .choice-panel.is-open {{ display:block; }}
    .choice-search {{ width:100%; min-height:32px; margin-bottom:8px; padding:6px 8px; border:1px solid var(--catalog-border); font:inherit; }}
    .choice-list {{ display:grid; gap:6px; max-height:230px; overflow:auto; }}
    .service-button {{ min-height:34px; padding:6px 10px; border:1px solid var(--catalog-border); border-radius:0; color:var(--catalog-muted); background:#fff; cursor:pointer; font:inherit; font-weight:700; text-align:center; }}
    .service-button.is-selected {{ border-color:#188038; color:#188038; }}
    .service-button.is-danger {{ border-color:var(--catalog-accent); color:var(--catalog-accent); }}
    .choice-button {{ text-align:left; font-weight:400; }}
    .empty-state {{ padding:18px; color:var(--catalog-muted); }}
    code {{ background:#f1f3f4; padding:1px 4px; }}
  </style>
</head>
<body>
  <main class="wrap">
    <h1>Авторы</h1>
    <p class="lede">Run <code>{html.escape(run_dir.name)}</code>. Здесь подтверждается, какого автора из базы использовать, или что автора нужно создать. Запись в базу на этом экране не выполняется.</p>
    <nav class="review-nav">
      <a href="../review_containers.html/">К контейнерам</a>
      <a href="#review">Нужно подтвердить: {len(review_rows)}</a>
      <a href="#ready">Найдены автоматически: {len(ready_rows)}</a>
    </nav>
    <div class="stage-actions"><a class="stage-link" href="../review_report.html/">Перейти к разбору записей</a></div>
    <section id="review" class="review-section">
      <div class="results-header"><h2>Нужно подтвердить</h2></div>
      <ul class="bibliography-list">{review_items or '<li class="empty-state">Нет авторов, требующих ручного решения.</li>'}</ul>
    </section>
    <section id="ready" class="review-section">
      <div class="results-header"><h2>Найдены автоматически</h2></div>
      <ul class="bibliography-list">{ready_items or '<li class="empty-state">Нет автоматически найденных авторов.</li>'}</ul>
    </section>
  </main>
  <script>
    var stateUrl = "../state/";
    function csrfToken() {{ var cookies=document.cookie.split(";").map(function(i){{return i.trim();}}); for(var i=0;i<cookies.length;i++){{ if(cookies[i].indexOf("bibliobon_data_editor_csrftoken=")===0||cookies[i].indexOf("csrftoken=")===0) return decodeURIComponent(cookies[i].split("=").slice(1).join("=")); }} return ""; }}
    function postReviewState(payload) {{ return fetch(stateUrl, {{method:"POST", headers:{{"Content-Type":"application/json","X-CSRFToken":csrfToken()}}, body:JSON.stringify(payload)}}).then(function(response){{ if(!response.ok) throw new Error("state save failed"); return response; }}).catch(function(){{ alert("Не удалось сохранить решение. Обновите страницу и попробуйте ещё раз."); }}); }}
    function markDecision(button, skipSave) {{
      var item = button.closest(".bibliography-item");
      item.querySelectorAll(".service-button").forEach(function(candidate){{ candidate.classList.remove("is-selected"); }});
      button.classList.add("is-selected");
      if(skipSave) return;
      postReviewState({{stage:"stage_authors", item_id:button.dataset.itemId||"", decision:button.dataset.decision||"", candidate_author:button.dataset.candidateAuthor||"", author_id:button.dataset.authorId||""}});
    }}
    function restoreState() {{ fetch(stateUrl).then(function(r){{return r.ok?r.json():null;}}).then(function(state){{ if(!state||!state.stage_authors)return; Object.keys(state.stage_authors).forEach(function(itemId){{ var saved=state.stage_authors[itemId]; var selector='.service-button[data-item-id="'+CSS.escape(itemId)+'"][data-decision="'+CSS.escape(saved.decision||"")+'"]'; var buttons=Array.prototype.slice.call(document.querySelectorAll(selector)); var button=buttons.find(function(candidate){{ return !saved.author_id || candidate.dataset.authorId===saved.author_id; }})||buttons[0]; if(button) markDecision(button,true); }}); }}).catch(function(){{}}); }}
    document.addEventListener("click", function(event) {{
      var panelButton = event.target.closest(".service-button[data-open-panel]");
      if(panelButton) {{ var panel=panelButton.closest(".bibliography-item").querySelector(".choice-panel"); if(panel) panel.classList.toggle("is-open"); return; }}
      var button = event.target.closest(".service-button[data-decision]");
      if(button) markDecision(button);
    }});
    document.addEventListener("input", function(event) {{ var search=event.target.closest(".choice-search"); if(!search)return; var query=search.value.toLowerCase(); var panel=search.closest(".choice-panel"); panel.querySelectorAll(".choice-button").forEach(function(button){{ button.style.display=button.textContent.toLowerCase().indexOf(query)===-1?"none":""; }}); }});
    restoreState();
  </script>
</body>
</html>"""
    path.write_text(doc, encoding="utf-8")


def render_author_stage_group(row: dict[str, Any]) -> str:
    target_bits = []
    if row.get("best_author_display_name"):
        target_bits.append(row.get("best_author_display_name"))
    if row.get("best_author_dates"):
        target_bits.append(row.get("best_author_dates"))
    if row.get("author_score"):
        target_bits.append(f"score: {row.get('author_score')}")
    examples = render_author_source_examples(row)
    choices = render_author_choice_panel(row)
    return f"""
<li class="bibliography-item" data-author-key="{html.escape(str(row.get('author_key') or ''))}">
  <div class="pair-layout">
    <div>
      <div class="citation-row candidate-citation"><span class="record-prefix">Автор из источника:</span> <span class="field">{html.escape(str(row.get('candidate_author') or ''))}</span></div>
      <div class="citation-row"><span class="record-prefix">Автор в базе:</span> {html.escape('; '.join(target_bits) if target_bits else 'уверенный автор не найден')}</div>
      <div class="field-note">{html.escape(str(row.get('review_note') or ''))}</div>
      {examples}
      {choices}
    </div>
    <div class="pair-actions">{author_action_buttons(row)}</div>
  </div>
</li>
"""


def render_author_source_examples(row: dict[str, Any]) -> str:
    rows = []
    for value in [row.get("example_1"), row.get("example_2")]:
        if value:
            rows.append(f'<div class="field-note"><span class="record-prefix">Источник:</span> {html.escape(str(value))}</div>')
    return "\n".join(rows)


def render_author_choice_panel(row: dict[str, Any]) -> str:
    buttons = []
    base_attrs = author_button_attrs(row)
    for item in row.get("author_options") or []:
        label = ", ".join(str(part) for part in [item.get("display_name"), item.get("person_dates")] if part)
        buttons.append(
            '<button class="service-button choice-button" type="button" '
            f'{base_attrs} data-decision="use_existing_author" data-author-id="{html.escape(str(item.get("author_id") or ""))}">'
            f'{html.escape(label)} <span class="pair-meta">author_score: {html.escape(str(item.get("score") or ""))}</span></button>'
        )
    if not buttons:
        return ""
    return f"""
<div class="choice-panel">
  <input class="choice-search" type="search" placeholder="Поиск по авторам">
  <div class="choice-list">{''.join(buttons)}</div>
</div>
"""


def author_action_buttons(row: dict[str, Any]) -> str:
    action = row.get("action")
    attrs = author_button_attrs(row)
    if action == "link_existing_author":
        return f'<button class="service-button" type="button" {attrs} data-decision="use_existing_author" data-author-id="{html.escape(str(row.get("best_author_id") or ""))}">Подтвердить автора</button>'
    if action == "confirm_existing_author":
        return "\n".join(
            [
                f'<button class="service-button" type="button" {attrs} data-decision="use_existing_author" data-author-id="{html.escape(str(row.get("best_author_id") or ""))}">Подтвердить автора</button>',
                '<button class="service-button" type="button" data-open-panel="1">Выбрать другого</button>',
                f'<button class="service-button is-danger" type="button" {attrs} data-decision="create_author">Создать автора</button>',
            ]
        )
    return "\n".join(
        [
            '<button class="service-button" type="button" data-open-panel="1">Выбрать автора</button>',
            f'<button class="service-button is-danger" type="button" {attrs} data-decision="create_author">Создать автора</button>',
        ]
    )


def author_button_attrs(row: dict[str, Any]) -> str:
    return " ".join(
        [
            f'data-item-id="{html.escape(str(row.get("author_key") or ""))}"',
            f'data-candidate-author="{html.escape(str(row.get("candidate_author") or ""), quote=True)}"',
        ]
    )


def render_stage2_pair(pair: dict[str, Any]) -> str:
    candidate = pair["candidate"]
    editor = pair["editor"]
    candidate_fields = display_fields_from_candidate(candidate)
    editor_fields = display_fields_from_editor(editor)
    diff_fields = changed_display_fields(candidate_fields, editor_fields)
    review_id = f"stage2:{candidate.get('candidate_id') or ''}:{editor.get('source_id') or ''}"
    return f"""
<li class="bibliography-item" data-review-id="{html.escape(review_id)}" data-candidate-id="{html.escape(str(candidate.get('candidate_id') or ''))}" data-editor-source-id="{html.escape(str(editor.get('source_id') or ''))}">
  <div class="pair-layout">
    <div>
      <div class="citation-row candidate-citation" {stage2_data_attrs(candidate_fields)}>{render_citation_fields(candidate_fields, diff_fields, boundary=False)}</div>
      <div class="citation-row" {stage2_data_attrs(editor_fields)}>{render_citation_fields(editor_fields, diff_fields, boundary=False)}</div>
      {render_field_note(candidate_fields, editor_fields, diff_fields)}
      <div class="pair-meta"><span>match_score: <strong>{pair['match_score']:.2f}</strong></span></div>
    </div>
    <div class="pair-actions"><div class="service-button status-box">Без изменений</div></div>
  </div>
</li>"""


def render_stage2_new(candidate: dict[str, Any]) -> str:
    fields = display_fields_from_candidate(candidate)
    review_id = f"stage2:{candidate.get('candidate_id') or ''}:new"
    return f"""
<li class="bibliography-item" data-review-id="{html.escape(review_id)}" data-candidate-id="{html.escape(str(candidate.get('candidate_id') or ''))}">
  <div class="pair-layout">
    <div>
      <div class="citation-row candidate-citation" {stage2_data_attrs(fields)}>{render_citation_fields(fields, set(), boundary=False)}</div>
      <div class="field-note">Новая запись без сильного совпадения.</div>
    </div>
    <div class="pair-actions"><div class="service-button status-box">Без изменений</div></div>
  </div>
</li>"""


def stage2_data_attrs(fields: dict[str, str]) -> str:
    mapping = {
        "sourceType": fields.get("source_type") or "",
        "authors": fields.get("authors") or "",
        "title": fields.get("title") or "",
        "subtitle": fields.get("subtitle") or "",
        "responsibility": fields.get("responsibility_statement") or "",
        "place": fields.get("publication_place") or "",
        "publisher": fields.get("publisher") or "",
        "date": fields.get("publication_date") or "",
        "extent": fields.get("extent") or "",
        "notes": fields.get("notes") or "",
    }
    return " ".join(f'data-{camel_to_kebab(key)}="{html.escape(str(value), quote=True)}"' for key, value in mapping.items())


def camel_to_kebab(value: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"-\1", value).lower()


STAGE3_FIELD_LABELS = {
    "raw_publication_details": "raw-запись источника",
    "public_review": "аннотация",
}


def stage3_field_label(field_name: str) -> str:
    return STAGE3_FIELD_LABELS.get(field_name, field_name)


def render_stage3_candidate_items(candidates: list[dict[str, Any]], proposed_rows: list[dict[str, Any]] | None = None) -> str:
    proposed_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for row in proposed_rows or []:
        proposed_by_candidate.setdefault(row.get("candidate_id") or "", []).append(row)
    items = []
    for candidate in candidates:
        fields = display_fields_from_candidate(candidate)
        notes = []
        for row in proposed_by_candidate.get(candidate.get("candidate_id") or "", []):
            notes.append(
                f"{stage3_field_label(row.get('field') or '')}: "
                f"{row.get('editor_value') or '[пусто]'} -> {row.get('candidate_value')}"
            )
        note_html = f'<div class="field-note">{html.escape("; ".join(notes))}</div>' if notes else ""
        items.append(
            f"""
<li class="bibliography-item">
  <div class="citation-row">{render_citation_fields(fields, set(), boundary=False)}</div>
  {note_html}
</li>
"""
        )
    return "\n".join(items)


def render_stage3_existing_items(review_pairs: list[dict[str, Any]], excluded_candidate_ids: set[str] | None = None) -> str:
    excluded_candidate_ids = excluded_candidate_ids or set()
    seen: set[str] = set()
    items = []
    for pair in review_pairs:
        candidate = pair["candidate"]
        candidate_id = candidate.get("candidate_id")
        if candidate_id in seen or candidate_id in excluded_candidate_ids:
            continue
        seen.add(candidate_id)
        fields = display_fields_from_candidate(candidate)
        note = "Запись уже есть в базе; отличия только служебные или не требуют внесения."
        if pair.get("match_score") is not None:
            note = f"match_score: {pair['match_score']:.2f}. {note}"
        items.append(
            f"""
<li class="bibliography-item">
  <div class="citation-row">{render_citation_fields(fields, set(), boundary=False)}</div>
  <div class="field-note">{html.escape(note)}</div>
</li>
"""
        )
    return "\n".join(items)


def render_stage3_container_items(rows: list[dict[str, Any]]) -> str:
    items = []
    for row in rows:
        kind = "Журнал" if row.get("container_kind") == "periodical_issue" else "Сборник"
        bits = [row.get("container_title") or ""]
        if row.get("issue_year"):
            bits.append(str(row.get("issue_year")))
        if row.get("issue_number"):
            bits.append(f"№ {row.get('issue_number')}")
        if row.get("volume") and str(row.get("volume")).strip(" .") != "-":
            bits.append(f"т. {row.get('volume')}")
        items.append(
            f"""
<li class="bibliography-item">
  <div class="citation-row"><span class="record-prefix">{html.escape(kind)}:</span> {html.escape(', '.join(part for part in bits if part))}</div>
  <div class="field-note">{html.escape(str(row.get('review_note') or row.get('action') or ''))}</div>
</li>
"""
        )
    return "\n".join(items)


def render_stage3_author_items(rows: list[dict[str, Any]]) -> str:
    items = []
    for row in rows:
        bits = [row.get("candidate_author") or ""]
        if row.get("candidate_heading_name") and row.get("candidate_heading_name") != row.get("candidate_author"):
            bits.append(row.get("candidate_heading_name"))
        items.append(
            f"""
<li class="bibliography-item">
  <div class="citation-row"><span class="record-prefix">Автор:</span> {html.escape(', '.join(part for part in bits if part))}</div>
  <div class="field-note">{html.escape(str(row.get('review_note') or row.get('action') or ''))}</div>
</li>
"""
        )
    return "\n".join(items)


REVIEW_FIELDS = [
    ("authors", "authors"),
    ("title", "title"),
    ("subtitle", "subtitle"),
    ("responsibility_statement", "responsibility"),
    ("publication_place", "place"),
    ("publisher", "publisher"),
    ("publication_date", "date"),
    ("extent", "extent"),
    ("notes", "notes"),
]


def render_match_pair(pair: dict[str, Any], container_by_raw_record: dict[str, dict[str, Any]] | None = None) -> str:
    candidate = pair["candidate"]
    editor = pair["editor"]
    score = pair["match_score"]
    candidate_fields = display_fields_from_candidate(candidate)
    editor_fields = display_fields_from_editor(editor)
    diff_fields = changed_display_fields(candidate_fields, editor_fields)
    field_note = render_field_note(candidate_fields, editor_fields, diff_fields)
    diagnostic_note = render_match_diagnostics(pair, diff_fields)
    container_note = render_container_binding_note(candidate, container_by_raw_record or {})
    action_html = (
        '<button class="service-button split-button" type="button">Разделить</button>'
        if score < 1
        else ""
    )
    score_meta = ""
    weak_meta = "<span>слабый кандидат</span>" if pair.get("weak_match") else ""
    return f"""
<li class="bibliography-item" data-candidate-id="{html.escape(str(candidate.get('candidate_id') or ''))}" data-editor-source-id="{html.escape(str(editor.get('source_id') or ''))}" data-match-score="{score:.2f}">
  <div class="pair-layout">
    <div>
      <div class="citation-row candidate-citation">{render_citation_fields(candidate_fields, diff_fields, boundary=False)}</div>
      <div class="citation-row">{render_citation_fields(editor_fields, diff_fields, boundary=False)}</div>
      {field_note}
      {container_note}
      {diagnostic_note}
      <div class="pair-meta">
        {score_meta}
        {weak_meta}
      </div>
    </div>
    <div class="pair-actions">
      {action_html}
    </div>
  </div>
</li>
"""


def render_new_candidate(candidate: dict[str, Any], container_by_raw_record: dict[str, dict[str, Any]] | None = None) -> str:
    fields = display_fields_from_candidate(candidate)
    container_note = render_container_binding_note(candidate, container_by_raw_record or {})
    return f"""
<li class="bibliography-item new-record" data-candidate-id="{html.escape(str(candidate.get('candidate_id') or ''))}">
  <div class="citation-row candidate-citation">{render_citation_fields(fields, set(), boundary=True)}</div>
  {container_note}
  <div class="pair-meta">
    <span>confidence: {html.escape(str(candidate.get('confidence') or ''))}</span>
  </div>
</li>
"""


def group_container_resolution_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("container_kind") == "periodical_issue":
            group_id = row.get("best_periodical_id") or f"title:{normalize_container_title(row.get('container_title'))}"
            group_key = f"periodical:{group_id}"
            group = groups.setdefault(
                group_key,
                {
                    "group_key": group_key,
                    "container_kind": "periodical_issue",
                    "container_title": row.get("best_periodical_title") or row.get("container_title") or "",
                    "best_periodical_id": row.get("best_periodical_id") or "",
                    "periodical_score": row.get("periodical_score") or "",
                    "items_by_key": {},
                },
            )
            issue_key = row.get("best_issue_id") or "issue:{year}:{number}:{volume}:{part}".format(
                year=row.get("issue_year") or "",
                number=normalize_compare_text(row.get("issue_number")) or "",
                volume=normalize_compare_text(row.get("volume")) or "",
                part=normalize_compare_text(row.get("part_number")) or "",
            )
            merge_container_group_item(group, issue_key, row)
        else:
            group_id = row.get("best_collection_id") or f"title:{normalize_container_title(row.get('container_title'))}"
            group_key = f"collection:{group_id}"
            group = groups.setdefault(
                group_key,
                {
                    "group_key": group_key,
                    "container_kind": "collection_work",
                    "container_title": row.get("best_collection_title") or row.get("container_title") or "",
                    "best_collection_id": row.get("best_collection_id") or "",
                    "collection_score": row.get("collection_score") or "",
                    "items_by_key": {},
                },
            )
            merge_container_group_item(group, group_key, row)

    grouped = []
    for group in groups.values():
        items = list(group.pop("items_by_key").values())
        items.sort(key=container_group_item_sort_key)
        group["items"] = items
        group["candidate_ids"] = sorted({cid for item in items for cid in item.get("candidate_ids", [])})
        group["raw_record_count"] = sum(len(item.get("raw_record_ids", [])) for item in items)
        group["status"] = "ready" if all(item.get("status") == "ready" for item in items) else "needs_confirmation"
        grouped.append(group)
    grouped.sort(key=lambda item: (item.get("status") != "needs_confirmation", normalize_container_title(item.get("container_title"))))
    return grouped


def load_raw_record_examples(run_dir: Path) -> dict[str, dict[str, Any]]:
    path = run_dir / "raw_records.jsonl"
    if not path.exists():
        return {}
    examples: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(path):
        raw_record_id = row.get("raw_record_id")
        if raw_record_id:
            examples[str(raw_record_id)] = row
    return examples


def attach_source_examples_to_container_groups(groups: list[dict[str, Any]], raw_records: dict[str, dict[str, Any]]) -> None:
    for group in groups:
        group_examples = []
        for item in group.get("items", []):
            examples = []
            for raw_record_id in item.get("raw_record_ids") or []:
                raw = raw_records.get(raw_record_id)
                if not raw:
                    continue
                examples.append(
                    {
                        "source_record_index": raw.get("source_record_index"),
                        "raw_text": raw.get("raw_text") or raw.get("normalized_text") or "",
                    }
                )
            item["source_examples"] = examples[:2]
            group_examples.extend(examples)
        group["source_examples"] = group_examples[:2]


def merge_container_group_item(group: dict[str, Any], item_key: str, row: dict[str, Any]) -> None:
    item = group["items_by_key"].setdefault(
        item_key,
        {
            **row,
            "group_item_key": item_key,
            "candidate_ids": [],
            "raw_record_ids": [],
        },
    )
    if row.get("candidate_id"):
        item["candidate_ids"].append(row["candidate_id"])
    if row.get("raw_record_id"):
        item["raw_record_ids"].append(row["raw_record_id"])
    item["candidate_ids"] = sorted(set(item["candidate_ids"]))
    item["raw_record_ids"] = sorted(set(item["raw_record_ids"]))


def container_group_item_sort_key(item: dict[str, Any]) -> tuple[int, str, str, str]:
    year = item.get("issue_year") or item.get("best_issue_year") or 0
    try:
        year_value = int(year)
    except (TypeError, ValueError):
        year_value = 0
    return (year_value, str(item.get("issue_number") or item.get("best_issue_number") or ""), str(item.get("volume") or ""), str(item.get("part_number") or ""))


def render_container_binding_note(candidate: dict[str, Any], container_by_raw_record: dict[str, dict[str, Any]]) -> str:
    if candidate.get("candidate_part") != "article":
        return ""
    row = container_by_raw_record.get(candidate.get("raw_record_id"))
    if not row:
        return ""
    source_bits = []
    if row.get("container_title"):
        source_bits.append(str(row.get("container_title")))
    if row.get("issue_year"):
        source_bits.append(str(row.get("issue_year")))
    if row.get("issue_number"):
        source_bits.append(f"№ {row.get('issue_number')}")
    target = row.get("best_issue_id") or row.get("best_collection_id") or row.get("best_periodical_id") or "не подтвержден"
    return f'<div class="field-note">Контейнер: {html.escape(", ".join(source_bits))} → {html.escape(str(target))}</div>'


def render_container_resolution(row: dict[str, Any]) -> str:
    kind_label = "Журнал/выпуск" if row.get("container_kind") == "periodical_issue" else "Сборник"
    action_labels = {
        "link_existing_issue": "Найден выпуск",
        "confirm_existing_issue": "Подтвердить выпуск",
        "confirm_create_issue": "Создать выпуск?",
        "choose_periodical": "Выбрать журнал",
        "confirm_create_periodical": "Создать журнал?",
        "link_existing_collection": "Найден сборник",
        "confirm_existing_collection": "Подтвердить сборник",
        "confirm_create_collection": "Создать сборник?",
    }
    action = row.get("action") or ""
    target_bits = []
    if row.get("best_periodical_title"):
        target_bits.append(f"журнал: {row.get('best_periodical_title')} ({row.get('periodical_score')})")
    if row.get("best_issue_id"):
        issue_label = ", ".join(str(part) for part in [row.get("best_issue_year"), f"№ {row.get('best_issue_number')}" if row.get("best_issue_number") else ""] if part)
        target_bits.append(f"выпуск: {issue_label or row.get('best_issue_id')} ({row.get('issue_score')})")
    if row.get("best_collection_title"):
        target_bits.append(f"сборник: {row.get('best_collection_title')} ({row.get('collection_score')})")
    source_bits = []
    if row.get("issue_year"):
        source_bits.append(str(row.get("issue_year")))
    if row.get("issue_number"):
        source_bits.append(f"№ {row.get('issue_number')}")
    if row.get("volume") and str(row.get("volume")).strip(" .") != "-":
        source_bits.append(f"т. {row.get('volume')}")
    if row.get("part_number") and str(row.get("part_number")).strip(" .") != "-":
        source_bits.append(f"ч. {row.get('part_number')}")
    return f"""
<li class="bibliography-item" data-candidate-id="{html.escape(str(row.get('candidate_id') or ''))}">
  <div class="pair-layout">
    <div>
      <div class="citation-row candidate-citation"><span class="record-prefix">{html.escape(kind_label)}:</span> <span class="field">{html.escape(str(row.get('container_title') or ''))}</span>{' - ' + html.escape(', '.join(source_bits)) if source_bits else ''}</div>
      <div class="citation-row">{html.escape('; '.join(target_bits) if target_bits else 'В базе не найден уверенный контейнер')}</div>
      <div class="field-note">{html.escape(str(row.get('review_note') or ''))}</div>
    </div>
    <div class="pair-actions">
      <div class="service-button">{html.escape(action_labels.get(action, action))}</div>
    </div>
  </div>
</li>
"""


def render_container_stage0(row: dict[str, Any]) -> str:
    kind_label = "Журнал/выпуск" if row.get("container_kind") == "periodical_issue" else "Сборник"
    target_bits = []
    if row.get("best_periodical_title"):
        target_bits.append(f"журнал: {row.get('best_periodical_title')} ({row.get('periodical_score')})")
    if row.get("best_issue_id"):
        issue_label = ", ".join(str(part) for part in [row.get("best_issue_year"), f"№ {row.get('best_issue_number')}" if row.get("best_issue_number") else ""] if part)
        target_bits.append(f"выпуск: {issue_label or row.get('best_issue_id')} ({row.get('issue_score')})")
    if row.get("best_collection_title"):
        target_bits.append(f"сборник: {row.get('best_collection_title')} ({row.get('collection_score')})")

    source_bits = []
    if row.get("issue_year"):
        source_bits.append(str(row.get("issue_year")))
    if row.get("issue_number"):
        source_bits.append(f"№ {row.get('issue_number')}")
    if row.get("volume") and str(row.get("volume")).strip(" .") != "-":
        source_bits.append(f"т. {row.get('volume')}")
    if row.get("part_number") and str(row.get("part_number")).strip(" .") != "-":
        source_bits.append(f"ч. {row.get('part_number')}")

    actions = stage0_action_buttons(row)
    choices = render_stage0_choice_panel(row)
    return f"""
<li class="bibliography-item"
    data-candidate-id="{html.escape(str(row.get('candidate_id') or ''))}"
    data-container-kind="{html.escape(str(row.get('container_kind') or ''))}"
    data-container-action="{html.escape(str(row.get('action') or ''))}"
    data-periodical-id="{html.escape(str(row.get('best_periodical_id') or ''))}"
    data-issue-id="{html.escape(str(row.get('best_issue_id') or ''))}"
    data-collection-id="{html.escape(str(row.get('best_collection_id') or ''))}">
  <div class="pair-layout">
    <div>
      <div class="citation-row candidate-citation"><span class="record-prefix">Из источника:</span> <span class="field">{html.escape(str(row.get('container_title') or ''))}</span>{' - ' + html.escape(', '.join(source_bits)) if source_bits else ''}</div>
      <div class="citation-row"><span class="record-prefix">{html.escape(kind_label)} в базе:</span> {html.escape('; '.join(target_bits) if target_bits else 'уверенный контейнер не найден')}</div>
      <div class="field-note">{html.escape(str(row.get('review_note') or ''))}</div>
      <div class="pair-meta">
        <span>{html.escape(str(row.get('action') or ''))}</span>
      </div>
      {choices}
    </div>
    <div class="pair-actions">
      {actions}
    </div>
  </div>
</li>
"""


def render_container_stage0_group(group: dict[str, Any]) -> str:
    kind_label = "Журнал" if group.get("container_kind") == "periodical_issue" else "Сборник"
    item_rows = "\n".join(render_container_stage0_group_item(group, item) for item in group.get("items", []))
    title = group.get("container_title") or ""
    meta_bits = []
    if group.get("container_kind") == "periodical_issue" and group.get("best_periodical_id"):
        meta_bits.append(str(group.get("best_periodical_id")))
    source_examples = render_container_source_examples(group.get("source_examples") or [])
    return f"""
<li class="bibliography-item"
    data-candidate-id="{html.escape(str(group.get('group_key') or ''))}"
    data-container-kind="{html.escape(str(group.get('container_kind') or ''))}"
    data-periodical-id="{html.escape(str(group.get('best_periodical_id') or ''))}"
    data-collection-id="{html.escape(str(group.get('best_collection_id') or ''))}">
  <div>
    <div class="citation-row"><span class="record-prefix">{html.escape(kind_label)}:</span> <span class="field">{html.escape(str(title))}</span></div>
    {f'<div class="pair-meta">{html.escape("; ".join(meta_bits))}</div>' if meta_bits else ''}
    {source_examples}
    <div class="issue-list">
      {item_rows}
    </div>
  </div>
</li>
"""


def render_container_stage0_group_item(group: dict[str, Any], item: dict[str, Any]) -> str:
    if group.get("container_kind") == "periodical_issue":
        source_bits = []
        if item.get("issue_year"):
            source_bits.append(str(item.get("issue_year")))
        if item.get("issue_number"):
            source_bits.append(f"№ {item.get('issue_number')}")
        if item.get("volume") and str(item.get("volume")).strip(" .") != "-":
            source_bits.append(f"т. {item.get('volume')}")
        if item.get("part_number") and str(item.get("part_number")).strip(" .") != "-":
            source_bits.append(f"ч. {item.get('part_number')}")
        target_bits = []
        if item.get("best_issue_id"):
            target_bits.append(str(item.get("best_issue_id")))
        if item.get("issue_score"):
            target_bits.append(f"issue_score: {item.get('issue_score')}")
        label = "Выпуск: " + (", ".join(source_bits) if source_bits else "без номера")
        target = "; ".join(target_bits) if target_bits else "выпуск не выбран"
    else:
        label = "Сборник: " + str(item.get("container_title") or group.get("container_title") or "")
        target = ""
    choices = render_stage0_choice_panel(item)
    return f"""
<div class="issue-item">
  <div>
    <div class="citation-row candidate-citation"><span class="record-prefix">{html.escape(label)}</span></div>
    {f'<div class="citation-row">{html.escape(target)}</div>' if target else ''}
    <div class="field-note">{html.escape(str(item.get('review_note') or ''))}</div>
    {choices}
  </div>
  <div class="pair-actions">
    {stage0_action_buttons(item)}
  </div>
</div>
"""


def render_container_source_examples(examples: list[dict[str, Any]]) -> str:
    if not examples:
        return ""
    rows = []
    for example in examples[:2]:
        index = example.get("source_record_index")
        prefix = f"{index}. " if index else ""
        rows.append(
            f'<div class="field-note"><span class="record-prefix">Источник:</span> '
            f'{html.escape(prefix + str(example.get("raw_text") or ""))}</div>'
        )
    return "\n".join(rows)


def render_stage0_choice_panel(row: dict[str, Any]) -> str:
    buttons: list[str] = []
    base_attrs = stage0_button_attrs(row)
    for item in row.get("issue_options") or []:
        label_parts = [
            item.get("periodical_title") or row.get("best_periodical_title") or "",
            str(item.get("year") or ""),
            f"№ {item.get('issue_number')}" if item.get("issue_number") else "",
            f"т. {item.get('volume')}" if item.get("volume") else "",
            f"ч. {item.get('part_number')}" if item.get("part_number") else "",
        ]
        label = ", ".join(part for part in label_parts if part)
        buttons.append(
            '<button class="service-button choice-button" type="button" '
            f'{base_attrs} data-decision="use_existing_issue" data-periodical-id="{html.escape(str(item.get("periodical_id") or ""))}" '
            f'data-issue-id="{html.escape(str(item.get("issue_id") or ""))}">'
            f'{html.escape(label)} <span class="pair-meta">issue_score: {html.escape(str(item.get("score") or ""))}</span></button>'
        )
    for item in row.get("periodical_options") or []:
        buttons.append(
            '<button class="service-button choice-button" type="button" '
            f'{base_attrs} data-decision="choose_periodical" data-periodical-id="{html.escape(str(item.get("periodical_id") or ""))}">'
            f'{html.escape(str(item.get("title") or ""))} <span class="pair-meta">periodical_score: {html.escape(str(item.get("score") or ""))}</span></button>'
        )
    for item in row.get("collection_options") or []:
        label = ", ".join(str(part) for part in [item.get("title"), item.get("year")] if part)
        buttons.append(
            '<button class="service-button choice-button" type="button" '
            f'{base_attrs} data-decision="use_existing_collection" data-collection-id="{html.escape(str(item.get("collection_id") or ""))}">'
            f'{html.escape(label)} <span class="pair-meta">collection_score: {html.escape(str(item.get("score") or ""))}</span></button>'
        )
    if not buttons:
        return ""
    return f"""
<div class="choice-panel">
  <input class="choice-search" type="search" placeholder="Поиск по вариантам">
  <div class="choice-list">
    {''.join(buttons)}
  </div>
</div>
"""


def stage0_action_buttons(row: dict[str, Any]) -> str:
    action = row.get("action")
    attrs = stage0_button_attrs(row)
    if action == "link_existing_issue":
        return f'<button class="service-button" type="button" {attrs} data-decision="use_existing_issue">Подтвердить выпуск</button>'
    if action == "link_existing_collection":
        return f'<button class="service-button" type="button" {attrs} data-decision="use_existing_collection">Подтвердить сборник</button>'
    if action == "confirm_existing_issue":
        return "\n".join(
            [
                f'<button class="service-button" type="button" {attrs} data-decision="use_existing_issue">Подтвердить выпуск</button>',
                '<button class="service-button" type="button" data-open-panel="1">Выбрать другой</button>',
            ]
        )
    if action == "confirm_create_issue":
        return "\n".join(
            [
                f'<button class="service-button" type="button" {attrs} data-decision="create_issue">Создать выпуск</button>',
                '<button class="service-button" type="button" data-open-panel="1">Выбрать выпуск</button>',
            ]
        )
    if action == "choose_periodical":
        return "\n".join(
            [
                '<button class="service-button" type="button" data-open-panel="1">Выбрать журнал</button>',
                f'<button class="service-button is-danger" type="button" {attrs} data-decision="create_periodical">Создать журнал</button>',
            ]
        )
    if action == "confirm_create_periodical":
        return "\n".join(
            [
                '<button class="service-button" type="button" data-open-panel="1">Выбрать похожий</button>',
                f'<button class="service-button is-danger" type="button" {attrs} data-decision="create_periodical">Создать журнал</button>',
            ]
        )
    if action == "confirm_existing_collection":
        return "\n".join(
            [
                f'<button class="service-button" type="button" {attrs} data-decision="use_existing_collection">Подтвердить сборник</button>',
                '<button class="service-button" type="button" data-open-panel="1">Выбрать другой</button>',
            ]
        )
    if action == "confirm_create_collection":
        return "\n".join(
            [
                '<button class="service-button" type="button" data-open-panel="1">Выбрать сборник</button>',
                f'<button class="service-button is-danger" type="button" {attrs} data-decision="create_collection">Создать сборник</button>',
            ]
        )
    return f'<button class="service-button" type="button" {attrs} data-decision="unresolved">Оставить нерешенным</button>'


def stage0_button_attrs(row: dict[str, Any]) -> str:
    item_id = row.get("group_item_key") or row.get("candidate_id") or ""
    candidate_id = ",".join(row.get("candidate_ids") or []) or row.get("candidate_id") or ""
    return " ".join(
        [
            f'data-item-id="{html.escape(str(item_id))}"',
            f'data-candidate-id="{html.escape(str(candidate_id))}"',
            f'data-periodical-id="{html.escape(str(row.get("best_periodical_id") or ""))}"',
            f'data-issue-id="{html.escape(str(row.get("best_issue_id") or ""))}"',
            f'data-collection-id="{html.escape(str(row.get("best_collection_id") or ""))}"',
        ]
    )


def append_new_record_added_note(candidate: dict[str, Any], added_date: str) -> None:
    source = candidate.get("source") or {}
    note = f"Дата добавления в базу: {added_date}"
    existing = source.get("notes") or ""
    if note in existing:
        return
    source["notes"] = "; ".join(part for part in [existing, note] if part)


def display_fields_from_candidate(candidate: dict[str, Any]) -> dict[str, str]:
    source = candidate.get("source") or {}
    prefix = record_prefix_for_candidate(candidate)
    return {
        "record_prefix": prefix,
        "authors": candidate_author_string(candidate),
        "title": source.get("title") or "",
        "subtitle": source.get("subtitle") or "",
        "responsibility_statement": source.get("responsibility_statement") or "",
        "publication_place": source.get("publication_place") or "",
        "publisher": source.get("publisher") or "",
        "publication_date": str(source.get("publication_date") or source.get("year") or ""),
        "extent": source.get("extent") or "",
        "notes": source.get("notes") or "",
    }


def display_fields_from_editor(editor: dict[str, Any]) -> dict[str, str]:
    return {
        "record_prefix": record_prefix_for_editor(editor),
        "authors": editor.get("authors") or editor.get("raw_author_string") or "",
        "title": editor.get("title") or "",
        "subtitle": editor.get("subtitle") or "",
        "responsibility_statement": editor.get("responsibility_statement") or "",
        "publication_place": editor.get("publication_place") or "",
        "publisher": editor.get("publisher") or "",
        "publication_date": str(editor.get("publication_date") or editor.get("inferred_year") or ""),
        "extent": editor.get("extent") or "",
        "notes": editor.get("notes") or "",
    }


def record_prefix_for_candidate(candidate: dict[str, Any]) -> str:
    source = candidate.get("source") or {}
    candidate_part = candidate.get("candidate_part")
    source_type = source.get("source_type")
    if candidate_part == "article" or source_type == "article":
        return "Статья"
    if candidate_part == "container" or source_type in {"issue", "periodical_issue", "collection", "container"}:
        return "Журнал/сборник"
    return ""


def record_prefix_for_editor(editor: dict[str, Any]) -> str:
    source_type = editor.get("source_type")
    if source_type == "article":
        return "Статья"
    if source_type in {"issue", "periodical_issue", "collection", "container"}:
        return "Журнал/сборник"
    return ""


def changed_display_fields(candidate_fields: dict[str, str], editor_fields: dict[str, str]) -> set[str]:
    return changed_fields_for(candidate_fields, editor_fields, REVIEW_FIELDS)


def changed_fields_for(
    candidate_fields: dict[str, str],
    editor_fields: dict[str, str],
    field_defs: list[tuple[str, str]],
) -> set[str]:
    changed: set[str] = set()
    for field_name, _label in field_defs:
        candidate_value = normalize_field_for_diff(field_name, candidate_fields.get(field_name))
        editor_value = normalize_field_for_diff(field_name, editor_fields.get(field_name))
        if candidate_value != editor_value:
            if candidate_fields.get(field_name) or editor_fields.get(field_name):
                changed.add(field_name)
    return changed


def normalize_field_for_diff(field_name: str, value: str | None) -> str:
    normalized = normalize_compare_text(value)
    if field_name == "source_type":
        aliases = {
            "book": "book",
            "monograph": "book",
            "volume": "book",
            "collection": "container",
            "container": "container",
            "issue": "issue",
            "article": "article",
        }
        return aliases.get(normalized, normalized)
    return normalized


DIAGNOSTIC_FIELDS = [
    ("source_type", "тип"),
    ("authors", "authors"),
    ("title", "title"),
    ("subtitle", "subtitle"),
    ("responsibility_statement", "responsibility"),
    ("publication_place", "place"),
    ("publisher", "publisher"),
    ("publication_date", "date"),
    ("extent", "extent"),
    ("notes", "notes"),
    ("raw_publication_details", "raw publication"),
]

SERVICE_DIAGNOSTIC_FIELDS = {"raw_publication_details"}


def diagnostic_fields_from_candidate(candidate: dict[str, Any]) -> dict[str, str]:
    source = candidate.get("source") or {}
    fields = display_fields_from_candidate(candidate)
    fields.update(
        {
            "source_type": source.get("source_type") or "",
            "raw_publication_details": source.get("raw_publication_details") or source.get("publication_details_raw") or "",
        }
    )
    return fields


def diagnostic_fields_from_editor(editor: dict[str, Any]) -> dict[str, str]:
    fields = display_fields_from_editor(editor)
    fields.update(
        {
            "source_type": editor.get("source_type") or "",
            "raw_publication_details": editor.get("raw_publication_details") or "",
        }
    )
    return fields


def render_field_note(
    candidate_fields: dict[str, str],
    editor_fields: dict[str, str],
    diff_fields: set[str],
) -> str:
    if not diff_fields:
        return ""
    candidate_joined = normalize_compare_text(" ".join(candidate_fields.values()))
    editor_joined = normalize_compare_text(" ".join(editor_fields.values()))
    if candidate_joined == editor_joined:
        return '<div class="field-note"><strong>Текст совпадает, но поля разложены по-разному.</strong> Это нужно проверить на следующем этапе.</div>'
    labels = [label for field_name, label in REVIEW_FIELDS if field_name in diff_fields]
    return f'<div class="field-note">Отличаются поля: <strong>{html.escape(", ".join(labels))}</strong>.</div>'


def render_match_diagnostics(pair: dict[str, Any], display_diff_fields: set[str]) -> str:
    candidate_fields = diagnostic_fields_from_candidate(pair["candidate"])
    editor_fields = diagnostic_fields_from_editor(pair["editor"])
    diagnostic_diff_fields = changed_fields_for(candidate_fields, editor_fields, DIAGNOSTIC_FIELDS)
    if not diagnostic_diff_fields and pair["match_score"] >= 1:
        return ""
    labels = [label for field_name, label in DIAGNOSTIC_FIELDS if field_name in diagnostic_diff_fields]
    reasons = pair.get("match_reasons") or ""
    bits = []
    if pair["match_score"] < 1:
        bits.append(f'match_score: <span class="score">{pair["match_score"]:.2f}</span>')
    if reasons:
        bits.append(f'причины: <strong>{html.escape(reasons)}</strong>')
    if labels:
        if diagnostic_diff_fields and diagnostic_diff_fields <= SERVICE_DIAGNOSTIC_FIELDS:
            bits.append("Отличия только в служебных полях — в базу не вносятся")
        else:
            prefix = "Отличия вне отображаемой строки" if not display_diff_fields else "Все отличия"
            bits.append(f'{prefix}: <strong>{html.escape(", ".join(labels))}</strong>')
    if not bits:
        return ""
    return f'<div class="field-note">{"; ".join(bits)}.</div>'


def render_citation_fields(
    fields: dict[str, str],
    diff_fields: set[str],
    *,
    boundary: bool,
    number: Any = None,
) -> str:
    parts: list[str] = []
    if number not in (None, ""):
        parts.append(f'<span class="work-number">{html.escape(str(number))}.</span>')
    record_prefix = fields.get("record_prefix")
    if record_prefix:
        parts.append(f'<span class="record-prefix">{html.escape(record_prefix)}: </span>')
    author = render_field("authors", fields.get("authors"), diff_fields, boundary, extra_class="field-author")
    if author:
        parts.append(author)
    title = render_field("title", fields.get("title"), diff_fields, boundary)
    if title:
        parts.append(title)
    subtitle = render_field("subtitle", fields.get("subtitle"), diff_fields, boundary)
    if subtitle:
        parts.append(": " + subtitle)
    responsibility = render_field(
        "responsibility_statement",
        fields.get("responsibility_statement"),
        diff_fields,
        boundary,
    )
    if responsibility:
        parts.append("/ " + responsibility)

    publication_bits = []
    place = render_field("publication_place", fields.get("publication_place"), diff_fields, boundary)
    publisher = render_field("publisher", fields.get("publisher"), diff_fields, boundary)
    date = render_field("publication_date", fields.get("publication_date"), diff_fields, boundary)
    if place:
        publication_bits.append(place)
    if publisher:
        if publication_bits:
            publication_bits[-1] += ":"
        publication_bits.append(publisher)
    if date:
        if publication_bits:
            publication_bits[-1] += ","
        publication_bits.append(date)
    if publication_bits:
        parts.append("- " + " ".join(publication_bits))

    extent = render_field("extent", fields.get("extent"), diff_fields, boundary)
    if extent:
        parts.append("- " + extent)
    notes = render_field("notes", fields.get("notes"), diff_fields, boundary)
    if notes:
        parts.append("- " + notes)
    citation = " ".join(part for part in parts if part)
    if citation and not citation.endswith("."):
        citation += "."
    return citation


def render_field(
    field_name: str,
    value: str | None,
    diff_fields: set[str],
    boundary: bool,
    *,
    extra_class: str = "",
) -> str:
    value = value or ""
    if not value:
        return ""
    classes = ["field"]
    if extra_class:
        classes.append(extra_class)
    if field_name in diff_fields:
        classes.append("field-diff")
    if boundary:
        classes.append("field-boundary")
    return f'<span class="{" ".join(classes)}" data-field="{html.escape(field_name)}">{html.escape(value)}</span>'


def write_sqlite(path: Path, manifest: dict[str, Any], raw_records: list[RawRecord], results: list[ParseResult]) -> None:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE run_manifest (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE raw_records (
                raw_record_id TEXT PRIMARY KEY,
                source_record_index INTEGER NOT NULL,
                source_line_start INTEGER,
                source_line_end INTEGER,
                raw_text TEXT NOT NULL,
                normalized_text TEXT NOT NULL,
                source_input_path TEXT NOT NULL,
                source_sha256 TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE parsed_candidates (
                candidate_id TEXT PRIMARY KEY,
                raw_record_id TEXT NOT NULL,
                source_type TEXT,
                title TEXT,
                year INTEGER,
                confidence REAL NOT NULL,
                description_status TEXT NOT NULL,
                candidate_json TEXT NOT NULL,
                FOREIGN KEY(raw_record_id) REFERENCES raw_records(raw_record_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE parser_warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id TEXT NOT NULL,
                raw_record_id TEXT NOT NULL,
                severity TEXT NOT NULL,
                code TEXT NOT NULL,
                message TEXT NOT NULL,
                fragment TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            "INSERT INTO run_manifest(key, value) VALUES (?, ?)",
            [(key, json.dumps(value, ensure_ascii=False, sort_keys=True)) for key, value in sorted(manifest.items())],
        )
        conn.executemany(
            """
            INSERT INTO raw_records(
                raw_record_id, source_record_index, source_line_start, source_line_end,
                raw_text, normalized_text, source_input_path, source_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record.raw_record_id,
                    record.source_record_index,
                    record.source_line_start,
                    record.source_line_end,
                    record.raw_text,
                    record.normalized_text,
                    record.source_input_path,
                    record.source_sha256,
                )
                for record in raw_records
            ],
        )
        conn.executemany(
            """
            INSERT INTO parsed_candidates(
                candidate_id, raw_record_id, source_type, title, year,
                confidence, description_status, candidate_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    result.candidate["candidate_id"],
                    result.candidate["raw_record_id"],
                    result.candidate["source"].get("source_type"),
                    result.candidate["source"].get("title"),
                    result.candidate["source"].get("year"),
                    result.candidate["confidence"],
                    result.candidate["description_status"],
                    json.dumps(result.candidate, ensure_ascii=False, sort_keys=True),
                )
                for result in results
            ],
        )
        conn.executemany(
            """
            INSERT INTO parser_warnings(candidate_id, raw_record_id, severity, code, message, fragment)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    result.candidate["candidate_id"],
                    result.candidate["raw_record_id"],
                    warning.severity,
                    warning.code,
                    warning.message,
                    warning.fragment,
                )
                for result in results
                for warning in result.warnings
            ],
        )
        conn.commit()
    finally:
        conn.close()


def load_candidates(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path)


def connect_readonly_sqlite(path: Path) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_editor_sources(editor_db: Path) -> list[dict[str, Any]]:
    conn = connect_readonly_sqlite(editor_db)
    try:
        source_rows = conn.execute(
            """
            SELECT
                s.source_id,
                s.source_number,
                s.source_type,
                s.title,
                s.subtitle,
                s.responsibility_statement,
                s.raw_author_string,
                s.publication_place,
                s.publisher,
                s.publication_date,
                s.inferred_year,
                s.extent,
                s.notes,
                s.public_review,
                s.raw_publication_details,
                GROUP_CONCAT(a.display_name, '; ') AS authors
            FROM sources_source s
            LEFT JOIN sources_sourceauthor sa ON sa.source_id = s.source_id
            LEFT JOIN sources_author a ON a.author_id = sa.author_id
            GROUP BY s.source_id
            """
        ).fetchall()
        rows = [dict(row) for row in source_rows]
        periodical_rows = conn.execute(
            """
            SELECT
                p.periodical_id AS source_id,
                p.source_django_id AS source_number,
                'issue' AS source_type,
                p.title AS title,
                p.title_remainder AS subtitle,
                p.responsibility_statement AS responsibility_statement,
                '' AS raw_author_string,
                p.place AS publication_place,
                p.publisher AS publisher,
                '' AS publication_date,
                NULL AS inferred_year,
                '' AS extent,
                p.description AS notes,
                '' AS public_review,
                '' AS raw_publication_details,
                '' AS authors
            FROM sources_periodical p
            """
        ).fetchall()
        rows.extend(dict(row) for row in periodical_rows)
        issue_rows = conn.execute(
            """
            SELECT
                i.issue_id AS source_id,
                i.source_django_id AS source_number,
                i.issue_type AS source_type,
                COALESCE(NULLIF(i.title, ''), p.title) AS title,
                i.title_remainder AS subtitle,
                i.responsibility_statement AS responsibility_statement,
                '' AS raw_author_string,
                i.publication_place AS publication_place,
                i.publisher AS publisher,
                i.publication_date AS publication_date,
                i.year AS inferred_year,
                '' AS extent,
                i.notes AS notes,
                '' AS public_review,
                i.publication_details AS raw_publication_details,
                '' AS authors
            FROM sources_issue i
            LEFT JOIN sources_periodical p ON p.periodical_id = i.periodical_id
            """
        ).fetchall()
        rows.extend(dict(row) for row in issue_rows)
        return rows
    finally:
        conn.close()


def load_editor_container_index(editor_db: Path) -> dict[str, list[dict[str, Any]]]:
    conn = connect_readonly_sqlite(editor_db)
    try:
        periodicals = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    periodical_id,
                    source_django_id,
                    title,
                    parallel_title,
                    title_remainder,
                    responsibility_statement,
                    place,
                    publisher,
                    issn,
                    start_year,
                    end_year
                FROM sources_periodical
                """
            ).fetchall()
        ]
        issues = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    i.issue_id,
                    i.periodical_id,
                    p.title AS periodical_title,
                    i.source_django_id,
                    i.issue_type,
                    i.title,
                    i.year,
                    i.publication_date,
                    i.issue_number,
                    i.volume,
                    i.part_number,
                    i.gross_number,
                    i.chronology,
                    i.enumeration,
                    i.publication_place,
                    i.publisher,
                    i.publication_details,
                    i.issn,
                    i.isbn,
                    i.source_id
                FROM sources_issue i
                LEFT JOIN sources_periodical p ON p.periodical_id = i.periodical_id
                """
            ).fetchall()
        ]
        collections = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    source_id,
                    source_number,
                    source_type,
                    title,
                    subtitle,
                    title_remainder,
                    responsibility_statement,
                    volume_number,
                    part_number,
                    part_title,
                    publication_place,
                    publisher,
                    publication_date,
                    inferred_year,
                    isbn,
                    issn
                FROM sources_source
                WHERE source_type IN ('collection', 'issue')
                """
            ).fetchall()
        ]
        return {"periodicals": periodicals, "issues": issues, "collections": collections}
    finally:
        conn.close()


def load_editor_author_index(editor_db: Path) -> list[dict[str, Any]]:
    conn = connect_readonly_sqlite(editor_db)
    try:
        rows = conn.execute(
            """
            SELECT
                author_id,
                display_name,
                heading_name,
                sort_name,
                aliases,
                person_dates,
                authority_note,
                note
            FROM sources_author
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def compare_value(old: Any, new: Any) -> str:
    old_norm = normalize_compare_text(str(old or ""))
    new_norm = normalize_compare_text(str(new or ""))
    if old_norm == new_norm:
        return "same"
    if not old_norm and new_norm:
        return "safe_fill_empty"
    if old_norm and not new_norm:
        return "candidate_empty"
    return "conflict"


def candidate_title(candidate: dict[str, Any]) -> str:
    source = candidate.get("source") or {}
    title = source.get("title") or ""
    subtitle = source.get("subtitle") or ""
    if subtitle:
        return f"{title}: {subtitle}"
    return title


def candidate_author_string(candidate: dict[str, Any]) -> str:
    authors = candidate.get("authors") or []
    if authors:
        return "; ".join(author.get("display_name") or "" for author in authors if author.get("display_name"))
    return (candidate.get("source") or {}).get("raw_author_string") or ""


def score_editor_match(candidate: dict[str, Any], editor_row: dict[str, Any]) -> tuple[float, str]:
    source = candidate.get("source") or {}
    cand_title = normalize_compare_text(candidate_title(candidate))
    editor_title = normalize_compare_text(editor_row.get("title"))
    editor_full_title = normalize_compare_text(
        " ".join(
            part
            for part in [
                editor_row.get("title"),
                editor_row.get("subtitle"),
                editor_row.get("responsibility_statement"),
            ]
            if part
        )
    )
    editor_title_variants = [variant for variant in [editor_title, editor_full_title] if variant]
    if not cand_title or not editor_title:
        return 0.0, "missing_title"

    score = 0.0
    reasons: list[str] = []
    if cand_title in editor_title_variants:
        score += 0.7
        reasons.append("title_exact")
    elif any(cand_title in variant or variant in cand_title for variant in editor_title_variants):
        score += 0.6
        reasons.append("title_contains")
    else:
        cand_words = set(cand_title.split())
        editor_words = set(" ".join(editor_title_variants).split())
        if cand_words and editor_words:
            overlap = len(cand_words & editor_words) / len(cand_words | editor_words)
            score += overlap * 0.6
            if overlap >= 0.65:
                reasons.append("title_word_overlap")

    cand_year = source.get("year") or source.get("inferred_year")
    editor_year = editor_row.get("inferred_year")
    if cand_year and editor_year and int(cand_year) == int(editor_year):
        score += 0.15
        reasons.append("year_exact")

    cand_author = normalize_compare_text(candidate_author_string(candidate))
    editor_author = normalize_compare_text(editor_row.get("authors") or editor_row.get("raw_author_string"))
    if cand_author and editor_author:
        cand_author_token = cand_author.split()[0]
        if cand_author_token and cand_author_token in editor_author:
            score += 0.1
            reasons.append("author_overlap")
    elif editor_author and cand_title:
        editor_author_prefix = " ".join(editor_author.split()[:3])
        if editor_author_prefix and cand_title.startswith(editor_author_prefix):
            score += 0.1
            reasons.append("author_in_title")

    cand_type = source.get("source_type")
    editor_type = editor_row.get("source_type")
    if cand_type and editor_type and types_compatible(cand_type, editor_type):
        score += 0.05
        reasons.append("type_compatible")

    return round(min(score, 1.0), 3), ",".join(reasons)


def types_compatible(candidate_type: str, editor_type: str) -> bool:
    aliases = {
        "book": {"book", "monograph", "collection", "volume"},
    "article": {"article"},
    "issue": {"issue", "collection", "container", "book", "monograph"},
        "conference_material": {"book", "monograph", "collection", "article"},
        "legal_document": {"book", "monograph", "article"},
        "electronic_resource": {"book", "monograph", "article", "unknown"},
    }
    return editor_type in aliases.get(candidate_type, {candidate_type})


def best_editor_matches(candidate: dict[str, Any], editor_rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for row in editor_rows:
        score, reasons = score_editor_match(candidate, row)
        if score >= 0.45:
            scored.append({**row, "match_score": score, "match_reasons": reasons})
    scored.sort(key=lambda item: item["match_score"], reverse=True)
    return scored[:limit]


def score_title_match(left: str | None, right: str | None) -> float:
    left_norm = normalize_container_title(left)
    right_norm = normalize_container_title(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    if left_norm in right_norm or right_norm in left_norm:
        return 0.86
    left_words = set(left_norm.split())
    right_words = set(right_norm.split())
    if not left_words or not right_words:
        return 0.0
    overlap = len(left_words & right_words) / len(left_words | right_words)
    return round(overlap, 3)


def resolve_container_candidate(candidate: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    source = candidate.get("source") or {}
    periodical = candidate.get("periodical") or {}
    issue = candidate.get("issue") or {}
    kind = candidate.get("container_kind")
    if source.get("source_type") == "article" and not (periodical or issue):
        return None
    if not kind and (periodical or source.get("source_type") == "issue"):
        kind = "periodical_issue" if periodical else "collection_work"
    if candidate.get("candidate_part") != "container" and source.get("source_type") != "article":
        return None
    if kind == "periodical_issue":
        return resolve_periodical_issue(candidate, index)
    if kind == "collection_work" or source.get("source_type") in {"collection", "container"} or issue.get("title"):
        return resolve_collection_container(candidate, index)
    return None


def resolve_periodical_issue(candidate: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    source = candidate.get("source") or {}
    periodical = candidate.get("periodical") or {}
    issue = candidate.get("issue") or {}
    title = periodical.get("title") or source.get("title")
    issn = periodical.get("issn") or source.get("issn")
    periodical_matches: list[dict[str, Any]] = []
    for row in index["periodicals"]:
        score = score_title_match(title, row.get("title"))
        reasons: list[str] = []
        if score >= 1:
            reasons.append("periodical_title_exact")
        elif score >= 0.75:
            reasons.append("periodical_title_similar")
        if issn and row.get("issn") and normalize_compare_text(issn) == normalize_compare_text(row.get("issn")):
            score = max(score, 1.0)
            reasons.append("issn_exact")
        if score >= 0.62:
            periodical_matches.append({**row, "periodical_score": round(score, 3), "periodical_reasons": ",".join(reasons)})
    periodical_matches.sort(key=lambda item: item["periodical_score"], reverse=True)
    best_periodical = periodical_matches[0] if periodical_matches else None

    issue_matches: list[dict[str, Any]] = []
    if best_periodical:
        for row in index["issues"]:
            if row.get("periodical_id") != best_periodical.get("periodical_id"):
                continue
            score, reasons = score_issue_match(issue, row)
            if score >= 0.35:
                issue_matches.append({**row, "issue_score": score, "issue_reasons": reasons})
        issue_matches.sort(key=lambda item: item["issue_score"], reverse=True)
    best_issue = issue_matches[0] if issue_matches else None

    if best_periodical and best_periodical["periodical_score"] >= 0.95 and best_issue and best_issue["issue_score"] >= 0.85:
        action = "link_existing_issue"
        status = "ready"
        note = "Журнал и выпуск найдены в базе; можно подтверждать связь статьи с существующим выпуском."
    elif best_periodical and best_periodical["periodical_score"] >= 0.82 and best_issue and best_issue["issue_score"] >= 0.65:
        action = "confirm_existing_issue"
        status = "needs_confirmation"
        note = "Журнал найден, выпуск похож; нужно подтвердить выпуск."
    elif best_periodical and best_periodical["periodical_score"] >= 0.82:
        action = "confirm_create_issue"
        status = "needs_confirmation"
        note = "Журнал найден, подходящий выпуск не найден; редактор должен выбрать выпуск или создать новый выпуск этого журнала."
    elif periodical_matches:
        action = "choose_periodical"
        status = "needs_confirmation"
        note = "Есть похожие журналы; редактор должен выбрать журнал или разрешить создание нового."
    else:
        action = "confirm_create_periodical"
        status = "needs_confirmation"
        note = "Журнал не найден; перед созданием нужен ручной контроль."

    row = container_resolution_row(candidate, "periodical_issue", title, issue, action, status, note, best_periodical, best_issue, None)
    row["periodical_options"] = [
        {
            "periodical_id": item.get("periodical_id") or "",
            "title": item.get("title") or "",
            "score": item.get("periodical_score") or "",
        }
        for item in periodical_matches[:8]
    ]
    row["issue_options"] = [
        {
            "issue_id": item.get("issue_id") or "",
            "periodical_id": item.get("periodical_id") or "",
            "periodical_title": item.get("periodical_title") or "",
            "year": item.get("year") or "",
            "issue_number": item.get("issue_number") or "",
            "volume": item.get("volume") or "",
            "part_number": item.get("part_number") or "",
            "score": item.get("issue_score") or "",
        }
        for item in issue_matches[:12]
    ]
    return row


def score_issue_match(candidate_issue: dict[str, Any], issue_row: dict[str, Any]) -> tuple[float, str]:
    score = 0.0
    reasons: list[str] = []
    cand_year = candidate_issue.get("year")
    row_year = issue_row.get("year")
    if cand_year and row_year and int(cand_year) == int(row_year):
        score += 0.42
        reasons.append("issue_year_exact")
    cand_number = normalize_compare_text(candidate_issue.get("issue_number"))
    row_number = normalize_compare_text(issue_row.get("issue_number"))
    if cand_number and row_number and cand_number == row_number:
        score += 0.36
        reasons.append("issue_number_exact")
    cand_volume = normalize_compare_text(candidate_issue.get("volume"))
    row_volume = normalize_compare_text(issue_row.get("volume"))
    if cand_volume and row_volume and cand_volume == row_volume:
        score += 0.14
        reasons.append("volume_exact")
    cand_part = normalize_compare_text(candidate_issue.get("part_number"))
    row_part = normalize_compare_text(issue_row.get("part_number"))
    if cand_part and row_part and cand_part == row_part:
        score += 0.08
        reasons.append("part_number_exact")
    cand_date = normalize_compare_text(candidate_issue.get("publication_date") or candidate_issue.get("date_text"))
    row_date = normalize_compare_text(issue_row.get("publication_date") or issue_row.get("chronology"))
    if cand_date and row_date and (cand_date == row_date or cand_date in row_date or row_date in cand_date):
        score += 0.08
        reasons.append("issue_date_match")
    if not any([cand_year, cand_number, cand_volume, cand_part, cand_date]):
        score = 0.0
        reasons.append("issue_data_missing")
    return round(min(score, 1.0), 3), ",".join(reasons)


def resolve_collection_container(candidate: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    source = candidate.get("source") or {}
    issue = candidate.get("issue") or {}
    title = issue.get("title") or source.get("title")
    matches: list[dict[str, Any]] = []
    for row in index["collections"]:
        score = score_title_match(title, row.get("title"))
        reasons: list[str] = []
        if score >= 1:
            reasons.append("collection_title_exact")
        elif score >= 0.75:
            reasons.append("collection_title_similar")
        cand_year = issue.get("year") or source.get("year")
        row_year = row.get("inferred_year")
        if cand_year and row_year and int(cand_year) == int(row_year):
            score += 0.12
            reasons.append("collection_year_exact")
        cand_part = normalize_compare_text(issue.get("part_number") or source.get("part_number"))
        row_part = normalize_compare_text(row.get("part_number"))
        if cand_part and row_part and cand_part == row_part:
            score += 0.08
            reasons.append("collection_part_exact")
        cand_volume = normalize_compare_text(issue.get("volume") or source.get("volume_number"))
        row_volume = normalize_compare_text(row.get("volume_number"))
        if cand_volume and row_volume and cand_volume == row_volume:
            score += 0.08
            reasons.append("collection_volume_exact")
        if score >= 0.62:
            matches.append({**row, "collection_score": round(min(score, 1.0), 3), "collection_reasons": ",".join(reasons)})
    matches.sort(key=lambda item: item["collection_score"], reverse=True)
    best = matches[0] if matches else None
    if best and best["collection_score"] >= 0.9:
        action = "link_existing_collection"
        status = "ready"
        note = "Сборник найден в базе; можно подтверждать связь статьи с существующим контейнером."
    elif best:
        action = "confirm_existing_collection"
        status = "needs_confirmation"
        note = "Найден похожий сборник; нужно подтвердить контейнер или создать новый."
    else:
        action = "confirm_create_collection"
        status = "needs_confirmation"
        note = "Сборник не найден; перед созданием нужен ручной контроль."
    row = container_resolution_row(candidate, "collection_work", title, issue, action, status, note, None, None, best)
    row["collection_options"] = [
        {
            "collection_id": item.get("source_id") or "",
            "title": item.get("title") or "",
            "year": item.get("inferred_year") or "",
            "source_number": item.get("source_number") or "",
            "score": item.get("collection_score") or "",
        }
        for item in matches[:12]
    ]
    return row


def resolve_author_candidates(candidates: list[dict[str, Any]], author_index: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if candidate.get("candidate_part") == "container":
            continue
        for author in candidate.get("authors") or []:
            display_name = (author.get("display_name") or author.get("heading_name") or "").strip()
            if not display_name:
                continue
            key = author_group_key(display_name)
            group = grouped.setdefault(
                key,
                {
                    "author_key": key,
                    "candidate_author": display_name,
                    "candidate_heading_name": author.get("heading_name") or "",
                    "candidate_ids": [],
                    "raw_record_ids": [],
                    "examples": [],
                },
            )
            if candidate.get("candidate_id"):
                group["candidate_ids"].append(candidate["candidate_id"])
            if candidate.get("raw_record_id"):
                group["raw_record_ids"].append(candidate["raw_record_id"])
            if len(group["examples"]) < 2:
                group["examples"].append(candidate_raw_bibliographic_text(candidate))

    rows: list[dict[str, Any]] = []
    for group in grouped.values():
        matches = best_author_matches(group["candidate_author"], author_index)
        best = matches[0] if matches else None
        if best and best["author_score"] >= 0.98:
            action = "link_existing_author"
            status = "ready"
            note = "Автор найден в базе; можно подтвердить связь с существующим автором."
        elif best and best["author_score"] >= 0.78:
            action = "confirm_existing_author"
            status = "needs_confirmation"
            note = "Найден похожий автор; нужно подтвердить автора или разрешить создание нового."
        else:
            action = "confirm_create_author"
            status = "needs_confirmation"
            note = "Автор не найден; перед созданием нужен ручной контроль."
        rows.append(
            {
                "author_key": group["author_key"],
                "candidate_author": group["candidate_author"],
                "candidate_heading_name": group["candidate_heading_name"],
                "candidate_ids": ",".join(sorted(set(group["candidate_ids"]))),
                "raw_record_ids": ",".join(sorted(set(group["raw_record_ids"]))),
                "example_1": group["examples"][0] if group["examples"] else "",
                "example_2": group["examples"][1] if len(group["examples"]) > 1 else "",
                "best_author_id": (best or {}).get("author_id") or "",
                "best_author_display_name": (best or {}).get("display_name") or "",
                "best_author_heading_name": (best or {}).get("heading_name") or "",
                "best_author_dates": (best or {}).get("person_dates") or "",
                "author_score": (best or {}).get("author_score") or "",
                "author_reasons": (best or {}).get("author_reasons") or "",
                "action": action,
                "status": status,
                "review_note": note,
                "author_options": [
                    {
                        "author_id": item.get("author_id") or "",
                        "display_name": item.get("display_name") or "",
                        "heading_name": item.get("heading_name") or "",
                        "person_dates": item.get("person_dates") or "",
                        "score": item.get("author_score") or "",
                    }
                    for item in matches[:12]
                ],
            }
        )
    rows.sort(key=lambda row: (row.get("status") == "ready", normalize_compare_text(row.get("candidate_author"))))
    return rows


def best_author_matches(candidate_author: str, author_index: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for row in author_index:
        score, reasons = score_author_match(candidate_author, row)
        if score >= 0.55:
            matches.append({**row, "author_score": score, "author_reasons": reasons})
    matches.sort(key=lambda item: item["author_score"], reverse=True)
    return matches


def score_author_match(candidate_author: str, author_row: dict[str, Any]) -> tuple[float, str]:
    candidate_variants = author_name_variants({"display_name": candidate_author})
    editor_variants = author_name_variants(author_row)
    if not candidate_variants or not editor_variants:
        return 0.0, "missing_author_name"
    if candidate_variants & editor_variants:
        return 1.0, "author_name_exact"

    cand_family, cand_initials = split_author_family_initials(candidate_author)
    best_score = 0.0
    reasons: list[str] = []
    for variant in editor_variants:
        editor_family, editor_initials = split_author_family_initials(variant)
        if cand_family and editor_family and cand_family == editor_family:
            if cand_initials and editor_initials and cand_initials == editor_initials:
                best_score = max(best_score, 0.95)
                reasons.append("family_initials_exact")
            elif cand_initials and editor_initials and cand_initials[0] == editor_initials[0]:
                best_score = max(best_score, 0.82)
                reasons.append("family_first_initial")
            else:
                best_score = max(best_score, 0.68)
                reasons.append("family_exact")
    return round(best_score, 3), ",".join(sorted(set(reasons))) or "author_similar"


def author_name_variants(author: dict[str, Any]) -> set[str]:
    values = [
        author.get("display_name") or "",
        author.get("heading_name") or "",
        author.get("sort_name") or "",
    ]
    aliases = author.get("aliases") or ""
    values.extend(re.split(r"[;\n]+", aliases))
    return {normalize_author_name(value) for value in values if normalize_author_name(value)}


def author_group_key(value: str) -> str:
    family, initials = split_author_family_initials(value)
    if family:
        return f"{family}:{initials}"
    return normalize_author_name(value)


def normalize_author_name(value: str | None) -> str:
    value = normalize_compare_text(value)
    value = re.sub(r"\b(ред|сост|пер|авт)\b\.?", " ", value)
    value = re.sub(r"[^0-9a-zа-я]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def split_author_family_initials(value: str | None) -> tuple[str, str]:
    normalized = normalize_author_name(value)
    if not normalized:
        return "", ""
    parts = normalized.split()
    if not parts:
        return "", ""
    family = parts[0]
    initials = "".join(part[0] for part in parts[1:] if part)
    return family, initials


def container_resolution_row(
    candidate: dict[str, Any],
    kind: str,
    title: str | None,
    issue: dict[str, Any],
    action: str,
    status: str,
    note: str,
    periodical: dict[str, Any] | None,
    issue_match: dict[str, Any] | None,
    collection: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "candidate_id": candidate.get("candidate_id") or "",
        "raw_record_id": candidate.get("raw_record_id") or "",
        "container_kind": kind,
        "container_title": title or "",
        "issue_year": issue.get("year") or "",
        "issue_number": issue.get("issue_number") or "",
        "volume": issue.get("volume") or "",
        "part_number": issue.get("part_number") or "",
        "best_periodical_id": (periodical or {}).get("periodical_id") or "",
        "best_periodical_title": (periodical or {}).get("title") or "",
        "periodical_score": (periodical or {}).get("periodical_score") or "",
        "best_issue_id": (issue_match or {}).get("issue_id") or "",
        "best_issue_year": (issue_match or {}).get("year") or "",
        "best_issue_number": (issue_match or {}).get("issue_number") or "",
        "issue_score": (issue_match or {}).get("issue_score") or "",
        "best_collection_id": (collection or {}).get("source_id") or "",
        "best_collection_title": (collection or {}).get("title") or "",
        "collection_score": (collection or {}).get("collection_score") or "",
        "action": action,
        "status": status,
        "review_note": note,
    }


def proposed_change_rows(candidate: dict[str, Any], editor_row: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source = candidate.get("source") or {}
    candidate_fields = {
        "title": source.get("title"),
        "publication_place": source.get("publication_place"),
        "publisher": source.get("publisher"),
        "publication_date": source.get("publication_date"),
        "inferred_year": source.get("year"),
        "extent": source.get("extent"),
        "public_review": source.get("public_review"),
        "raw_publication_details": candidate_raw_bibliographic_text(candidate),
    }
    changes: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for field_name, candidate_value in candidate_fields.items():
        result = compare_value(editor_row.get(field_name), candidate_value)
        if result in {"same", "candidate_empty"}:
            continue
        if field_name in {"raw_publication_details", "public_review"} and result != "safe_fill_empty":
            continue
        row = {
            "candidate_id": candidate["candidate_id"],
            "raw_record_id": candidate["raw_record_id"],
            "editor_source_id": editor_row["source_id"],
            "editor_source_number": editor_row["source_number"],
            "field": field_name,
            "editor_value": editor_row.get(field_name) or "",
            "candidate_value": candidate_value or "",
            "decision": result,
        }
        if result == "safe_fill_empty":
            changes.append(row)
        else:
            conflicts.append(row)
    return changes, conflicts


def candidate_raw_bibliographic_text(candidate: dict[str, Any]) -> str:
    source = candidate.get("source") or {}
    return (
        candidate.get("source_raw_text")
        or candidate.get("raw_text")
        or source.get("raw_publication_details")
        or source.get("publication_details_raw")
        or ""
    )


def run_compare(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.expanduser().resolve()
    candidates_path = run_dir / "parsed_candidates.jsonl"
    if not candidates_path.exists():
        raise SystemExit(f"Missing parsed candidates: {candidates_path}")
    editor_db = args.editor_db.expanduser().resolve()
    if not editor_db.exists():
        raise SystemExit(f"Editor database does not exist: {editor_db}")

    resolved_candidate_ids = applied_candidate_ids_for_run(run_dir)
    candidates = [
        candidate
        for candidate in load_candidates(candidates_path)
        if candidate.get("candidate_id") not in resolved_candidate_ids
    ]
    candidates_by_id = {candidate["candidate_id"]: candidate for candidate in candidates}
    editor_rows = load_editor_sources(editor_db)
    container_index = load_editor_container_index(editor_db)
    author_index = load_editor_author_index(editor_db)
    author_resolution_rows = resolve_author_candidates(candidates, author_index)

    match_rows: list[dict[str, Any]] = []
    proposed_rows: list[dict[str, Any]] = []
    conflict_rows: list[dict[str, Any]] = []
    new_rows: list[dict[str, Any]] = []
    container_resolution_rows: list[dict[str, Any]] = []
    review_pairs: list[dict[str, Any]] = []
    review_new: list[dict[str, Any]] = []
    new_record_added_date = datetime.now().date().isoformat()
    merge_plan: dict[str, Any] = {
        "created_at": now_utc(),
        "run_dir": str(run_dir),
        "editor_db": str(editor_db),
        "candidate_count": len(candidates),
        "resolved_candidate_count": len(resolved_candidate_ids),
        "staging_only": True,
        "apply_allowed": False,
        "items": [],
    }

    for candidate in candidates:
        container_resolution = resolve_container_candidate(candidate, container_index)
        if container_resolution:
            container_resolution_rows.append(container_resolution)
        if candidate.get("candidate_part") == "container":
            if container_resolution:
                merge_plan["items"].append(
                    {
                        "candidate_id": candidate["candidate_id"],
                        "action": "review_container_resolution",
                        "container_action": container_resolution["action"],
                        "container_status": container_resolution["status"],
                        "best_periodical_id": container_resolution["best_periodical_id"],
                        "best_issue_id": container_resolution["best_issue_id"],
                        "best_collection_id": container_resolution["best_collection_id"],
                        "safe_to_apply_automatically": container_resolution["action"] in {"link_existing_issue", "link_existing_collection"},
                    }
                )
            continue
        matches = best_editor_matches(candidate, editor_rows, args.match_limit)
        if matches and matches[0]["match_score"] >= 1.0:
            matches = [matches[0]]
        strong_matches = [match for match in matches if match["match_score"] >= args.new_threshold]
        review_matches = strong_matches
        weak_review_match = False
        if not review_matches and matches and float(candidate.get("confidence") or 0) >= args.new_threshold:
            review_matches = [matches[0]]
            weak_review_match = True
        for match in review_matches:
            review_pairs.append(
                {
                    "candidate": candidate,
                    "editor": match,
                    "match_score": match["match_score"],
                    "match_reasons": match["match_reasons"],
                    "weak_match": weak_review_match,
                }
            )
        if not review_matches:
            append_new_record_added_note(candidate, new_record_added_date)
            review_new.append(candidate)
        if (not matches or matches[0]["match_score"] < args.new_threshold) and not review_matches:
            append_new_record_added_note(candidate, new_record_added_date)
            source = candidate.get("source") or {}
            new_rows.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "raw_record_id": candidate["raw_record_id"],
                    "source_type": source.get("source_type") or "",
                    "title": candidate_title(candidate),
                    "authors": candidate_author_string(candidate),
                    "year": source.get("year") or "",
                    "notes": source.get("notes") or "",
                    "confidence": candidate.get("confidence") or "",
                    "review_note": "No probable duplicate above threshold.",
                }
            )
            merge_plan["items"].append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "action": "review_new_record",
                    "note_to_add": f"Дата добавления в базу: {new_record_added_date}",
                    "safe_to_apply_automatically": False,
                }
            )

        for match in matches:
            match_rows.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "raw_record_id": candidate["raw_record_id"],
                    "editor_source_id": match["source_id"],
                    "editor_source_number": match["source_number"],
                    "match_score": match["match_score"],
                    "match_reasons": match["match_reasons"],
                    "candidate_title": candidate_title(candidate),
                    "editor_title": match.get("title") or "",
                    "candidate_year": (candidate.get("source") or {}).get("year") or "",
                    "editor_year": match.get("inferred_year") or "",
                    "candidate_authors": candidate_author_string(candidate),
                    "editor_authors": match.get("authors") or match.get("raw_author_string") or "",
                }
            )

        if matches:
            best = matches[0]
            changes, conflicts = proposed_change_rows(candidate, best)
            proposed_rows.extend(changes)
            conflict_rows.extend(conflicts)
            merge_plan["items"].append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "best_editor_source_id": best["source_id"],
                    "best_editor_source_number": best["source_number"],
                    "match_score": best["match_score"],
                    "safe_fill_empty_count": len(changes),
                    "conflict_count": len(conflicts),
                    "action": "review_match",
                    "safe_to_apply_automatically": bool(changes and not conflicts),
                }
            )
        if container_resolution:
            merge_plan["items"].append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "action": "review_container_resolution",
                    "container_action": container_resolution["action"],
                    "container_status": container_resolution["status"],
                    "best_periodical_id": container_resolution["best_periodical_id"],
                    "best_issue_id": container_resolution["best_issue_id"],
                    "best_collection_id": container_resolution["best_collection_id"],
                    "safe_to_apply_automatically": container_resolution["action"] in {"link_existing_issue", "link_existing_collection"},
                }
            )

    write_tsv(
        run_dir / "match_candidates.tsv",
        match_rows,
        [
            "candidate_id",
            "raw_record_id",
            "editor_source_id",
            "editor_source_number",
            "match_score",
            "match_reasons",
            "candidate_title",
            "editor_title",
            "candidate_year",
            "editor_year",
            "candidate_authors",
            "editor_authors",
        ],
    )
    write_tsv(
        run_dir / "proposed_changes.tsv",
        proposed_rows,
        [
            "candidate_id",
            "raw_record_id",
            "editor_source_id",
            "editor_source_number",
            "field",
            "editor_value",
            "candidate_value",
            "decision",
        ],
    )
    write_tsv(
        run_dir / "conflicts.tsv",
        conflict_rows,
        [
            "candidate_id",
            "raw_record_id",
            "editor_source_id",
            "editor_source_number",
            "field",
            "editor_value",
            "candidate_value",
            "decision",
        ],
    )
    write_tsv(
        run_dir / "new_records.tsv",
        new_rows,
        [
            "candidate_id",
            "raw_record_id",
            "source_type",
            "title",
            "authors",
            "year",
            "notes",
            "confidence",
            "review_note",
        ],
    )
    write_tsv(
        run_dir / "container_resolution.tsv",
        container_resolution_rows,
        [
            "candidate_id",
            "raw_record_id",
            "container_kind",
            "container_title",
            "issue_year",
            "issue_number",
            "volume",
            "part_number",
            "best_periodical_id",
            "best_periodical_title",
            "periodical_score",
            "best_issue_id",
            "best_issue_year",
            "best_issue_number",
            "issue_score",
            "best_collection_id",
            "best_collection_title",
            "collection_score",
            "action",
            "status",
            "review_note",
        ],
    )
    write_json(run_dir / "container_resolution.json", container_resolution_rows)
    write_tsv(
        run_dir / "author_resolution.tsv",
        author_resolution_rows,
        [
            "author_key",
            "candidate_author",
            "candidate_heading_name",
            "candidate_ids",
            "raw_record_ids",
            "example_1",
            "example_2",
            "best_author_id",
            "best_author_display_name",
            "best_author_heading_name",
            "best_author_dates",
            "author_score",
            "author_reasons",
            "action",
            "status",
            "review_note",
        ],
    )
    write_json(run_dir / "author_resolution.json", author_resolution_rows)
    write_json(run_dir / "merge_plan.json", merge_plan)
    write_container_review_html(
        run_dir / "review_containers.html",
        run_dir,
        container_resolution_rows,
    )
    write_author_review_html(
        run_dir / "review_authors.html",
        run_dir,
        author_resolution_rows,
    )
    write_stage3_review_html(
        run_dir / "review_stage3.html",
        run_dir,
        review_new,
        proposed_rows,
        container_resolution_rows,
        author_resolution_rows,
        candidates_by_id,
        review_pairs,
    )
    write_stage2_review_html(
        run_dir / "review_stage2.html",
        run_dir,
        review_pairs,
        review_new,
    )
    write_review_html(
        run_dir / "review_report.html",
        run_dir,
        review_pairs,
        review_new,
        container_resolution_rows,
    )

    print(f"run_dir={run_dir}")
    print(
        "matches={matches} proposed_changes={changes} conflicts={conflicts} new_records={new}".format(
            matches=len(match_rows),
            changes=len(proposed_rows),
            conflicts=len(conflict_rows),
            new=len(new_rows),
        )
    )
    if resolved_candidate_ids:
        print(f"resolved_candidates_skipped={len(resolved_candidate_ids)}")
    print(f"container_resolution={len(container_resolution_rows)}")
    print(f"author_resolution={len(author_resolution_rows)}")
    return 0


def applied_candidate_ids_for_run(run_dir: Path) -> set[str]:
    request_data = read_json(run_dir / "apply_request.json", default={}) or {}
    resolved: set[str] = set()
    for event in request_data.get("history") or []:
        result = event.get("result") or {}
        for key in ("applied_candidate_ids", "already_applied_candidate_ids"):
            values = result.get(key) or []
            if isinstance(values, list):
                resolved.update(str(value) for value in values if value)
    return resolved


def next_run_id(runs_dir: Path, today: str | None = None) -> str:
    today = today or datetime.now().strftime("%Y-%m-%d")
    pattern = re.compile(rf"^{re.escape(today)}-(\d{{3}})$")
    last = 0
    if runs_dir.exists():
        for path in runs_dir.iterdir():
            if path.is_dir():
                match = pattern.match(path.name)
                if match:
                    last = max(last, int(match.group(1)))
    return f"{today}-{last + 1:03d}"


def run_parser(args: argparse.Namespace) -> int:
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise SystemExit(f"Input file does not exist: {input_path}")

    run_id = args.run_id or next_run_id(args.runs_dir)
    run_dir = args.runs_dir / run_id
    if run_dir.exists() and not args.force:
        raise SystemExit(f"Run directory already exists: {run_dir}. Use --force to replace known parser outputs.")
    run_dir.mkdir(parents=True, exist_ok=True)
    if args.force:
        for filename in (
            "run_manifest.json",
            "raw_records.jsonl",
            "parsed_candidates.jsonl",
            "parser_warnings.tsv",
            "parser.sqlite",
        ):
            path = run_dir / filename
            if path.exists():
                path.unlink()

    raw_records = load_raw_records(input_path, args.batch)
    results: list[ParseResult] = []
    for record in raw_records:
        parsed = parse_record(record, run_id)
        if isinstance(parsed, list):
            results.extend(parsed)
        else:
            results.append(parsed)
    warnings = warning_rows(run_id, results)

    manifest = {
        "run_id": run_id,
        "created_at": now_utc(),
        "parser_version": PARSER_VERSION,
        "input_path": str(input_path),
        "input_sha256": sha256_file(input_path),
        "batch_id": args.batch,
        "record_count": len(raw_records),
        "candidate_count": len(results),
        "warning_count": len(warnings),
        "description_status_counts": count_values(result.candidate["description_status"] for result in results),
        "outputs": {
            "run_manifest": "run_manifest.json",
            "raw_records": "raw_records.jsonl",
            "parsed_candidates": "parsed_candidates.jsonl",
            "parser_warnings": "parser_warnings.tsv",
            "parser_sqlite": "parser.sqlite",
        },
        "staging_only": True,
        "editor_database_touched": False,
    }

    raw_rows = [record.__dict__ for record in raw_records]
    candidate_rows = [result.candidate for result in results]
    write_json(run_dir / "run_manifest.json", manifest)
    write_jsonl(run_dir / "raw_records.jsonl", raw_rows)
    write_jsonl(run_dir / "parsed_candidates.jsonl", candidate_rows)
    write_tsv(
        run_dir / "parser_warnings.tsv",
        warnings,
        ["run_id", "candidate_id", "raw_record_id", "severity", "code", "message", "fragment"],
    )
    write_sqlite(run_dir / "parser.sqlite", manifest, raw_records, results)

    if args.write_normalized:
        normalized_path = args.normalized_dir / f"{args.batch or input_path.stem}.jsonl"
        write_jsonl(normalized_path, raw_rows)
        manifest["normalized_text_path"] = str(normalized_path)
        write_json(run_dir / "run_manifest.json", manifest)

    print(f"run_id={run_id}")
    print(f"run_dir={run_dir}")
    print(f"records={len(raw_records)} candidates={len(results)} warnings={len(warnings)}")
    return 0


def count_values(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse raw bibliography records into staging parser-run artifacts."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Create a parser run from .txt or .jsonl input.")
    run.add_argument("--input", required=True, help="Input .txt or .jsonl file.")
    run.add_argument("--batch", help="Batch id, usually matching source/incoming/<batch>.")
    run.add_argument("--run-id", help="Explicit parser run id. Defaults to YYYY-MM-DD-NNN.")
    run.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR, help="Parser runs directory.")
    run.add_argument("--normalized-dir", type=Path, default=DEFAULT_NORMALIZED_DIR, help="Normalized JSONL directory.")
    run.add_argument(
        "--write-normalized",
        action="store_true",
        help="Also write source/normalized_text/<batch>.jsonl from the loaded raw records.",
    )
    run.add_argument("--force", action="store_true", help="Allow replacing known outputs in an existing run directory.")
    run.set_defaults(func=run_parser)

    compare = subparsers.add_parser(
        "compare",
        help="Compare a parser run with the editor database and write review reports.",
    )
    compare.add_argument("--run-dir", type=Path, required=True, help="Parser run directory.")
    compare.add_argument(
        "--editor-db",
        type=Path,
        default=PROJECT_ROOT / "data" / "editor.sqlite",
        help="Editor SQLite database opened read-only.",
    )
    compare.add_argument("--match-limit", type=int, default=5, help="Maximum matches per candidate.")
    compare.add_argument(
        "--new-threshold",
        type=float,
        default=0.7,
        help="Best-match score below this threshold is also reported as a new record candidate.",
    )
    compare.set_defaults(func=run_compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "runs_dir"):
        args.runs_dir = args.runs_dir.expanduser().resolve()
    if hasattr(args, "normalized_dir"):
        args.normalized_dir = args.normalized_dir.expanduser().resolve()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
