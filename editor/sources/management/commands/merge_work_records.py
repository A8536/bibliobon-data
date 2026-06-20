from pathlib import Path
from shutil import copy2

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from sources.models import Article, Collection, Source, Work, WorkAuthor, WorkGroupItem, WorkTag


class Command(BaseCommand):
    help = "Safely merges one duplicate Work into another Work."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, help="Duplicate work_id to merge/delete.")
        parser.add_argument("--target", required=True, help="Canonical work_id to keep.")
        parser.add_argument("--apply", action="store_true", help="Apply merge. Default is dry-run.")
        parser.add_argument("--skip-target-refresh", action="store_true", help="Do not rebuild target tables.")

    def handle(self, *args, **options):
        source = get_work(options["source"], "source")
        target = get_work(options["target"], "target")
        if source.work_id == target.work_id:
            raise CommandError("Source and target must be different.")

        plan = build_plan(source, target)
        print_plan(self, plan)
        if plan["blocking_errors"]:
            raise CommandError("Merge has blocking errors; nothing changed.")
        if not options["apply"]:
            self.stdout.write(self.style.WARNING("Dry run: ничего не изменено. Добавьте --apply для слияния."))
            return

        backup = backup_sqlite_database("before-merge-work-records")
        if backup:
            self.stdout.write(self.style.WARNING(f"Backup создан: {backup}"))

        with transaction.atomic():
            stats = apply_merge(source, target)

        if not options["skip_target_refresh"]:
            call_command("convert_legacy_to_target", "--apply", "--reset", verbosity=0)

        self.stdout.write(self.style.SUCCESS(f"Слияние завершено: {stats}"))


def get_work(work_id, label):
    try:
        return Work.objects.get(work_id=work_id)
    except Work.DoesNotExist as exc:
        raise CommandError(f"{label} work not found: {work_id}") from exc


def build_plan(source, target):
    source_article = Article.objects.filter(work=source).first()
    target_article = Article.objects.filter(work=target).first()
    blocking_errors = []
    if source_article and target_article:
        blocking_errors.append("Both source and target are article records.")
    if source.contained_articles.exists() and target_article:
        blocking_errors.append("Source is a container but target is an article record.")
    source_collections = Collection.objects.filter(parent_work=source)
    target_collection = Collection.objects.filter(parent_work=target).first()

    return {
        "source": source.work_id,
        "source_title": source.title,
        "target": target.work_id,
        "target_title": target.title,
        "source_authors": WorkAuthor.objects.filter(work=source).count(),
        "source_tags": WorkTag.objects.filter(work=source).count(),
        "source_article": source_article.article_id if source_article else "",
        "source_contained_articles": source.contained_articles.count(),
        "source_groups": WorkGroupItem.objects.filter(work=source).count(),
        "source_legacy_collections": source_collections.count(),
        "source_legacy_collection_articles": Article.objects.filter(collection__parent_work=source).count(),
        "target_legacy_collection": target_collection.collection_id if target_collection else "",
        "source_target_source": Source.objects.filter(source_id=source.work_id).exists(),
        "blocking_errors": blocking_errors,
    }


def print_plan(command, plan):
    for key, value in plan.items():
        command.stdout.write(f"{key}: {value}")


def apply_merge(source, target):
    stats = {
        "authors_moved": 0,
        "tags_moved": 0,
        "article_moved": 0,
        "contained_articles_moved": 0,
        "groups_moved": 0,
        "legacy_collections_moved": 0,
        "legacy_collection_articles_moved": 0,
        "source_deleted": source.work_id,
    }

    for link in WorkAuthor.objects.filter(work=source).select_related("author"):
        _, created = WorkAuthor.objects.get_or_create(
            work=target,
            author=link.author,
            role=link.role,
            defaults={
                "sort_order": link.sort_order,
                "source_text": link.source_text,
                "name_as_printed": link.name_as_printed,
                "include_in_responsibility": link.include_in_responsibility,
                "is_primary_heading": link.is_primary_heading,
            },
        )
        stats["authors_moved"] += int(created)

    for link in WorkTag.objects.filter(work=source).select_related("tag"):
        _, created = WorkTag.objects.get_or_create(
            work=target,
            tag=link.tag,
            defaults={"sort_order": link.sort_order, "source_text": link.source_text},
        )
        stats["tags_moved"] += int(created)

    source_article = Article.objects.filter(work=source).first()
    if source_article:
        source_article.work = target
        source_article.save(update_fields=["work"])
        stats["article_moved"] = 1

    moved_contained = Article.objects.filter(container_work=source).update(container_work=target)
    stats["contained_articles_moved"] = moved_contained

    for item in WorkGroupItem.objects.filter(work=source).select_related("group"):
        _, created = WorkGroupItem.objects.get_or_create(
            group=item.group,
            work=target,
            defaults={"sort_order": item.sort_order},
        )
        stats["groups_moved"] += int(created)

    stats.update(merge_legacy_collections(source, target))

    Source.objects.filter(source_id=source.work_id).delete()
    source.delete()
    return stats


def merge_legacy_collections(source, target):
    stats = {
        "legacy_collections_moved": 0,
        "legacy_collection_articles_moved": 0,
    }
    source_collections = list(Collection.objects.filter(parent_work=source))
    if not source_collections:
        return stats

    target_collection = Collection.objects.filter(parent_work=target).first()
    if not target_collection:
        for collection in source_collections:
            collection.parent_work = target
            collection.save(update_fields=["parent_work"])
            stats["legacy_collections_moved"] += 1
        return stats

    for collection in source_collections:
        moved_articles = Article.objects.filter(collection=collection).update(collection=target_collection)
        stats["legacy_collection_articles_moved"] += moved_articles
        copy_blank_collection_fields(target_collection, collection)
        collection.delete()
        stats["legacy_collections_moved"] += 1

    return stats


def copy_blank_collection_fields(target, source):
    update_fields = []
    for field in ("publication_details", "place", "publisher", "source_text"):
        if not getattr(target, field) and getattr(source, field):
            setattr(target, field, getattr(source, field))
            update_fields.append(field)
    if target.year is None and source.year is not None:
        target.year = source.year
        update_fields.append("year")
    if update_fields:
        target.save(update_fields=update_fields)


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
