#!/usr/bin/env python3
"""Create AI-assisted staging markup for Bibliobon bibliography records."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = PROJECT_ROOT / "docs" / "prompts" / "bibliography_ai_markup_ru.md"
DEFAULT_AI_MARKUP_DIR = PROJECT_ROOT / "source" / "incoming"
DEFAULT_MODEL = os.environ.get("BIBLIOBON_AI_MODEL", "gpt-4.1-mini")
MARKUP_VERSION = "bibliography-ai-markup-0.1"

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from parse_bibliography import clean_record_text, read_jsonl, sha256_text, split_txt_records  # noqa: E402


def now_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def read_prompt(path: Path = PROMPT_PATH) -> str:
    return path.read_text(encoding="utf-8")


def load_input_records(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        rows = split_txt_records(path)
        return [
            {
                "record_id": f"raw-{sha256_text(str(index) + ':' + row['raw_text'])[:16]}",
                "raw_text": row["raw_text"],
                "source_line_start": row.get("source_line_start"),
                "source_line_end": row.get("source_line_end"),
            }
            for index, row in enumerate(rows, start=1)
        ]
    if suffix == ".jsonl":
        records = []
        for index, row in enumerate(read_jsonl(path), start=1):
            raw_text = row.get("raw_text") or row.get("normalized_text") or row.get("text") or row.get("record")
            if not isinstance(raw_text, str) or not raw_text.strip():
                continue
            records.append(
                {
                    "record_id": row.get("raw_record_id") or row.get("record_id") or f"raw-{sha256_text(str(index) + ':' + raw_text)[:16]}",
                    "raw_text": clean_record_text(raw_text),
                    "source_line_start": row.get("source_line_start") or row.get("_line_no"),
                    "source_line_end": row.get("source_line_end") or row.get("_line_no"),
                }
            )
        return records
    raise SystemExit("Input must be .txt or .jsonl")


def output_path_for(batch: str, output: str | None) -> Path:
    if output:
        return Path(output).resolve()
    return DEFAULT_AI_MARKUP_DIR / batch / "ai_markup" / f"{now_stamp()}.jsonl"


def chunks(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def markup_with_mock(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        text = record["raw_text"]
        rows.append(
            {
                "record_id": record["record_id"],
                "raw_text": text,
                "record_type": "book",
                "authors": [],
                "title": text.split(" - ", 1)[0].split(" — ", 1)[0].strip(" ."),
                "subtitle": None,
                "title_remainder": None,
                "responsibility_statement": None,
                "publication_place": None,
                "publisher": None,
                "publication_date": None,
                "extent": None,
                "notes": [],
                "warnings": ["mock-разметка: ИИ не вызывался"],
                "confidence": 0.3,
                "periodical_title": None,
                "issue_year": None,
                "issue_number": None,
                "article_pages": None,
            }
        )
    return rows


def markup_with_openai(records: list[dict[str, Any]], model: str, prompt: str, api_key: str) -> list[dict[str, Any]]:
    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "Разметь библиографические записи и верни JSON по схеме.",
                        "records": [{"record_id": item["record_id"], "raw_text": item["raw_text"]} for item in records],
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "bibliography_markup",
                "strict": True,
                "schema": response_schema(),
            }
        },
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"OpenAI API error {exc.code}: {body}") from exc
    text = extract_output_text(data)
    parsed = json.loads(text)
    return parsed["records"]


def extract_output_text(response: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                parts.append(content.get("text", ""))
    if not parts:
        raise SystemExit(f"OpenAI response did not contain output_text: {json.dumps(response, ensure_ascii=False)[:1000]}")
    return "".join(parts)


def nullable(schema_type: str) -> dict[str, Any]:
    return {"anyOf": [{"type": schema_type}, {"type": "null"}]}


def response_schema() -> dict[str, Any]:
    record = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "record_id": {"type": "string"},
            "raw_text": {"type": "string"},
            "record_type": {
                "type": "string",
                "enum": [
                    "book",
                    "article",
                    "journal_article",
                    "collection_article",
                    "conference_material",
                    "legal_document",
                    "electronic_resource",
                    "unknown",
                ],
            },
            "authors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "display_name": {"type": "string"},
                        "role": {"type": "string"},
                    },
                    "required": ["display_name", "role"],
                },
            },
            "title": nullable("string"),
            "subtitle": nullable("string"),
            "title_remainder": nullable("string"),
            "responsibility_statement": nullable("string"),
            "publication_place": nullable("string"),
            "publisher": nullable("string"),
            "publication_date": nullable("string"),
            "extent": nullable("string"),
            "notes": {"type": "array", "items": {"type": "string"}},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
            "periodical_title": nullable("string"),
            "issue_year": nullable("string"),
            "issue_number": nullable("string"),
            "article_pages": nullable("string"),
        },
        "required": [
            "record_id",
            "raw_text",
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
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "records": {"type": "array", "items": record},
        },
        "required": ["records"],
    }


def validate_markup(input_records: list[dict[str, Any]], markup_rows: list[dict[str, Any]]) -> None:
    expected = {item["record_id"] for item in input_records}
    actual = {item.get("record_id") for item in markup_rows}
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        raise SystemExit(f"AI markup record_id mismatch. Missing: {sorted(missing)} Extra: {sorted(extra)}")


def write_markup(path: Path, source_records: list[dict[str, Any]], markup_rows: list[dict[str, Any]], model: str, mock: bool) -> None:
    by_id = {row["record_id"]: row for row in markup_rows}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for index, source in enumerate(source_records, start=1):
            markup = by_id[source["record_id"]]
            row = {
                "raw_record_id": source["record_id"],
                "raw_text": source["raw_text"],
                "source_line_start": source.get("source_line_start"),
                "source_line_end": source.get("source_line_end"),
                "ai_markup_version": MARKUP_VERSION,
                "ai_model": "mock" if mock else model,
                "ai_markup": markup,
            }
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create AI markup JSONL for Bibliobon parser staging.")
    parser.add_argument("--input", required=True, help="Input .txt or .jsonl file.")
    parser.add_argument("--batch", required=True, help="Incoming batch id.")
    parser.add_argument("--output", help="Output JSONL path. Defaults to source/incoming/<batch>/ai_markup/<timestamp>.jsonl.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"OpenAI model. Default: {DEFAULT_MODEL}.")
    parser.add_argument("--chunk-size", type=int, default=20, help="Records per API request.")
    parser.add_argument("--mock", action="store_true", help="Write deterministic low-confidence markup without calling OpenAI.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = Path(args.input).resolve()
    if not input_path.exists():
        raise SystemExit(f"Input file does not exist: {input_path}")
    if args.chunk_size < 1:
        raise SystemExit("--chunk-size must be >= 1")
    records = load_input_records(input_path)
    if not records:
        raise SystemExit("No input records found.")

    prompt = read_prompt()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not args.mock and not api_key:
        raise SystemExit("Set OPENAI_API_KEY or run with --mock.")

    all_markup: list[dict[str, Any]] = []
    for chunk in chunks(records, args.chunk_size):
        if args.mock:
            all_markup.extend(markup_with_mock(chunk))
        else:
            all_markup.extend(markup_with_openai(chunk, args.model, prompt, api_key or ""))

    validate_markup(records, all_markup)
    output_path = output_path_for(args.batch, args.output)
    write_markup(output_path, records, all_markup, args.model, args.mock)
    print(f"ai_markup_path={output_path}")
    print(f"records={len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
