from pathlib import Path
from shutil import copy2

from django.conf import settings
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from .models import Author, SourceAuthor, WorkAuthor


def merge_authors_plan(source_ids, target_id):
    source_ids = [str(value).strip() for value in source_ids if str(value).strip()]
    target_id = str(target_id or "").strip()
    errors = []
    if not target_id:
        errors.append("Не указан целевой автор.")
    if not source_ids:
        errors.append("Не указаны авторы-дубли.")
    if target_id in source_ids:
        errors.append("Целевой автор не должен быть в списке дублей.")

    target = Author.objects.filter(author_id=target_id).first() if target_id else None
    sources = list(Author.objects.filter(author_id__in=source_ids))
    found_source_ids = {author.author_id for author in sources}
    missing = [author_id for author_id in source_ids if author_id not in found_source_ids]
    if target_id and target is None:
        errors.append(f"Целевой автор не найден: {target_id}.")
    if missing:
        errors.append("Авторы-дубли не найдены: " + ", ".join(missing) + ".")

    work_link_count = WorkAuthor.objects.filter(author_id__in=source_ids).count()
    source_link_count = SourceAuthor.objects.filter(author_id__in=source_ids).count()
    aliases = sorted(alias_values_for_merge(target, sources)) if target else []
    return {
        "errors": errors,
        "target": target,
        "sources": sources,
        "work_link_count": work_link_count,
        "source_link_count": source_link_count,
        "aliases": aliases,
    }


def apply_author_merge(source_ids, target_id, refresh_target=True):
    plan = merge_authors_plan(source_ids, target_id)
    if plan["errors"]:
        return {"error": " ".join(plan["errors"]), "message": ""}

    backup = backup_sqlite_database("before-merge-authors")
    with transaction.atomic():
        target = Author.objects.select_for_update().get(author_id=target_id)
        sources = list(Author.objects.select_for_update().filter(author_id__in=source_ids))
        work_stats = merge_relation_links(WorkAuthor, "work_id", sources, target)
        source_stats = merge_relation_links(SourceAuthor, "source_id", sources, target)
        target.aliases = merged_aliases_text(target, sources)
        target.note = append_note(target.note, "Merged author duplicates: " + ", ".join(author.author_id for author in sources))
        target.save(update_fields=["aliases", "note"])
        deleted = Author.objects.filter(author_id__in=[author.author_id for author in sources]).delete()[0]

    if refresh_target:
        call_command("convert_legacy_to_target", "--apply", "--reset", verbosity=0)

    return {
        "error": "",
        "message": (
            f"Авторы объединены в {target_id}. "
            f"Перенесено связей WorkAuthor: {work_stats['moved']}; объединено дублей связей: {work_stats['merged']}. "
            f"Перенесено связей SourceAuthor: {source_stats['moved']}; объединено дублей связей: {source_stats['merged']}. "
            f"Удалено строк авторов: {deleted}. Backup: {backup}."
        ),
    }


def merge_relation_links(model, owner_field, sources, target):
    moved = 0
    merged = 0
    for source_author in sources:
        for link in model.objects.filter(author=source_author).order_by("sort_order", "id"):
            owner_id = getattr(link, owner_field)
            existing = model.objects.filter(**{owner_field: owner_id, "author": target, "role": link.role}).first()
            if existing:
                fill_empty_link_fields(existing, link)
                existing.save()
                link.delete()
                merged += 1
            else:
                link.author = target
                if not link.name_as_printed:
                    link.name_as_printed = source_author.display_name
                if not link.source_text:
                    link.source_text = source_author.display_name
                link.save(update_fields=["author", "name_as_printed", "source_text"])
                moved += 1
    return {"moved": moved, "merged": merged}


def fill_empty_link_fields(target_link, source_link):
    for field in ["source_text", "name_as_printed"]:
        if not getattr(target_link, field) and getattr(source_link, field):
            setattr(target_link, field, getattr(source_link, field))
    if not target_link.sort_order and source_link.sort_order:
        target_link.sort_order = source_link.sort_order
    target_link.include_in_responsibility = target_link.include_in_responsibility or source_link.include_in_responsibility
    target_link.is_primary_heading = target_link.is_primary_heading or source_link.is_primary_heading


def merged_aliases_text(target, sources):
    values = alias_values_for_merge(target, sources)
    values.discard(target.display_name.strip())
    return "\n".join(sorted(values, key=lambda value: value.casefold()))


def alias_values_for_merge(target, sources):
    values = set(split_aliases(target.aliases if target else ""))
    for author in sources:
        values.add(author.display_name.strip())
        if author.heading_name:
            values.add(author.heading_name.strip())
        if author.sort_name:
            values.add(author.sort_name.strip())
        values.update(split_aliases(author.aliases))
    return {value for value in values if value}


def split_aliases(value):
    aliases = []
    for line in str(value or "").replace(";", "\n").splitlines():
        item = line.strip()
        if item:
            aliases.append(item)
    return aliases


def append_note(existing, note):
    return f"{existing}\n{note}".strip() if existing else note


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
