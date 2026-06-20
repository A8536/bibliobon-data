#!/usr/bin/env python3
"""Audit Bibliobon field names across models, sheets, contract, and artifact."""

from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EDITOR_ROOT = PROJECT_ROOT / "editor"
REPORTS_ROOT = PROJECT_ROOT / "reports"
FIELD_CONTRACT = PROJECT_ROOT / "docs" / "FIELD_CONTRACT.md"
SITE_CONTRACT = PROJECT_ROOT / "data" / "site_contract.json"
SITE_SQLITE = PROJECT_ROOT / "data" / "bibliobon.sqlite"


@dataclass(frozen=True)
class InventoryItem:
    surface: str
    owner: str
    field: str
    full_name: str
    status: str
    note: str = ""


def main() -> int:
    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    contract_rows = parse_field_contract(FIELD_CONTRACT)
    canonical_names = {row["canonical_name"] for row in contract_rows}
    contract_google_headers = {
        header.strip()
        for row in contract_rows
        for header in (row.get("google_sheet_header") or "").split(";")
        if header.strip()
    }
    contract_artifact_columns = {
        item.strip()
        for row in contract_rows
        for item in (row.get("artifact_column") or "").split(";")
        if item.strip()
    }

    inventory: list[InventoryItem] = []
    inventory.extend(model_inventory())
    inventory.extend(google_sheet_inventory())
    inventory.extend(site_contract_inventory())
    inventory.extend(sqlite_inventory())
    inventory.extend(field_contract_inventory(contract_rows))

    findings = audit_inventory(inventory, contract_rows, canonical_names, contract_artifact_columns, contract_google_headers)
    write_inventory_json(REPORTS_ROOT / "field_contract_inventory.json", inventory, findings)
    write_inventory_tsv(REPORTS_ROOT / "field_contract_audit.tsv", inventory, findings)
    write_report_md(REPORTS_ROOT / "field_contract_audit.md", inventory, findings)

    print(f"wrote {REPORTS_ROOT / 'field_contract_audit.md'}")
    print(f"wrote {REPORTS_ROOT / 'field_contract_audit.tsv'}")
    print(f"wrote {REPORTS_ROOT / 'field_contract_inventory.json'}")
    print(f"findings={len(findings)}")
    return 1 if any(item["severity"] == "error" for item in findings) else 0


def parse_field_contract(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    headers: list[str] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or set(cells[0]) <= {"-", ":"}:
            continue
        if headers is None:
            headers = cells
            continue
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells)))
    return rows


def model_inventory() -> list[InventoryItem]:
    sys.path.insert(0, str(EDITOR_ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from django.apps import apps

    items: list[InventoryItem] = []
    for model in apps.get_app_config("sources").get_models():
        for field in model._meta.get_fields():
            if field.auto_created and not field.concrete:
                continue
            field_name = getattr(field, "name", "")
            if not field_name:
                continue
            items.append(
                InventoryItem(
                    surface="model",
                    owner=model.__name__,
                    field=field_name,
                    full_name=f"{model.__name__}.{field_name}",
                    status="present",
                    note=field.__class__.__name__,
                )
            )
    return items


def google_sheet_inventory() -> list[InventoryItem]:
    sys.path.insert(0, str(EDITOR_ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from sources.google_sheets import SHEETS

    items: list[InventoryItem] = []
    for sheet_name, headers in SHEETS.items():
        for header in headers:
            items.append(
                InventoryItem(
                    surface="google_sheet",
                    owner=sheet_name,
                    field=header,
                    full_name=f"{sheet_name}.{header}",
                    status="present",
                )
            )
    return items


def site_contract_inventory() -> list[InventoryItem]:
    if not SITE_CONTRACT.exists():
        return [
            InventoryItem(
                surface="site_contract",
                owner="data",
                field="site_contract.json",
                full_name="data/site_contract.json",
                status="missing",
                note="file missing",
            )
        ]
    data = json.loads(SITE_CONTRACT.read_text(encoding="utf-8"))
    items: list[InventoryItem] = []
    for table, columns in (data.get("tables") or {}).items():
        for column in columns:
            items.append(
                InventoryItem(
                    surface="site_contract",
                    owner=table,
                    field=column,
                    full_name=f"{table}.{column}",
                    status="present",
                )
            )
    return items


def sqlite_inventory() -> list[InventoryItem]:
    if not SITE_SQLITE.exists():
        return [
            InventoryItem(
                surface="artifact_sqlite",
                owner="data",
                field="bibliobon.sqlite",
                full_name="data/bibliobon.sqlite",
                status="missing",
                note="file missing",
            )
        ]
    items: list[InventoryItem] = []
    with sqlite3.connect(SITE_SQLITE) as conn:
        table_names = [
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            if row[0] != "sqlite_sequence"
        ]
        for table in table_names:
            for col in conn.execute(f"PRAGMA table_info({quote_identifier(table)})"):
                column = col[1]
                items.append(
                    InventoryItem(
                        surface="artifact_sqlite",
                        owner=table,
                        field=column,
                        full_name=f"{table}.{column}",
                        status="present",
                        note=col[2],
                    )
                )
    return items


def field_contract_inventory(rows: list[dict[str, str]]) -> list[InventoryItem]:
    items: list[InventoryItem] = []
    for row in rows:
        canonical = row.get("canonical_name") or ""
        if not canonical:
            continue
        items.append(
            InventoryItem(
                surface="field_contract",
                owner=row.get("model") or "",
                field=canonical,
                full_name=canonical,
                status=row.get("status") or "",
                note=row.get("notes") or "",
            )
        )
    return items


def audit_inventory(
    inventory: list[InventoryItem],
    contract_rows: list[dict[str, str]],
    canonical_names: set[str],
    contract_artifact_columns: set[str],
    contract_google_headers: set[str],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    by_surface_full = {(item.surface, item.full_name): item for item in inventory}
    by_surface_field: dict[tuple[str, str], list[InventoryItem]] = {}
    for item in inventory:
        by_surface_field.setdefault((item.surface, item.field), []).append(item)

    for item in inventory:
        if item.field == "publication_details_raw":
            findings.append(
                finding(
                    "error",
                    "deprecated_field_name",
                    item.surface,
                    item.full_name,
                    "Use canonical raw_publication_details instead of publication_details_raw.",
                )
            )
        if item.surface in {"google_sheet", "site_contract", "artifact_sqlite"}:
            if not is_known_contract_item(item, canonical_names, contract_artifact_columns, contract_google_headers):
                findings.append(
                    finding(
                        "warning",
                        "orphan_field",
                        item.surface,
                        item.full_name,
                        "Field is not covered by docs/FIELD_CONTRACT.md.",
                    )
                )

    for row in contract_rows:
        if (row.get("status") or "") not in {"active", "compatibility"}:
            continue
        for artifact_column in split_contract_cells(row.get("artifact_column")):
            if "." not in artifact_column or "*" in artifact_column:
                continue
            if artifact_column and ("artifact_sqlite", artifact_column) not in by_surface_full:
                findings.append(
                    finding(
                        "warning",
                        "contract_artifact_missing",
                        "artifact_sqlite",
                        artifact_column,
                        f"FIELD_CONTRACT declares {row.get('canonical_name')} here, but bibliobon.sqlite does not expose it.",
                    )
                )
            if artifact_column and ("site_contract", artifact_column) not in by_surface_full:
                findings.append(
                    finding(
                        "warning",
                        "contract_site_contract_missing",
                        "site_contract",
                        artifact_column,
                        f"FIELD_CONTRACT declares {row.get('canonical_name')} here, but site_contract.json does not expose it.",
                    )
                )
        for header in split_contract_cells(row.get("google_sheet_header")):
            if not header:
                continue
            if ("google_sheet", header) in by_surface_field:
                continue
            findings.append(
                finding(
                    "warning",
                    "contract_google_header_missing",
                    "google_sheet",
                    header,
                    f"FIELD_CONTRACT declares Google Sheets header for {row.get('canonical_name')}, but active SHEETS does not expose it.",
                )
            )

    required_source_fields = {"raw_publication_details", "data_source", "first_seen_at", "updated_at"}
    for field_name in required_source_fields:
        if ("model", f"Source.{field_name}") not in by_surface_full:
            findings.append(
                finding(
                    "error",
                    "required_source_field_missing",
                    "model",
                    f"Source.{field_name}",
                    "Canonical Source field is missing from editor model.",
                )
            )
        if ("artifact_sqlite", f"sources.{field_name}") not in by_surface_full:
            findings.append(
                finding(
                    "warning",
                    "required_artifact_field_missing",
                    "artifact_sqlite",
                    f"sources.{field_name}",
                    "Canonical Source field is missing from current exported artifact.",
                )
            )
        if ("site_contract", f"sources.{field_name}") not in by_surface_full:
            findings.append(
                finding(
                    "warning",
                    "required_site_contract_field_missing",
                    "site_contract",
                    f"sources.{field_name}",
                    "Canonical Source field is missing from current site_contract.json.",
                )
            )
    return findings


def is_known_contract_item(
    item: InventoryItem,
    canonical_names: set[str],
    contract_artifact_columns: set[str],
    contract_google_headers: set[str],
) -> bool:
    if item.field in canonical_names:
        return True
    if item.surface == "google_sheet" and item.field in contract_google_headers:
        return True
    if item.full_name in contract_artifact_columns:
        return True
    if item.field.startswith("legacy_") or item.field in {"id", "source_django_id"}:
        return True
    if item.surface == "artifact_sqlite" and item.owner == "export_metadata":
        return True
    return False


def split_contract_cells(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(";") if item.strip()]


def finding(severity: str, code: str, surface: str, field: str, message: str) -> dict[str, str]:
    return {
        "severity": severity,
        "code": code,
        "surface": surface,
        "field": field,
        "message": message,
    }


def write_inventory_json(path: Path, inventory: list[InventoryItem], findings: list[dict[str, str]]) -> None:
    payload = {
        "generated_for": "bibliobon_field_contract",
        "counts": {
            "inventory": len(inventory),
            "findings": len(findings),
        },
        "findings": findings,
        "inventory": [item.__dict__ for item in inventory],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_inventory_tsv(path: Path, inventory: list[InventoryItem], findings: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["kind", "severity", "code", "surface", "owner", "field", "full_name", "status", "note", "message"],
            delimiter="\t",
        )
        writer.writeheader()
        for item in inventory:
            writer.writerow(
                {
                    "kind": "inventory",
                    "severity": "",
                    "code": "",
                    "surface": item.surface,
                    "owner": item.owner,
                    "field": item.field,
                    "full_name": item.full_name,
                    "status": item.status,
                    "note": item.note,
                    "message": "",
                }
            )
        for item in findings:
            writer.writerow(
                {
                    "kind": "finding",
                    "severity": item["severity"],
                    "code": item["code"],
                    "surface": item["surface"],
                    "owner": "",
                    "field": item["field"],
                    "full_name": item["field"],
                    "status": "",
                    "note": "",
                    "message": item["message"],
                }
            )


def write_report_md(path: Path, inventory: list[InventoryItem], findings: list[dict[str, str]]) -> None:
    by_surface: dict[str, list[InventoryItem]] = {}
    for item in inventory:
        by_surface.setdefault(item.surface, []).append(item)
    lines = [
        "# Field Contract Audit",
        "",
        "Generated by `scripts/audit_field_contract.py`.",
        "",
        "## Summary",
        "",
        "| item | count |",
        "|---|---:|",
        f"| inventory rows | {len(inventory)} |",
        f"| findings | {len(findings)} |",
    ]
    for surface, items in sorted(by_surface.items()):
        lines.append(f"| {surface} fields | {len(items)} |")
    lines.extend(["", "## Findings", ""])
    if findings:
        lines.extend(["| severity | code | surface | field | message |", "|---|---|---|---|---|"])
        for item in findings:
            lines.append(
                "| {severity} | {code} | {surface} | `{field}` | {message} |".format(
                    severity=item["severity"],
                    code=item["code"],
                    surface=item["surface"],
                    field=item["field"],
                    message=item["message"],
                )
            )
    else:
        lines.append("No findings.")

    lines.extend(["", "## Inventory", ""])
    for surface, items in sorted(by_surface.items()):
        lines.extend([f"### {surface}", "", "| owner | field | status | note |", "|---|---|---|---|"])
        for item in sorted(items, key=lambda value: (value.owner, value.field)):
            lines.append(f"| {item.owner} | `{item.field}` | {item.status} | {item.note} |")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


if __name__ == "__main__":
    raise SystemExit(main())
