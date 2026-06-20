import json
import re
from dataclasses import dataclass
from pathlib import Path
from shutil import copy2

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Article, ArticlePlacement, Issue, Journal, JournalIssue, Periodical


def normalize_issue_piece(value):
    value = (value or "").lower().replace("ё", "е").replace("–", "-").replace("—", "-")
    value = re.sub(r"(?:\b(?:n|no|номер)\b|№)\s*", "", value)
    value = re.sub(r"\s+", "", value)
    return value.strip(" .,;")


def issue_key(issue):
    return (
        str(issue.year or ""),
        normalize_issue_piece(issue.issue_number),
        normalize_issue_piece(issue.volume),
        normalize_issue_piece(issue.part_number),
        normalize_issue_piece(issue.gross_number),
    )


def issue_label(issue):
    bits = [issue.journal.title]
    if issue.year:
        bits.append(str(issue.year))
    if issue.issue_number:
        bits.append(f"№ {issue.issue_number}")
    if issue.volume:
        bits.append(f"т. {issue.volume}")
    if issue.part_number:
        bits.append(f"ч. {issue.part_number}")
    if issue.gross_number:
        bits.append(f"общ. № {issue.gross_number}")
    return ", ".join(bits)


def target_issue_label(issue):
    bits = []
    if issue.periodical:
        bits.append(issue.periodical.title)
    elif issue.source:
        bits.append(issue.source.title)
    elif issue.title:
        bits.append(issue.title)
    else:
        bits.append(issue.issue_id)
    if issue.year:
        bits.append(str(issue.year))
    if issue.issue_number:
        bits.append(f"№ {issue.issue_number}")
    if issue.volume:
        bits.append(f"т. {issue.volume}")
    return ", ".join(bits)


def issue_field_differences(source_issue, target_issue):
    fields = ["title", "publication_date", "volume", "part_number", "gross_number", "publication_details", "enumeration", "date_text"]
    differences = []
    for field in fields:
        source_value = str(getattr(source_issue, field, "") or "").strip()
        target_value = str(getattr(target_issue, field, "") or "").strip()
        if source_value and target_value and normalize_issue_piece(source_value) != normalize_issue_piece(target_value):
            differences.append({"field": field, "source": source_value, "target": target_value})
    return differences


@dataclass
class JournalNormalizationPlan:
    source: Journal
    target: Journal
    rows: list
    totals: dict

    @property
    def can_apply(self):
        return any(row["action"] in {"move", "merge"} for row in self.rows)


def build_journal_normalization_plan(source_journal_id, target_journal_id):
    source = Journal.objects.get(journal_id=source_journal_id)
    target = Journal.objects.get(journal_id=target_journal_id)
    target_by_key = {}
    for issue in JournalIssue.objects.filter(journal=target).select_related("journal", "target_issue"):
        target_by_key.setdefault(issue_key(issue), []).append(issue)

    rows = []
    for source_issue in (
        JournalIssue.objects.filter(journal=source)
        .select_related("journal", "target_issue")
        .order_by("year", "issue_number", "volume", "part_number", "gross_number", "journal_issue_id")
    ):
        matches = target_by_key.get(issue_key(source_issue), [])
        legacy_article_count = Article.objects.filter(journal_issue=source_issue).count()
        placement_count = normalized_placements_for_legacy_issue(source_issue).count()
        samples = list(
            Article.objects.filter(journal_issue=source_issue)
            .select_related("work")
            .order_by("work__source_sequence", "work__source_number")[:5]
        )
        if not matches:
            action = "move"
            target_issue = None
            notes = ["Совпадающий выпуск в правильном журнале не найден."]
            differences = []
        elif len(matches) == 1:
            action = "merge"
            target_issue = matches[0]
            differences = issue_field_differences(source_issue, target_issue)
            notes = ["Будет слит с существующим выпуском правильного журнала."]
            if differences:
                notes.append("Есть отличающиеся поля выпуска; они будут сохранены в отчёте, без перезаписи.")
        else:
            action = "review"
            target_issue = None
            differences = []
            notes = [f"Найдено несколько выпусков с тем же годом/номером: {len(matches)}. Нужен ручной выбор."]
        rows.append(
            {
                "source_issue": source_issue,
                "source_label": issue_label(source_issue),
                "action": action,
                "action_label": action_label(action),
                "target_issue": target_issue,
                "target_label": issue_label(target_issue) if target_issue else "",
                "article_count": legacy_article_count,
                "placement_count": placement_count,
                "sample_articles": samples,
                "notes": notes,
                "differences": differences,
                "target_candidates": matches,
            }
        )

    totals = {
        "source_issues": len(rows),
        "move": sum(1 for row in rows if row["action"] == "move"),
        "merge": sum(1 for row in rows if row["action"] == "merge"),
        "review": sum(1 for row in rows if row["action"] == "review"),
        "articles": sum(row["article_count"] for row in rows),
        "placements": sum(row["placement_count"] for row in rows),
    }
    return JournalNormalizationPlan(source=source, target=target, rows=rows, totals=totals)


def action_label(action):
    return {
        "move": "Выпуск будет перенесён",
        "merge": "Выпуск будет слит с существующим",
        "review": "Требует ручной проверки",
        "skip": "Пропустить",
    }.get(action, action)


def normalized_placements_for_legacy_issue(legacy_issue):
    target_issue = target_issue_for_legacy_issue(legacy_issue)
    if not target_issue:
        return ArticlePlacement.objects.none()
    return ArticlePlacement.objects.filter(issue=target_issue)


def target_issue_for_legacy_issue(legacy_issue):
    try:
        return legacy_issue.target_issue
    except Issue.DoesNotExist:
        return None


def target_periodical_for_legacy_journal(journal):
    try:
        return journal.target_periodical
    except Periodical.DoesNotExist:
        return None


def apply_journal_normalization_plan(source_journal_id, target_journal_id, user=None):
    plan = build_journal_normalization_plan(source_journal_id, target_journal_id)
    backup_path = backup_sqlite_database("before-journal-normalization")
    result = {
        "source_journal": {"id": plan.source.journal_id, "title": plan.source.title},
        "target_journal": {"id": plan.target.journal_id, "title": plan.target.title},
        "backup_path": str(backup_path) if backup_path else "",
        "moved_issues": [],
        "merged_issues": [],
        "skipped_issues": [],
        "moved_articles": 0,
        "moved_placements": 0,
        "user": getattr(user, "username", "") if user else "",
        "timestamp": timezone.now().isoformat(),
    }
    with transaction.atomic():
        for row in plan.rows:
            if row["action"] == "move":
                apply_move_issue(row, plan.target, result)
            elif row["action"] == "merge":
                apply_merge_issue(row, result)
            else:
                result["skipped_issues"].append(
                    {
                        "source_issue_id": row["source_issue"].journal_issue_id,
                        "source_label": row["source_label"],
                        "reason": "; ".join(row["notes"]),
                    }
                )
    report_path = write_journal_normalization_report(result)
    result["report_path"] = str(report_path)
    return result


def apply_move_issue(row, target_journal, result):
    source_issue = row["source_issue"]
    source_issue.journal = target_journal
    source_issue.save(update_fields=["journal"])
    moved_placements = 0
    target_periodical = target_periodical_for_legacy_journal(target_journal)
    target_issue = target_issue_for_legacy_issue(source_issue)
    if target_issue and target_periodical:
        target_issue.periodical = target_periodical
        target_issue.save(update_fields=["periodical"])
        moved_placements = ArticlePlacement.objects.filter(issue=target_issue).count()
    result["moved_issues"].append(
        {
            "source_issue_id": source_issue.journal_issue_id,
            "source_label": row["source_label"],
            "new_label": issue_label(source_issue),
            "article_count": row["article_count"],
            "placement_count": moved_placements,
        }
    )


def apply_merge_issue(row, result):
    source_issue = row["source_issue"]
    target_issue = row["target_issue"]
    article_count = Article.objects.filter(journal_issue=source_issue).update(journal_issue=target_issue)
    placement_count = 0
    source_target_issue = target_issue_for_legacy_issue(source_issue)
    target_target_issue = target_issue_for_legacy_issue(target_issue)
    if source_target_issue and target_target_issue:
        placement_count = ArticlePlacement.objects.filter(issue=source_target_issue).update(issue=target_target_issue)
    result["moved_articles"] += article_count
    result["moved_placements"] += placement_count
    result["merged_issues"].append(
        {
            "source_issue_id": source_issue.journal_issue_id,
            "source_label": row["source_label"],
            "target_issue_id": target_issue.journal_issue_id,
            "target_label": issue_label(target_issue),
            "article_count": article_count,
            "placement_count": placement_count,
            "field_differences": row["differences"],
        }
    )


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


def write_journal_normalization_report(result):
    reports_dir = settings.PROJECT_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    path = reports_dir / f"journal_normalization.{timestamp}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
