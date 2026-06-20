import csv
import json
import re
import subprocess
import sys
from collections import defaultdict

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpResponse, JsonResponse
from django.core.management import call_command
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.csrf import ensure_csrf_cookie
from pathlib import Path
from shutil import copy2, rmtree

from .models import Article, ArticlePlacement, Author, Book, Collection, ImportApplyLog, ImportBatch, ImportDecision, ImportEntity, ImportEntityRelation, ImportGroup, ImportItem, Issue, Journal, JournalIssue, Language, Periodical, Section, Source, Tag, Work, WorkAuthor, WorkGroupItem, WorkTag
from .author_merge import apply_author_merge, merge_authors_plan
from .google_sheets import build_export_values, get_sheets_service, read_sheet_values, write_sheet_values
from .journal_normalization import apply_journal_normalization_plan, build_journal_normalization_plan
from .issue_collection_conversion import apply_issue_to_collection, build_issue_to_collection_plan
from .import_workflow import (
    apply_entity_decision,
    apply_import_batch,
    apply_item_decision,
    article_issue_match_context,
    batch_status_label_ru,
    build_import_plan,
    compare_work_to_parsed_data,
    comparison_status_label_ru,
    contributor_role_label_ru,
    describe_existing_entity,
    decision_type_label_ru,
    entity_status_label_ru,
    entity_type_label_ru,
    group_container_relation_type,
    group_status_label_ru,
    group_type_label_ru,
    author_entity_only_used_by_auto_resolved_existing_articles,
    is_auto_resolved_existing_article,
    item_detected_type_label_ru,
    item_status_label_ru,
    move_article_to_group,
    parse_import_batch,
    reconcile_import_auto_links,
    refresh_item_from_manual_parse,
    split_article_to_new_group,
)
from .management.commands.cleanup_redundant_fields import apply_cleanup as apply_redundant_cleanup
from .management.commands.cleanup_redundant_fields import cleanup_plan as redundant_cleanup_plan
from .management.commands.import_google_sheet import backup_sqlite_database as backup_google_import_database
from .management.commands.import_google_sheet import import_values as import_google_values


PROJECT_ROOT = settings.PROJECT_ROOT
PARSER_SCRIPT = PROJECT_ROOT / "scripts" / "parse_bibliography.py"
AI_MARKUP_SCRIPT = PROJECT_ROOT / "scripts" / "ai_markup_bibliography.py"
INCOMING_ROOT = PROJECT_ROOT / "source" / "incoming"
PARSER_RUNS_ROOT = PROJECT_ROOT / "data" / "parser_runs"
PARSER_REVIEW_TEMPLATES_ROOT = PROJECT_ROOT / "data" / "parser_review_templates"
EDITOR_DB = PROJECT_ROOT / "data" / "editor.sqlite"


def reconcile_import_for_review(batch):
    if batch.status != ImportBatch.Status.APPLIED:
        reconcile_import_auto_links(batch)


@staff_member_required
def import_batch_list(request):
    queryset = (
        ImportBatch.objects.annotate(
            item_count=Count("items", distinct=True),
            unresolved_count=Count("entities", filter=Q(entities__status=ImportEntity.Status.UNRESOLVED), distinct=True),
            ready_count=Count("entities", filter=Q(entities__status__in=[ImportEntity.Status.WILL_CREATE, ImportEntity.Status.LINKED_EXISTING, ImportEntity.Status.WILL_UPDATE_EXISTING]), distinct=True),
        )
        .select_related("created_by")
        .order_by("-created_at", "-id")
    )
    batches = list(queryset.exclude(status=ImportBatch.Status.APPLIED))
    applied_batches = list(queryset.filter(status=ImportBatch.Status.APPLIED))
    decorate_import_batches(batches)
    decorate_import_batches(applied_batches)
    return render(request, "sources/imports/list.html", {"batches": batches, "applied_batches": applied_batches})


@staff_member_required
def import_batch_cleanup(request):
    if request.method != "POST":
        raise Http404("Cleanup endpoint accepts POST only.")
    statuses = [
        ImportBatch.Status.DRAFT,
        ImportBatch.Status.PARSED,
        ImportBatch.Status.REVIEW_REQUIRED,
        ImportBatch.Status.READY_TO_APPLY,
        ImportBatch.Status.CANCELLED,
    ]
    queryset = ImportBatch.objects.filter(status__in=statuses)
    deleted_preview = list(queryset.order_by("-created_at", "-id").values_list("title", flat=True)[:10])
    count = queryset.count()
    queryset.delete()
    if count:
        sample = "; ".join(deleted_preview)
        if count > len(deleted_preview):
            sample += f"; ещё {count - len(deleted_preview)}"
        messages.success(request, f"Удалены черновики импорта: {count}. Библиографические записи не изменялись. Примеры: {sample}")
    else:
        messages.info(request, "Черновиков импорта для удаления нет. Библиографические записи не изменялись.")
    return redirect("sources:import_batch_list")


@staff_member_required
def import_batch_cleanup_applied(request):
    if request.method != "POST":
        raise Http404("Applied import cleanup endpoint accepts POST only.")
    queryset = ImportBatch.objects.filter(status=ImportBatch.Status.APPLIED)
    deleted_preview = list(queryset.order_by("-created_at", "-id").values_list("title", flat=True)[:10])
    count = queryset.count()
    queryset.delete()
    if count:
        sample = "; ".join(deleted_preview)
        if count > len(deleted_preview):
            sample += f"; ещё {count - len(deleted_preview)}"
        messages.success(
            request,
            f"Удалена история применённых импортов: {count}. Библиографические записи не изменялись. Примеры: {sample}",
        )
    else:
        messages.info(request, "Истории применённых импортов для удаления нет. Библиографические записи не изменялись.")
    return redirect("sources:import_batch_list")


@staff_member_required
def import_batch_new(request):
    if request.method == "POST":
        title = request.POST.get("title", "").strip() or "Новый импорт"
        source_name = request.POST.get("source_name", "").strip()
        source_type = request.POST.get("source_type", ImportBatch.SourceType.PLAIN_TEXT)
        raw_input = request.POST.get("raw_input", "")
        uploaded = request.FILES.get("source_file")
        if uploaded:
            source_type = ImportBatch.SourceType.FILE
            source_name = source_name or uploaded.name
            raw_input = uploaded.read().decode("utf-8-sig")
        if not raw_input.strip():
            messages.error(request, "Добавьте исходный текст или загрузите .txt файл.")
            return redirect("sources:import_batch_new")
        batch = ImportBatch.objects.create(
            title=title,
            source_name=source_name,
            source_type=source_type,
            raw_input=raw_input,
            notes=request.POST.get("notes", ""),
            created_by=request.user if request.user.is_authenticated else None,
        )
        if request.POST.get("parse_now") == "1":
            parse_import_batch(batch)
            messages.success(request, "Импорт создан и разобран.")
            return redirect("sources:import_batch_review", pk=batch.pk)
        messages.success(request, "Импорт создан.")
        return redirect("sources:import_batch_detail", pk=batch.pk)
    return render(request, "sources/imports/new.html", {"source_types": ImportBatch.SourceType.choices})


@staff_member_required
def import_batch_detail(request, pk):
    batch = get_object_or_404(ImportBatch.objects.select_related("created_by"), pk=pk)
    decorate_import_batches([batch])
    context = {
        "batch": batch,
        "is_applied": batch.status == ImportBatch.Status.APPLIED,
        "stats": import_batch_stats(batch),
        "plan": build_import_plan(batch) if batch.items.exists() else None,
        "latest_apply_log": batch.apply_logs.order_by("-applied_at", "-id").first(),
    }
    return render(request, "sources/imports/detail.html", context)


@staff_member_required
def import_batch_parse(request, pk):
    if request.method != "POST":
        raise Http404("Parse endpoint accepts POST only.")
    batch = get_object_or_404(ImportBatch, pk=pk)
    parse_import_batch(batch)
    messages.success(request, "Парсер создал записи, сущности, совпадения и группы.")
    return redirect("sources:import_batch_review", pk=batch.pk)


@staff_member_required
def import_batch_review(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk)
    reconcile_import_for_review(batch)
    decorate_import_batches([batch])
    groups = list(ImportGroup.objects.filter(import_batch=batch).select_related("root_entity").annotate(
        relation_count=Count("root_entity__child_relations", distinct=True)
    ))
    decorate_import_groups(groups)
    decorate_import_review_groups(groups)
    items = list(ImportItem.objects.filter(import_batch=batch).order_by("id"))
    decorate_import_items(items)
    decorate_import_item_review_summaries(items)
    item_groups = grouped_import_review_items(items)
    container_groups = [group for group in groups if group.group_type != ImportGroup.GroupType.STANDALONE_BOOKS]
    return render(
        request,
        "sources/imports/review.html",
        {
            "batch": batch,
            "groups": groups,
            "container_groups": container_groups,
            "item_groups": item_groups,
            "items": items,
            "stats": import_batch_stats(batch),
            "is_applied": batch.status == ImportBatch.Status.APPLIED,
            "latest_apply_log": batch.apply_logs.order_by("-applied_at", "-id").first(),
        },
    )


@staff_member_required
def import_author_review(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk)
    decorate_import_batches([batch])
    authors = list(
        ImportEntity.objects.filter(import_batch=batch, entity_type=ImportEntity.EntityType.AUTHOR)
        .prefetch_related("matches")
        .order_by("label", "id")
    )
    decorate_import_entities(authors)
    for author in authors:
        author.related_entities = author_related_entities(author)
        author.related_items = items_for_entities(batch, author.related_entities)
        decorate_import_items(author.related_items)
        author.related_item_count = author.related_items.count()
    return render(
        request,
        "sources/imports/authors.html",
        {
            "batch": batch,
            "authors": authors,
            "stats": import_batch_stats(batch),
            "plan": build_import_plan(batch),
            "is_applied": batch.status == ImportBatch.Status.APPLIED,
            "latest_apply_log": batch.apply_logs.order_by("-applied_at", "-id").first(),
        },
    )


@staff_member_required
def import_batch_groups(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk)
    reconcile_import_for_review(batch)
    groups = list(ImportGroup.objects.filter(import_batch=batch).select_related("root_entity"))
    decorate_import_groups(groups)
    return render(request, "sources/imports/groups.html", {"batch": batch, "groups": groups, "is_applied": batch.status == ImportBatch.Status.APPLIED})


@staff_member_required
def import_group_detail(request, pk, group_id):
    batch = get_object_or_404(ImportBatch, pk=pk)
    reconcile_import_for_review(batch)
    group = get_object_or_404(ImportGroup.objects.select_related("root_entity"), pk=group_id, import_batch=batch)
    decorate_import_groups([group])
    entities = group_entities(group)
    decorate_import_entities(entities)
    items = items_for_entities(batch, entities)
    decorate_import_items(items)
    article_entries = group_article_entries(group)
    for entry in article_entries:
        if entry.get("item"):
            decorate_import_items([entry["item"]])
        decorate_import_entities([entry["entity"]])
    compatible_groups = list(ImportGroup.objects.filter(
        import_batch=batch,
        group_type=group.group_type,
        root_entity__isnull=False,
    ).exclude(pk=group.pk).select_related("root_entity"))
    decorate_import_groups(compatible_groups)
    review_summary = import_group_review_summary(group, entities, article_entries)
    return render(
        request,
        "sources/imports/group_detail.html",
        {
            "batch": batch,
            "group": group,
            "entities": entities,
            "items": items,
            "article_entries": article_entries,
            "review_summary": review_summary,
            "compatible_groups": compatible_groups,
            "plan": build_import_plan(batch),
            "is_applied": batch.status == ImportBatch.Status.APPLIED,
            "latest_apply_log": batch.apply_logs.order_by("-applied_at", "-id").first(),
        },
    )


@staff_member_required
def import_item_detail(request, pk, item_id):
    batch = get_object_or_404(ImportBatch, pk=pk)
    reconcile_import_for_review(batch)
    item = get_object_or_404(ImportItem, pk=item_id, import_batch=batch)
    refresh_item_comparison_from_existing_work(item)
    decorate_import_items([item])
    decorate_import_item_decision(item)
    entities = item_entities(item)
    decorate_import_entities(entities)
    review_summary = import_item_review_summary(item, entities)
    return render(
        request,
        "sources/imports/item_detail.html",
        {
            "batch": batch,
            "item": item,
            "entities": entities,
            "review_summary": review_summary,
            "manual_parse_fields": manual_parse_field_rows(item),
            "parse_preview_fields": parse_preview_field_rows(item),
            "parse_preview_segments": parse_preview_segments(item),
            "unused_fragments": unused_parse_fragments(item),
            "is_applied": batch.status == ImportBatch.Status.APPLIED,
            "latest_apply_log": batch.apply_logs.order_by("-applied_at", "-id").first(),
        },
    )


@staff_member_required
def import_item_parse_edit(request, pk, item_id):
    if request.method != "POST":
        raise Http404("Manual parse edit endpoint accepts POST only.")
    batch = get_object_or_404(ImportBatch, pk=pk)
    item = get_object_or_404(ImportItem, pk=item_id, import_batch=batch)
    if import_batch_is_applied(request, batch):
        return redirect("sources:import_item_detail", pk=batch.pk, item_id=item.pk)
    data = manual_parse_data_from_post(item, request.POST)
    if not data.get("title"):
        messages.error(request, "Укажите заглавие записи.")
        return redirect("sources:import_item_detail", pk=batch.pk, item_id=item.pk)
    refresh_item_from_manual_parse(item, data, user=request.user if request.user.is_authenticated else None)
    messages.success(request, "Разбор сохранён, совпадения пересчитаны.")
    return redirect("sources:import_item_detail", pk=batch.pk, item_id=item.pk)


@staff_member_required
def import_item_decision(request, pk, item_id):
    if request.method != "POST":
        raise Http404("Item decision endpoint accepts POST only.")
    batch = get_object_or_404(ImportBatch, pk=pk)
    item = get_object_or_404(ImportItem, pk=item_id, import_batch=batch)
    if import_batch_is_applied(request, batch):
        return redirect(request.POST.get("next") or reverse("sources:import_item_detail", args=[batch.pk, item.pk]))
    decision_type = request.POST.get("decision_type", "")
    if decision_type == ImportDecision.DecisionType.UPDATE_EXISTING:
        refresh_item_comparison_from_existing_work(item)
    payload = {}
    if decision_type == ImportDecision.DecisionType.UPDATE_EXISTING:
        selected_fields = [value for value in request.POST.getlist("selected_fields") if value]
        replacement_fields = [value for value in request.POST.getlist("replacement_fields") if value]
        if not selected_fields and not replacement_fields:
            messages.error(request, "Выберите хотя бы одно поле для дополнения или замены.")
            return redirect(request.POST.get("next") or reverse("sources:import_item_detail", args=[batch.pk, item.pk]))
        payload["selected_fields"] = selected_fields
        if replacement_fields:
            payload["replacement_fields"] = replacement_fields
    try:
        apply_item_decision(item, decision_type, user=request.user if request.user.is_authenticated else None, payload=payload)
    except ValueError:
        messages.error(request, "Неизвестное решение по строке импорта.")
    else:
        messages.success(request, "Решение по строке импорта сохранено.")
    next_url = request.POST.get("next") or reverse("sources:import_item_detail", args=[batch.pk, item.pk])
    return redirect(next_url)


@staff_member_required
def import_entity_decision(request, pk, entity_id):
    if request.method != "POST":
        raise Http404("Decision endpoint accepts POST only.")
    batch = get_object_or_404(ImportBatch, pk=pk)
    entity = get_object_or_404(ImportEntity, pk=entity_id, import_batch=batch)
    if import_batch_is_applied(request, batch):
        return redirect(request.POST.get("next") or reverse("sources:import_batch_review", args=[batch.pk]))
    decision_type = request.POST.get("decision_type", "")
    target_type = request.POST.get("target_type", "")
    target_id = request.POST.get("target_id", "")
    if decision_type not in ImportDecision.DecisionType.values:
        messages.error(request, "Неизвестное решение.")
    else:
        apply_entity_decision(entity, decision_type, target_type=target_type, target_id=target_id, user=request.user if request.user.is_authenticated else None)
        messages.success(request, "Решение сохранено и зависимые статусы пересчитаны.")
    next_url = request.POST.get("next") or reverse("sources:import_batch_review", args=[batch.pk])
    return redirect(next_url)


@staff_member_required
def import_group_decision(request, pk, group_id):
    if request.method != "POST":
        raise Http404("Group decision endpoint accepts POST only.")
    batch = get_object_or_404(ImportBatch, pk=pk)
    group = get_object_or_404(ImportGroup.objects.select_related("root_entity"), pk=group_id, import_batch=batch)
    if import_batch_is_applied(request, batch):
        return redirect("sources:import_group_detail", pk=batch.pk, group_id=group.pk)
    if not group.root_entity:
        messages.error(request, "У группы нет корневой сущности.")
    else:
        apply_entity_decision(
            group.root_entity,
            request.POST.get("decision_type", ""),
            target_type=request.POST.get("target_type", ""),
            target_id=request.POST.get("target_id", ""),
            user=request.user if request.user.is_authenticated else None,
        )
        messages.success(request, "Решение по группе сохранено.")
    return redirect("sources:import_group_detail", pk=batch.pk, group_id=group.pk)


@staff_member_required
def import_group_article_action(request, pk, group_id, entity_id):
    if request.method != "POST":
        raise Http404("Group article action endpoint accepts POST only.")
    batch = get_object_or_404(ImportBatch, pk=pk)
    group = get_object_or_404(ImportGroup.objects.select_related("root_entity"), pk=group_id, import_batch=batch)
    if import_batch_is_applied(request, batch):
        return redirect("sources:import_group_detail", pk=batch.pk, group_id=group.pk)
    article = get_object_or_404(
        ImportEntity,
        pk=entity_id,
        import_batch=batch,
        entity_type=ImportEntity.EntityType.ARTICLE,
    )
    action = request.POST.get("action", "")
    redirect_group_id = group.pk
    try:
        if action == "split":
            new_group = split_article_to_new_group(group, article, user=request.user if request.user.is_authenticated else None)
            redirect_group_id = new_group.pk
            messages.success(request, "Статья вынесена в новую группу. Связи авторов сохранены.")
        elif action == "move":
            target_group = get_object_or_404(
                ImportGroup.objects.select_related("root_entity"),
                pk=request.POST.get("target_group_id"),
                import_batch=batch,
            )
            target_group = move_article_to_group(group, article, target_group, user=request.user if request.user.is_authenticated else None)
            redirect_group_id = target_group.pk
            messages.success(request, "Статья перенесена в выбранную группу. План импорта пересчитан.")
        else:
            messages.error(request, "Неизвестное действие со статьёй.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("sources:import_group_detail", pk=batch.pk, group_id=redirect_group_id)


@staff_member_required
def import_batch_plan(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk)
    decorate_import_batches([batch])
    return render(request, "sources/imports/plan.html", {"batch": batch, "plan": build_import_plan(batch), "is_applied": batch.status == ImportBatch.Status.APPLIED, "latest_apply_log": batch.apply_logs.order_by("-applied_at", "-id").first()})


@staff_member_required
def import_batch_apply(request, pk):
    if request.method != "POST":
        raise Http404("Apply endpoint accepts POST only.")
    batch = get_object_or_404(ImportBatch, pk=pk)
    result = apply_import_batch(batch, user=request.user if request.user.is_authenticated else None)
    if result.get("applied"):
        messages.success(request, "Импорт применён к редакторской базе.")
        return redirect("sources:import_batch_result", pk=batch.pk)
    messages.error(request, "Импорт нельзя применить: " + "; ".join(result.get("problems", [])))
    return redirect("sources:import_batch_plan", pk=batch.pk)


@staff_member_required
def import_batch_result(request, pk, log_id=None):
    batch = get_object_or_404(ImportBatch.objects.select_related("created_by"), pk=pk)
    decorate_import_batches([batch])
    log = None
    if log_id is not None:
        log = get_object_or_404(ImportApplyLog.objects.select_related("applied_by"), pk=log_id, import_batch=batch)
    else:
        log = batch.apply_logs.select_related("applied_by").order_by("-applied_at", "-id").first()
    context = {
        "batch": batch,
        "log": log,
        "result": build_apply_result_context(batch, log) if log else None,
        "is_applied": batch.status == ImportBatch.Status.APPLIED,
    }
    return render(request, "sources/imports/result.html", context)


def import_batch_is_applied(request, batch):
    if batch.status == ImportBatch.Status.APPLIED:
        messages.warning(request, "Импорт уже применён; решения нельзя менять.")
        return True
    return False


def refresh_item_comparison_from_existing_work(item):
    if item.matched_existing_type != "work" or not item.matched_existing_id:
        return
    if not item.parsed_data_json:
        return
    try:
        work = Work.objects.get(work_id=item.matched_existing_id)
    except Work.DoesNotExist:
        return
    comparison = compare_work_to_parsed_data(work, item.parsed_data_json)
    if comparison != item.comparison_json:
        item.comparison_json = comparison
        item.save(update_fields=["comparison_json", "updated_at"])


@staff_member_required
def import_batch_cancel(request, pk):
    if request.method != "POST":
        raise Http404("Cancel endpoint accepts POST only.")
    batch = get_object_or_404(ImportBatch, pk=pk)
    batch.status = ImportBatch.Status.CANCELLED
    batch.save(update_fields=["status", "updated_at"])
    messages.warning(request, "Импорт отменён. Основная база не изменялась.")
    return redirect("sources:import_batch_detail", pk=batch.pk)


def import_batch_stats(batch):
    unresolved_authors = [
        entity
        for entity in batch.entities.filter(entity_type=ImportEntity.EntityType.AUTHOR, status=ImportEntity.Status.UNRESOLVED)
        if not author_entity_only_used_by_auto_resolved_existing_articles(entity)
    ]
    return {
        "items": batch.items.count(),
        "entities": batch.entities.count(),
        "groups": batch.groups.count(),
        "needs_review": batch.entities.filter(status=ImportEntity.Status.UNRESOLVED).count(),
        "ready": batch.entities.filter(status__in=[ImportEntity.Status.WILL_CREATE, ImportEntity.Status.LINKED_EXISTING, ImportEntity.Status.WILL_UPDATE_EXISTING]).count(),
        "matches": batch.matches.count(),
        "authors_need_review": len(unresolved_authors),
        "new_records": batch.entities.filter(status=ImportEntity.Status.WILL_CREATE, entity_type__in=[ImportEntity.EntityType.BOOK, ImportEntity.EntityType.ARTICLE]).count(),
        "found_no_changes": batch.items.filter(status=ImportItem.Status.FOUND_EXISTING_NO_CHANGES).count(),
        "found_with_differences": batch.items.filter(status=ImportItem.Status.FOUND_EXISTING_WITH_DIFFERENCES).count(),
        "structural_conflicts": batch.items.filter(status=ImportItem.Status.STRUCTURAL_CONFLICT).count(),
        "errors": batch.items.filter(status=ImportItem.Status.ERROR).count(),
    }


def decorate_import_items(items):
    for item in items:
        item.matched_existing_label = describe_existing_entity(item.matched_existing_type, item.matched_existing_id) if item.matched_existing_id else ""
        item.detected_type_label = item_detected_type_label_ru(item.detected_type)
        item.status_label = item_status_label_ru(item.status)
        item.confidence_label = f"уверенность разбора {item.confidence:.2f}".replace(".", ",")
        item.has_selectable_update_fields = any(row.get("status") == "new_in_source" for row in item.comparison_json.get("fields", []))
        item.is_auto_resolved_existing_article = any(is_auto_resolved_existing_article(entity) for entity in item_entities(item))
        item.has_weak_entity_match = False
        item.best_entity_match_label = ""
        item.best_entity_match_score_percent = ""
        work_entities = [
            entity
            for entity in item_entities(item)
            if entity.entity_type in {ImportEntity.EntityType.BOOK, ImportEntity.EntityType.ARTICLE}
            and entity.data_json.get("item_id") == item.id
        ]
        item.has_new_record_plan = bool(
            item.status == ImportItem.Status.PARSED
            and work_entities
            and all(entity.status == ImportEntity.Status.WILL_CREATE and not entity.matches.exists() for entity in work_entities)
        )
        item.new_record_plan_label = "; ".join(entity.label for entity in work_entities if entity.status == ImportEntity.Status.WILL_CREATE)
        decorate_import_review_item_status(item)


def decorate_import_review_item_status(item):
    if item.is_auto_resolved_existing_article:
        label, css, order, note = "Найдена", "ok", 5, "Уже есть в базе: статья уже связана с этим журналом и выпуском."
    elif item.status == ImportItem.Status.FOUND_EXISTING_NO_CHANGES:
        label, css, order, note = "Найдена", "ok", 5, item.comparison_json.get("summary", "Изменений не требуется.")
    elif item.status == ImportItem.Status.FOUND_EXISTING_WITH_DIFFERENCES:
        label, css, order, note = ("Будет дополнена" if item.has_selectable_update_fields else "Отличается"), "warn", 4 if item.has_selectable_update_fields else 3, item.comparison_json.get("summary", "")
    elif item.status == ImportItem.Status.STRUCTURAL_CONFLICT:
        label, css, order, note = "Отличается", "warn", 3, "Источник описывает работу как часть родительского издания."
    elif item.status == ImportItem.Status.POSTPONED:
        label, css, order, note = "Отложена", "muted", 6, "Строка не будет применена сейчас."
    elif item.status == ImportItem.Status.REJECTED:
        label, css, order, note = "Отклонена", "muted", 7, "Строка не будет применяться."
    elif item.status == ImportItem.Status.PARSED and item.import_batch.status == ImportBatch.Status.APPLIED:
        label, css, order, note = "Обработано при применении", "ok", 5, "См. результат применения."
    elif item.status == ImportItem.Status.PARSED and item.has_weak_entity_match:
        label, css, order, note = "Требует решения", "danger", 1, "Похожа на запись в базе. Найдена похожая запись, подтвердите совпадение."
    elif item.status == ImportItem.Status.PARSED:
        label, css, order, note = "Новая", "new", 2, "Похожих записей в базе не найдено."
    elif item.status == ImportItem.Status.READY:
        label, css, order, note = "Решение принято", "ok", 5, ""
    elif item.status == ImportItem.Status.ERROR:
        label, css, order, note = "Ошибка", "danger", 1, ""
    elif item.status == ImportItem.Status.NEEDS_REVIEW:
        label, css, order, note = "Требует решения", "danger", 1, ""
    else:
        label, css, order, note = item.status_label, "muted", 8, ""
    item.review_status_label = label
    item.review_status_css = css
    item.review_status_order = order
    item.review_status_note = note


def grouped_import_review_items(items):
    groups = []
    labels = {
        1: "Требует решения",
        2: "Новые",
        3: "Отличаются",
        4: "Будут дополнены",
        5: "Найдены",
        6: "Отложены",
        7: "Отклонены",
        8: "Прочее",
    }
    buckets = defaultdict(list)
    for item in items:
        buckets[item.review_status_order].append(item)
    for order in sorted(buckets):
        groups.append({"label": labels.get(order, "Прочее"), "items": buckets[order]})
    return groups


MANUAL_PARSE_FIELDS = {
    ImportItem.DetectedType.BOOK: [
        ("authors", "Авторы", "textarea", "Через точку с запятой, если авторов несколько."),
        ("title", "Заглавие", "text", ""),
        ("title_remainder", "Уточнение названия", "text", ""),
        ("responsibility_statement", "Сведения об ответственности", "text", "Например: под ред. ..."),
        ("edition_statement", "Сведения об издании", "text", "Например: 2-е изд. 1867."),
        ("publication_place", "Место издания", "text", ""),
        ("publisher", "Издательство / типография", "text", ""),
        ("year", "Год", "text", ""),
        ("extent", "Объём", "text", ""),
        ("dimensions", "Размер", "text", "Используется только для разбора/сравнения."),
        ("notes", "Примечания", "textarea", ""),
    ],
    ImportItem.DetectedType.JOURNAL_ARTICLE: [
        ("authors", "Авторы", "textarea", "Через точку с запятой, если авторов несколько."),
        ("title", "Заглавие статьи", "text", ""),
        ("journal_title", "Журнал", "text", ""),
        ("year", "Год", "text", ""),
        ("issue_number", "Номер выпуска", "text", ""),
        ("pages", "Страницы статьи", "text", ""),
        ("raw_host", "Описание контейнера как в источнике", "textarea", "Используется для сравнения."),
        ("raw_parent_description", "Родительское описание", "textarea", "Используется для сравнения."),
    ],
    ImportItem.DetectedType.COLLECTION_ARTICLE: [
        ("authors", "Авторы", "textarea", "Через точку с запятой, если авторов несколько."),
        ("title", "Заглавие статьи", "text", ""),
        ("collection_title", "Сборник", "text", ""),
        ("year", "Год", "text", ""),
        ("pages", "Страницы статьи", "text", ""),
        ("raw_host", "Описание контейнера как в источнике", "textarea", "Используется для сравнения."),
        ("raw_parent_description", "Родительское описание", "textarea", "Используется для сравнения."),
    ],
}


def manual_parse_field_rows(item):
    field_defs = MANUAL_PARSE_FIELDS.get(item.detected_type, MANUAL_PARSE_FIELDS[ImportItem.DetectedType.BOOK])
    rows = []
    data = item.parsed_data_json or {}
    for key, label, input_type, help_text in field_defs:
        value = data.get(key, "")
        if key == "authors" and isinstance(value, list):
            value = "; ".join(value)
        rows.append({"key": key, "label": label, "input_type": input_type, "help_text": help_text, "value": value or ""})
    return rows


def manual_parse_data_from_post(item, post_data):
    data = dict(item.parsed_data_json or {})
    for row in manual_parse_field_rows(item):
        key = row["key"]
        if f"parsed_{key}" in post_data:
            data[key] = post_data.get(f"parsed_{key}", "")
        elif f"comparison_{key}" in post_data:
            data[key] = post_data.get(f"comparison_{key}", "")
    return data


def parse_preview_field_rows(item):
    hidden_preview_keys = {"raw_host", "raw_parent_description"}
    rows = []
    for field in manual_parse_field_rows(item):
        if field["key"] in hidden_preview_keys:
            continue
        value = normalize_preview_value(field["value"])
        if not value:
            continue
        rows.append({"label": field["label"], "value": value})
    return rows


def parse_preview_segments(item):
    raw = item.raw_text or ""
    if not raw:
        return []
    segments = []
    cursor = 0
    unmatched_badges = []
    for field in parse_preview_field_rows(item):
        values = parse_preview_values_for_field(field)
        matched_any = False
        for value in values:
            index = raw.find(value, cursor)
            if index < 0:
                index = raw.find(value)
            if index < 0:
                continue
            if index > cursor:
                segments.append({"kind": "text", "value": raw[cursor:index]})
            segments.append({"kind": "badge", "value": raw[index:index + len(value)], "label": field["label"]})
            cursor = index + len(value)
            matched_any = True
            break
        if not matched_any:
            unmatched_badges.append({"kind": "badge", "value": field["value"], "label": field["label"]})
    if cursor < len(raw):
        segments.append({"kind": "text", "value": raw[cursor:]})
    if unmatched_badges:
        if segments:
            segments.append({"kind": "separator", "value": " "})
        segments.extend(unmatched_badges)
    return segments


def parse_preview_values_for_field(field):
    value = str(field.get("value") or "").strip()
    if not value:
        return []
    values = [value]
    if field.get("label") == "Авторы":
        values.extend([part.strip() for part in value.split(";") if part.strip()])
    return sorted(set(values), key=len, reverse=True)


def normalize_preview_value(value):
    if isinstance(value, list):
        value = "; ".join(str(part) for part in value if part)
    return str(value or "").strip()


def unused_parse_fragments(item):
    fragments = []
    raw = item.raw_text or ""
    parsed_values = []
    for value in (item.parsed_data_json or {}).values():
        if isinstance(value, list):
            parsed_values.extend(value)
        else:
            parsed_values.append(value)
    edition_matches = re.findall(r"\b\d+\s*-\s*е\s+изд\.\s*(?:17|18|19|20)\d{2}", raw, flags=re.I)
    for fragment in edition_matches:
        if not any(fragment in str(value or "") for value in parsed_values):
            fragments.append({"fragment": fragment, "status": "Не использовано / требует проверки"})
    return fragments


def decorate_import_item_review_summaries(items):
    for item in items:
        entities = item_entities(item)
        decorate_import_entities(entities)
        summary = import_item_review_summary(item, entities)
        item.review_summary = summary
        main = summary.get("main_decision")
        item.has_weak_entity_match = bool(main)
        item.best_entity_match_label = main["best_match"]["existing_label"] if main else ""
        item.best_entity_match_score_percent = main["best_match"]["score_percent"] if main else ""
        decorate_import_review_item_status(item)


def decorate_import_item_decision(item):
    decision = item.decisions.order_by("-updated_at", "-id").first()
    item.latest_decision = decision
    item.latest_decision_label = decision_type_label_ru(decision.decision_type) if decision else ""
    selected = set()
    replacements = set()
    if decision and decision.decision_type == ImportDecision.DecisionType.UPDATE_EXISTING:
        selected = set(decision.payload_json.get("selected_fields") or [])
        replacements = set(decision.payload_json.get("replacement_fields") or [])
    item.selected_update_fields = sorted(selected)
    item.selected_replacement_fields = sorted(replacements)
    item.has_applicable_update_fields = False
    field_rows = manual_parse_field_rows(item)
    field_rows_by_key = {row["key"]: row for row in field_rows}
    field_rows_by_label = {row["label"]: row for row in field_rows}
    rows = []
    for row in item.comparison_json.get("fields", []):
        row = dict(row)
        if should_hide_comparison_row(item, row):
            continue
        row["selected_for_update"] = row.get("label") in selected
        row["selected_for_replacement"] = row.get("label") in replacements
        row["status_label"] = comparison_status_label_ru(row.get("status"))
        if decision and decision.decision_type == ImportDecision.DecisionType.UPDATE_EXISTING and row.get("status") == "new_in_source":
            row["status_label"] = "будет добавлено" if row["selected_for_update"] else "не выбрано"
        parsed_key = comparison_label_to_parsed_key(row.get("label"))
        if row.get("label") == "Родительское издание" and item.detected_type == ImportItem.DetectedType.COLLECTION_ARTICLE:
            parsed_key = "collection_title"
        elif row.get("label") == "Родительское издание" and item.detected_type == ImportItem.DetectedType.JOURNAL_ARTICLE:
            parsed_key = "journal_title"
        field_row = field_rows_by_label.get(row.get("label")) or field_rows_by_key.get(parsed_key)
        row["editable"] = bool(field_row)
        if field_row:
            row["parsed_key"] = field_row["key"]
            source_value = field_row["value"] if field_row["value"] not in (None, "") else row.get("source", "")
            row["input_type"] = "textarea" if field_row["input_type"] == "textarea" or len(str(source_value)) > 80 else "text"
            row["source_value"] = source_value
            row["help_text"] = field_row["help_text"]
        row["replacement_supported"] = False
        if row.get("status") == "new_in_source":
            item.has_applicable_update_fields = True
        rows.append(row)
    item.comparison_rows = rows


def should_hide_comparison_row(item, row):
    existing = str(row.get("existing") or "").strip()
    source = str(row.get("source") or "").strip()
    label = row.get("label")
    if not existing and not source:
        return True
    article_only_labels = {"Родительское издание", "Номер выпуска", "Страницы статьи"}
    if item.detected_type == ImportItem.DetectedType.BOOK and label in article_only_labels and not existing and not source:
        return True
    return False


def comparison_label_to_parsed_key(label):
    return {
        "Автор": "authors",
        "Название": "title",
        "Уточнение названия": "title_remainder",
        "Ответственность": "responsibility_statement",
        "Сведения об издании": "edition_statement",
        "Место издания": "publication_place",
        "Издательство / типография": "publisher",
        "Год": "year",
        "Страницы": "extent",
        "Размер": "dimensions",
        "Родительское издание": "parent_title",
        "Номер выпуска": "issue_number",
        "Страницы статьи": "pages",
        "Примечания": "notes",
    }.get(label, "")


def decorate_import_entities(entities):
    for entity in entities:
        entity.matched_existing_label = describe_existing_entity(entity.matched_existing_type, entity.matched_existing_id) if entity.matched_existing_id else ""
        entity.entity_type_label = entity_type_label_ru(entity.entity_type)
        entity.status_label = entity_status_label_ru(entity.status)
        for match in list(entity.matches.all()):
            decorate_import_match(match)


def decorate_import_match(match):
    match.existing_label = describe_existing_entity(match.existing_type, match.existing_id)
    match.score_percent = int(round((match.score or 0) * 100))
    match.work_count = (match.match_reason_json or {}).get("work_count", "")
    match.admin_url = admin_url_for_applied_entity(match.existing_type, match.existing_id)
    reason = dict(match.match_reason_json or {})
    if (
        match.existing_type == "work"
        and match.entity_id
        and match.entity.entity_type == ImportEntity.EntityType.ARTICLE
    ):
        try:
            work = Work.objects.get(work_id=match.existing_id)
        except Work.DoesNotExist:
            work = None
        if work:
            reason.update(article_issue_match_context(match.entity, work))
    match.import_issue_label = reason.get("import_issue_label", "")
    match.existing_issue_label = reason.get("existing_issue_label", "")
    match.same_issue = reason.get("same_issue")
    match.has_issue_context = bool(match.import_issue_label or match.existing_issue_label)
    return match


def best_import_match(entity, min_score=0.0):
    for match in entity.matches.all():
        if match.score >= min_score:
            return decorate_import_match(match)
    return None


def import_item_review_summary(item, entities):
    return {
        "main_decision": main_decision_for_item(item, entities),
        "authors": author_summaries_for_item(item, entities),
    }


def main_decision_for_item(item, entities):
    candidates = []
    for entity in entities:
        if entity.entity_type not in {ImportEntity.EntityType.BOOK, ImportEntity.EntityType.ARTICLE}:
            continue
        is_auto_resolved = is_auto_resolved_existing_article(entity)
        if entity.status != ImportEntity.Status.UNRESOLVED and not is_auto_resolved:
            continue
        if entity.data_json.get("item_id") != item.id:
            continue
        best = best_import_match(entity, min_score=0.7)
        if best:
            candidates.append((best.score, entity, best, is_auto_resolved))
    if not candidates:
        return None
    _, entity, best, is_auto_resolved = sorted(candidates, key=lambda row: row[0], reverse=True)[0]
    type_name = "книга" if entity.entity_type == ImportEntity.EntityType.BOOK else "статья"
    is_different_issue = entity.entity_type == ImportEntity.EntityType.ARTICLE and best.same_issue is False
    is_same_issue = entity.entity_type == ImportEntity.EntityType.ARTICLE and best.same_issue is True
    title = f"Осталось решить: {type_name} похожа на существующую запись"
    message = "Совпадение найдено, но оно недостаточно уверенное для автоматического решения. Проверьте найденную запись и выберите действие."
    create_label = "Нет, создать новую запись"
    link_label = "Да, связать с найденной записью"
    postpone_label = "Отложить решение"
    create_description = ""
    link_description = ""
    found_article_label = ""
    if is_different_issue:
        title = "Найдена похожая статья, но в другом выпуске"
        message = "Проверьте, это отдельная публикация в выпуске из текущей строки или та же запись, которая уже заведена в другом выпуске."
        import_issue = format_issue_for_editor(best.import_issue_label)
        existing_issue = format_issue_for_editor(best.existing_issue_label)
        create_description = f"Создать отдельную статью в выпуске {import_issue}."
        link_description = f"Найдена статья в журнале {existing_issue}."
        found_article_label = best.existing_label
        create_label = "Создать"
        link_label = "Связать"
        postpone_label = "Отложить решение"
    elif is_same_issue:
        title = "Статья уже есть в этом выпуске"
        message = "Название, автор, журнал и выпуск совпадают. Ручное подтверждение не требуется: при применении новая статья не будет создана."
        link_label = "Пропустить без изменений"
        create_label = "Создать новую запись"
        postpone_label = "Отложить решение"
    return {
        "entity": entity,
        "best_match": {
            "existing_type": best.existing_type,
            "existing_id": best.existing_id,
            "existing_label": best.existing_label,
            "score_percent": best.score_percent,
            "admin_url": best.admin_url,
            "import_issue_label": best.import_issue_label,
            "existing_issue_label": best.existing_issue_label,
            "import_issue_editor_label": format_issue_for_editor(best.import_issue_label),
            "existing_issue_editor_label": format_issue_for_editor(best.existing_issue_label),
            "same_issue": best.same_issue,
            "has_issue_context": best.has_issue_context,
            "is_different_issue": is_different_issue,
            "is_same_issue": is_same_issue,
            "is_auto_resolved": is_auto_resolved,
        },
        "type_name": type_name,
        "title": title,
        "message": message,
        "create_label": create_label,
        "link_label": link_label,
        "postpone_label": postpone_label,
        "create_description": create_description,
        "link_description": link_description,
        "found_article_label": found_article_label,
        "source_label": entity.label or item.raw_text,
        "is_auto_resolved": is_auto_resolved,
    }


def author_summaries_for_item(item, entities):
    item_auto_resolved = any(is_auto_resolved_existing_article(entity) for entity in entities if entity.data_json.get("item_id") == item.id)
    rows = []
    for entity in entities:
        if entity.entity_type != ImportEntity.EntityType.AUTHOR:
            continue
        matches = [decorate_import_match(match) for match in entity.matches.all()]
        rows.append(
            {
                "entity": entity,
                "matches": matches,
                "best_match": matches[0] if matches else None,
                "resolved_label": entity.matched_existing_label if entity.status == ImportEntity.Status.LINKED_EXISTING else "",
                "is_linked": entity.status == ImportEntity.Status.LINKED_EXISTING,
                "is_unresolved": entity.status == ImportEntity.Status.UNRESOLVED,
                "will_create": entity.status == ImportEntity.Status.WILL_CREATE,
                "is_nonblocking_for_auto_resolved_item": item_auto_resolved
                and entity.status == ImportEntity.Status.UNRESOLVED
                and author_entity_only_used_by_auto_resolved_existing_articles(entity),
            }
        )
    return rows


def import_group_review_summary(group, entities, article_entries):
    article_cards = group_article_decision_cards(article_entries)
    containers = group_container_rows(group, entities)
    unresolved_containers = [
        row for row in containers if row["entity"].status == ImportEntity.Status.UNRESOLVED
    ]
    if unresolved_containers:
        root_label = entity_type_label_ru(group.root_entity.entity_type).lower() if group.root_entity else "контейнер"
        title = f"Осталось решить: {root_label} требует проверки"
        message = "Сначала решите, создавать новый контейнер или связать его с существующей записью. После этого проверьте статьи внутри контейнера."
    elif article_cards:
        different_issue_cards = [card for card in article_cards if card["best_match"].get("is_different_issue")]
        same_issue_cards = [card for card in article_cards if card["best_match"].get("is_same_issue")]
        if len(article_cards) == 1 and different_issue_cards:
            title = "Найдена похожая статья, но в другом выпуске"
            message = "Для статьи найдено совпадение в базе, но оно связано с другим выпуском. Проверьте, нужно ли создать отдельную статью в выпуске из текущей строки."
        elif len(article_cards) == 1 and same_issue_cards:
            title = "Статья уже есть в этом выпуске"
            message = "Название, автор, журнал и выпуск совпадают. Подтвердите, что новую статью создавать не нужно."
        elif len(article_cards) == 1:
            title = "Осталось решить: статья похожа на существующую запись"
            message = "В этой группе 1 статья. Для неё найдено похожее совпадение в базе. Проверьте совпадение и выберите действие."
        else:
            title = f"Осталось решить: {len(article_cards)} статьи требуют проверки"
            message = "Для нескольких статей найдены похожие записи в базе. Проверьте совпадения и выберите действие для каждой статьи."
    else:
        title = "Группа готова к проверке"
        message = "Обязательные решения по статьям и контейнеру в этой группе приняты или не требуются."
    return {
        "title": title,
        "message": message,
        "article_cards": article_cards,
        "containers": containers,
    }


def group_article_decision_cards(article_entries):
    cards = []
    for entry in article_entries:
        article = entry["entity"]
        if article.status != ImportEntity.Status.UNRESOLVED:
            continue
        best = best_import_match(article, min_score=0.7)
        if not best:
            continue
        is_different_issue = best.same_issue is False
        is_same_issue = best.same_issue is True
        import_issue = format_issue_for_editor(best.import_issue_label)
        existing_issue = format_issue_for_editor(best.existing_issue_label)
        cards.append(
            {
                "entity": article,
                "item": entry.get("item"),
                "source_text": entry["item"].raw_text if entry.get("item") else article.data_json.get("raw_text") or article.label,
                "best_match": {
                    "existing_type": best.existing_type,
                    "existing_id": best.existing_id,
                    "existing_label": best.existing_label,
                    "score_percent": best.score_percent,
                    "admin_url": best.admin_url,
                    "import_issue_label": best.import_issue_label,
                    "existing_issue_label": best.existing_issue_label,
                    "import_issue_editor_label": import_issue,
                    "existing_issue_editor_label": existing_issue,
                    "same_issue": best.same_issue,
                    "has_issue_context": best.has_issue_context,
                    "is_different_issue": is_different_issue,
                    "is_same_issue": is_same_issue,
                },
                "create_label": "Создать" if is_different_issue else ("Создать новую статью" if is_same_issue else "Нет, создать новую статью"),
                "link_label": "Связать" if is_different_issue else ("Пропустить без изменений" if is_same_issue else "Да, связать с найденной статьёй"),
                "postpone_label": "Отложить решение",
                "create_description": f"Создать отдельную статью в выпуске {import_issue}." if is_different_issue else "",
                "link_description": f"Найдена статья в журнале {existing_issue}." if is_different_issue else "",
                "found_article_label": best.existing_label if is_different_issue else "",
            }
        )
    return cards


def group_container_rows(group, entities):
    if group.group_type == ImportGroup.GroupType.JOURNAL_ISSUE_GROUP:
        container_ids = set()
        if group.root_entity_id:
            container_ids.add(group.root_entity_id)
            container_ids.update(
                ImportEntityRelation.objects.filter(
                    import_batch=group.import_batch,
                    child_entity=group.root_entity,
                    relation_type="journal_has_issue",
                    parent_entity__entity_type=ImportEntity.EntityType.JOURNAL,
                ).values_list("parent_entity_id", flat=True)
            )
        filtered_entities = [entity for entity in entities if entity.id in container_ids]
    elif group.group_type == ImportGroup.GroupType.COLLECTION_VOLUME_GROUP:
        filtered_entities = [
            entity
            for entity in entities
            if entity.id == group.root_entity_id
            and entity.entity_type in {ImportEntity.EntityType.COLLECTION, ImportEntity.EntityType.COLLECTION_VOLUME}
        ]
    else:
        container_types = {
            ImportEntity.EntityType.JOURNAL,
            ImportEntity.EntityType.JOURNAL_ISSUE,
            ImportEntity.EntityType.COLLECTION,
            ImportEntity.EntityType.COLLECTION_VOLUME,
        }
        filtered_entities = [entity for entity in entities if entity.entity_type in container_types]
    rows = []
    for entity in filtered_entities:
        if entity.status == ImportEntity.Status.LINKED_EXISTING:
            summary = f"уже связано с существующей записью: {entity.matched_existing_label}"
        elif entity.status == ImportEntity.Status.WILL_CREATE:
            summary = f"будет создана новая запись: {entity.label}"
        elif entity.status == ImportEntity.Status.UNRESOLVED:
            summary = "требует решения"
        else:
            summary = entity.status_label.lower()
        matches = [decorate_import_match(match) for match in entity.matches.all()]
        best_match = matches[0] if matches else None
        match_rows = [{"match": match, "description": container_link_description(entity, match)} for match in matches]
        rows.append(
            {
                "entity": entity,
                "summary": summary,
                "matches": matches,
                "match_rows": match_rows,
                "best_match": best_match,
                "editor_label": container_entity_editor_label(entity),
                "create_description": container_create_description(entity),
            }
        )
    return rows


def decorate_import_batches(batches):
    for batch in batches:
        batch.status_label = batch_status_label_ru(batch.status)


def decorate_import_groups(groups):
    for group in groups:
        group.group_type_label = group_type_label_ru(group.group_type)
        group.status_label = group_status_label_ru(group.status)


def decorate_import_review_groups(groups):
    for group in groups:
        article_total, unresolved_articles = group_article_review_counts(group)
        group.article_count_label = article_count_label(article_total, unresolved_articles)
        group.article_state_label = article_state_label(article_total, unresolved_articles)
        group.article_state_css = "warn" if unresolved_articles else "ok"
        if group.group_type == ImportGroup.GroupType.JOURNAL_ISSUE_GROUP:
            group.review_check_label = "Журнал и выпуск"
            if journal_issue_group_has_unresolved_container(group):
                group.container_state_label = journal_issue_group_review_state(group)
                group.container_state_css = "warn"
                group.review_action_label = "Проверить журнал и выпуск"
            elif unresolved_articles:
                group.container_state_label = "Готов"
                group.container_state_css = "ok"
                group.review_action_label = "Проверить статьи"
            else:
                group.container_state_label = "Готов"
                group.container_state_css = "ok"
                group.review_action_label = "Готово"
        elif group.group_type == ImportGroup.GroupType.COLLECTION_VOLUME_GROUP:
            group.review_check_label = "Сборник"
            if group.root_entity and group.root_entity.status == ImportEntity.Status.UNRESOLVED:
                group.review_action_label = "Проверить сборник"
                group.container_state_label = "Требует решения"
                group.container_state_css = "warn"
            elif unresolved_articles:
                group.review_action_label = "Проверить статьи"
                group.container_state_label = "Готов"
                group.container_state_css = "ok"
            else:
                group.review_action_label = "Готово"
                group.container_state_label = "Готов"
                group.container_state_css = "ok"
        elif group.group_type == ImportGroup.GroupType.STANDALONE_BOOKS:
            group.review_action_label = "Проверить записи"
            group.review_check_label = "Отдельные записи"
            group.container_state_label = "Не требуется"
            group.container_state_css = "muted"
        else:
            group.review_action_label = "Проверить"
            group.review_check_label = group.group_type_label
            group.container_state_label = group.status_label
            group.container_state_css = "muted"


def group_has_unresolved_articles(group):
    relation_type = group_container_relation_type(group)
    if not relation_type or not group.root_entity_id:
        return False
    return ImportEntityRelation.objects.filter(
        import_batch=group.import_batch,
        parent_entity=group.root_entity,
        relation_type=relation_type,
        child_entity__entity_type=ImportEntity.EntityType.ARTICLE,
        child_entity__status=ImportEntity.Status.UNRESOLVED,
    ).exists()


def group_article_review_counts(group):
    relation_type = group_container_relation_type(group)
    if not relation_type or not group.root_entity_id:
        return 0, 0
    queryset = ImportEntityRelation.objects.filter(
        import_batch=group.import_batch,
        parent_entity=group.root_entity,
        relation_type=relation_type,
        child_entity__entity_type=ImportEntity.EntityType.ARTICLE,
    )
    total = queryset.count()
    unresolved = queryset.filter(child_entity__status=ImportEntity.Status.UNRESOLVED).count()
    return total, unresolved


def article_count_label(total, unresolved):
    if total == 0:
        return "Нет статей"
    if unresolved == 0:
        return f"{total} всего, все готовы"
    return f"{total} всего, {unresolved} требует проверки" if unresolved == 1 else f"{total} всего, {unresolved} требуют проверки"


def article_state_label(total, unresolved):
    if total == 0:
        return "Нет статей"
    if unresolved == 0:
        return "Все готовы"
    return "1 требует проверки" if unresolved == 1 else f"{unresolved} требуют проверки"


def journal_issue_group_has_unresolved_container(group):
    if not group.root_entity:
        return True
    if group.root_entity.status == ImportEntity.Status.UNRESOLVED:
        return True
    return ImportEntityRelation.objects.filter(
        import_batch=group.import_batch,
        child_entity=group.root_entity,
        relation_type="journal_has_issue",
        parent_entity__entity_type=ImportEntity.EntityType.JOURNAL,
        parent_entity__status=ImportEntity.Status.UNRESOLVED,
    ).exists()


def journal_issue_group_review_state(group):
    if not group.root_entity:
        return "Требует решения: контейнер не найден"
    if not journal_issue_group_has_unresolved_container(group):
        if group_has_unresolved_articles(group):
            return "Контейнер готов; проверьте статьи"
        return "Контейнер готов"
    bits = []
    issue_match = group.root_entity.matches.order_by("-score").first()
    if issue_match:
        bits.append(f"Похожий выпуск: {format_issue_for_editor(describe_existing_entity(issue_match.existing_type, issue_match.existing_id))}")
    journal_relation = ImportEntityRelation.objects.filter(
        import_batch=group.import_batch,
        child_entity=group.root_entity,
        relation_type="journal_has_issue",
        parent_entity__entity_type=ImportEntity.EntityType.JOURNAL,
    ).select_related("parent_entity").first()
    if journal_relation:
        journal = journal_relation.parent_entity
        journal_match = journal.matches.order_by("-score").first()
        if journal_match:
            bits.insert(0, f"Похожий журнал: {quote_title(clean_container_title(describe_existing_entity(journal_match.existing_type, journal_match.existing_id)))}")
    if bits:
        return "Требует решения: " + "; ".join(bits)
    return "Требует решения: проверьте журнал и выпуск"


def clean_container_title(value):
    value = normalize_editor_punctuation(value)
    value = re.sub(r"\s*\([^)]*\)\s*$", "", value).strip()
    return value.strip(" ,.;")


def normalize_editor_punctuation(value):
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    value = re.sub(r",\s*,+", ",", value)
    value = re.sub(r"\s+,", ",", value)
    return value.strip()


def quote_title(value):
    value = clean_container_title(value)
    return f"«{value}»" if value else "«без названия»"


def split_issue_label(value):
    text = normalize_editor_punctuation(value)
    text = text.replace(" — ", ", ")
    text = re.sub(r",\s*,+", ",", text)
    parts = [part.strip(" ,.;") for part in text.split(",") if part.strip(" ,.;")]
    title_parts = []
    year = ""
    number = ""
    for part in parts:
        if re.fullmatch(r"(17|18|19|20)\d{2}", part):
            year = part
        elif part.startswith("№"):
            number = part[1:].strip()
        else:
            title_parts.append(part)
    title = clean_container_title(", ".join(title_parts))
    return title, year, number


def format_issue_for_editor(value):
    title, year, number = split_issue_label(value)
    bits = [quote_title(title)]
    if number:
        bits.append(f"№ {number}")
    if year:
        bits.append(f"{year} год")
    return ", ".join(bits)


def container_entity_editor_label(entity):
    if entity.entity_type == ImportEntity.EntityType.JOURNAL:
        return quote_title(entity.data_json.get("title") or entity.label)
    if entity.entity_type == ImportEntity.EntityType.JOURNAL_ISSUE:
        data = entity.data_json or {}
        title = data.get("journal_title") or entity.label
        issue_number = f"№ {data.get('issue_number')}" if data.get("issue_number") else ""
        return format_issue_for_editor(", ".join(str(bit) for bit in [title, data.get("year"), issue_number] if bit))
    return quote_title(entity.label)


def container_create_description(entity):
    if entity.entity_type == ImportEntity.EntityType.JOURNAL:
        return f"Создать новый журнал {container_entity_editor_label(entity)}."
    if entity.entity_type == ImportEntity.EntityType.JOURNAL_ISSUE:
        return f"Создать новый выпуск {container_entity_editor_label(entity)}."
    if entity.entity_type == ImportEntity.EntityType.COLLECTION:
        return f"Создать новый сборник {container_entity_editor_label(entity)}."
    return f"Создать новую запись {container_entity_editor_label(entity)}."


def container_link_description(entity, match):
    if entity.entity_type == ImportEntity.EntityType.JOURNAL:
        return f"Связать журнал с {quote_title(clean_container_title(match.existing_label))}."
    if entity.entity_type == ImportEntity.EntityType.JOURNAL_ISSUE:
        return f"Связать выпуск с {format_issue_for_editor(match.existing_label)}."
    if entity.entity_type == ImportEntity.EntityType.COLLECTION:
        return f"Связать сборник с {quote_title(clean_container_title(match.existing_label))}."
    return f"Связать с существующей записью {quote_title(clean_container_title(match.existing_label))}."


def build_apply_result_context(batch, log):
    created_rows = [decorate_apply_created_row(row) for row in log.created_entities_json]
    updated_rows = [decorate_apply_updated_row(row) for row in log.updated_entities_json]
    relation_rows = [decorate_apply_relation_row(row) for row in log.created_relations_json]
    decision_rows = [decorate_apply_decision_row(batch, row) for row in log.decisions_json]
    no_op_rows = [row for row in updated_rows if row["status"] == "no_op"]
    changed_rows = [row for row in updated_rows if row["status"] != "no_op"]
    summary = dict(log.summary_json or {})
    summary.setdefault("created", len(created_rows))
    summary.setdefault("updated", len(changed_rows))
    summary.setdefault("update_noop", len(no_op_rows))
    summary.setdefault("relations", len(relation_rows))
    return {
        "summary": summary,
        "backup_path": summary.get("backup_path", ""),
        "created_rows": created_rows,
        "updated_rows": changed_rows,
        "no_op_rows": no_op_rows,
        "relation_rows": relation_rows,
        "decision_rows": decision_rows,
    }


APPLY_TYPE_LABELS = {
    "author": "Автор",
    "book": "Книга",
    "article": "Статья",
    "journal": "Журнал",
    "journal_issue": "Выпуск журнала",
    "collection": "Сборник",
    "work": "Библиографическая запись",
}


def decorate_apply_created_row(row):
    entity_type = row.get("type", "")
    entity_id = row.get("id", "")
    return {
        "type": entity_type,
        "type_label": APPLY_TYPE_LABELS.get(entity_type, entity_type),
        "id": entity_id,
        "label": row.get("label", "") or describe_existing_entity(entity_type, entity_id),
        "url": admin_url_for_applied_entity(entity_type, entity_id),
    }


def decorate_apply_updated_row(row):
    entity_type = row.get("type", "")
    entity_id = row.get("id", "")
    return {
        "type": entity_type,
        "type_label": APPLY_TYPE_LABELS.get(entity_type, entity_type),
        "id": entity_id,
        "label": row.get("label", "") or describe_existing_entity(entity_type, entity_id),
        "url": admin_url_for_applied_entity(entity_type, entity_id),
        "status": row.get("status", ""),
        "reason": row.get("reason", ""),
        "updated_fields": row.get("updated_fields", []),
        "skipped_fields": row.get("skipped_fields", []),
    }


def decorate_apply_relation_row(row):
    relation_type = row.get("type", "")
    if relation_type == "author_of":
        work_id = row.get("work_id", "")
        author_id = row.get("author_id", "")
        return {
            "type_label": "Автор записи",
            "label": f"{describe_existing_entity('author', author_id)} → {describe_existing_entity('work', work_id)}",
            "work_url": admin_url_for_applied_entity("work", work_id),
            "author_url": admin_url_for_applied_entity("author", author_id),
        }
    return {"type_label": relation_type, "label": str(row), "work_url": "", "author_url": ""}


def decorate_apply_decision_row(batch, row):
    decision_type = row.get("decision_type", "")
    item_id = row.get("item_id")
    entity_id = row.get("entity_id")
    group_id = row.get("group_id")
    label = ""
    target_label = ""
    url = ""
    if item_id:
        item = ImportItem.objects.filter(import_batch=batch, pk=item_id).first()
        if item:
            label = item.raw_text
            url = reverse("sources:import_item_detail", args=[batch.pk, item.pk])
    elif entity_id:
        entity = ImportEntity.objects.filter(import_batch=batch, pk=entity_id).first()
        if entity:
            label = entity.label
            url = entity_result_url(batch, entity)
    elif group_id:
        group = ImportGroup.objects.filter(import_batch=batch, pk=group_id).first()
        if group:
            label = group.label
            url = reverse("sources:import_group_detail", args=[batch.pk, group.pk])
    if row.get("target_id"):
        target_label = describe_existing_entity(row.get("target_type", ""), row.get("target_id", ""))
    return {
        "decision_label": decision_type_label_ru(decision_type),
        "label": label or "-",
        "target_label": target_label,
        "url": url,
    }


def entity_result_url(batch, entity):
    if entity.entity_type == ImportEntity.EntityType.AUTHOR:
        return reverse("sources:import_author_review", args=[batch.pk])
    group = ImportGroup.objects.filter(import_batch=batch, root_entity=entity).first()
    if group:
        return reverse("sources:import_group_detail", args=[batch.pk, group.pk])
    item_id = entity.data_json.get("item_id")
    if item_id:
        return reverse("sources:import_item_detail", args=[batch.pk, item_id])
    return reverse("sources:import_batch_review", args=[batch.pk])


def admin_url_for_applied_entity(entity_type, entity_id):
    if not entity_id:
        return ""
    url_name_by_type = {
        "author": "admin:sources_author_change",
        "journal": "admin:sources_journal_change",
        "journal_issue": "admin:sources_journalissue_change",
        "collection": "admin:sources_collection_change",
        "work": "admin:sources_work_change",
        "book": "admin:sources_work_change",
        "article": "admin:sources_work_change",
    }
    url_name = url_name_by_type.get(entity_type)
    if not url_name:
        return ""
    try:
        return reverse(url_name, args=[entity_id])
    except Exception:
        return ""


def group_entities(group):
    if not group.root_entity:
        return []
    ids = {group.root_entity_id}
    ids.update(ImportEntityRelation.objects.filter(import_batch=group.import_batch, parent_entity=group.root_entity).values_list("child_entity_id", flat=True))
    ids.update(ImportEntityRelation.objects.filter(import_batch=group.import_batch, child_entity=group.root_entity).values_list("parent_entity_id", flat=True))
    child_ids = list(ImportEntityRelation.objects.filter(import_batch=group.import_batch, parent_entity_id__in=ids).values_list("child_entity_id", flat=True))
    ids.update(child_ids)
    return list(ImportEntity.objects.filter(import_batch=group.import_batch, id__in=ids).prefetch_related("matches"))


def author_related_entities(author):
    ids = set(
        ImportEntityRelation.objects.filter(
            import_batch=author.import_batch,
            parent_entity=author,
            relation_type="author_of",
        ).values_list("child_entity_id", flat=True)
    )
    return list(ImportEntity.objects.filter(import_batch=author.import_batch, id__in=ids).prefetch_related("matches"))


def item_entities(item):
    item_id = item.id
    entities = list(ImportEntity.objects.filter(import_batch=item.import_batch, data_json__item_id=item_id).prefetch_related("matches"))
    linked_ids = set(e.id for e in entities)
    for entity in list(entities):
        linked_ids.update(ImportEntityRelation.objects.filter(import_batch=item.import_batch, child_entity=entity).values_list("parent_entity_id", flat=True))
        linked_ids.update(ImportEntityRelation.objects.filter(import_batch=item.import_batch, parent_entity=entity).values_list("child_entity_id", flat=True))
    return list(ImportEntity.objects.filter(import_batch=item.import_batch, id__in=linked_ids).prefetch_related("matches"))


def items_for_entities(batch, entities):
    item_ids = [entity.data_json.get("item_id") for entity in entities if entity.data_json.get("item_id")]
    return ImportItem.objects.filter(import_batch=batch, id__in=item_ids)


def group_article_entries(group):
    relation_type = group_container_relation_type(group)
    if not relation_type or not group.root_entity_id:
        return []
    relations = (
        ImportEntityRelation.objects.filter(
            import_batch=group.import_batch,
            parent_entity=group.root_entity,
            relation_type=relation_type,
            child_entity__entity_type=ImportEntity.EntityType.ARTICLE,
        )
        .select_related("child_entity")
        .order_by("id")
    )
    item_ids = [relation.child_entity.data_json.get("item_id") for relation in relations if relation.child_entity.data_json.get("item_id")]
    items = {item.id: item for item in ImportItem.objects.filter(import_batch=group.import_batch, id__in=item_ids)}
    entries = []
    for relation in relations:
        entity = relation.child_entity
        entries.append({"entity": entity, "item": items.get(entity.data_json.get("item_id"))})
    return entries


@staff_member_required
def parser_batches(request):
    if request.method == "POST":
        action = request.POST.get("action", "parse")
        if action == "clear_parser_staging":
            stats = clear_parser_staging_data()
            messages.warning(
                request,
                "Отладочные данные парсинга очищены: "
                f"parser runs {stats['parser_runs']}, incoming batches {stats['incoming_batches']}, "
                f"normalized files {stats['normalized_files']}. Редакторская база не изменялась.",
            )
            return redirect(f"{reverse('sources:parser_batches')}?clear_parser_storage=1")
        text = request.POST.get("bibliography_text", "")
        uploaded = request.FILES.get("bibliography_file")
        batch_id = request.POST.get("batch_id", "").strip()
        use_ai_markup = request.POST.get("use_ai_markup") == "1"
        clear_work_file = request.POST.get("clear_work_file") == "1"
        try:
            batch_id, run_id = create_parser_batch_from_request(
                text,
                uploaded,
                batch_id=batch_id or None,
                use_ai_markup=use_ai_markup,
                clear_work_file=clear_work_file,
            )
        except Exception as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Пачка {batch_id} создана/обновлена. Parser run: {run_id}.")
            return redirect("sources:parser_run_page", run_id=run_id, page_name=initial_parser_review_page(run_id))
        return redirect("sources:parser_batches")

    return render(
        request,
        "sources/parser_batches.html",
        {
            "batches": list_parser_batches(),
            "runs": list_parser_runs(),
        },
    )


@staff_member_required
@ensure_csrf_cookie
def parser_run_page(request, run_id, page_name):
    if page_name not in {
        "review_containers.html",
        "review_authors.html",
        "review_report.html",
        "review_stage2.html",
        "review_stage3.html",
    }:
        raise Http404("Unknown parser page.")
    path = (PARSER_RUNS_ROOT / run_id / page_name).resolve()
    if not path.exists() or PARSER_RUNS_ROOT.resolve() not in path.parents:
        raise Http404("Parser page not found.")
    content = path.read_text(encoding="utf-8")
    if page_name == "review_stage3.html":
        content = inject_parser_apply_status(content, PARSER_RUNS_ROOT / run_id)
        content = inject_stage2_edits_status(content, PARSER_RUNS_ROOT / run_id)
    return HttpResponse(content, content_type="text/html; charset=utf-8")


@csrf_exempt
@staff_member_required
@ensure_csrf_cookie
def parser_review_state(request, run_id):
    run_dir = parser_run_dir_or_404(run_id)
    state_path = run_dir / "review_state.json"
    state = read_json_file(state_path, default=default_review_state(run_id))
    if request.method == "GET":
        return JsonResponse(state, json_dumps_params={"ensure_ascii": False})
    if request.method != "POST":
        raise Http404("Parser review state endpoint accepts GET and POST only.")

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)

    try:
        state = update_review_state(state, payload)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    state["updated_at"] = timezone.now().isoformat()
    write_json_file(state_path, state)
    return JsonResponse({"ok": True, "state": state}, json_dumps_params={"ensure_ascii": False})


@staff_member_required
def parser_apply_request(request, run_id):
    if request.method != "POST":
        raise Http404("Parser apply endpoint accepts POST only.")
    mode = request.POST.get("mode", "")
    if mode not in {"changed_only", "changed_and_new"}:
        messages.error(request, "Неизвестный режим внесения.")
        return redirect("sources:parser_run_page", run_id=run_id, page_name="review_stage3.html")
    run_dir = (PARSER_RUNS_ROOT / run_id).resolve()
    if not run_dir.exists() or PARSER_RUNS_ROOT.resolve() not in run_dir.parents:
        raise Http404("Parser run not found.")

    apply_result = apply_parser_run_changes(run_dir, mode)
    backup_path = None
    if apply_result["applied"]:
        backup_path = backup_sqlite_database(f"before-parser-apply-{mode}")
        apply_result = collect_parser_run_changes(run_dir, mode, write=True)
    request_path = run_dir / "apply_request.json"
    request_data = read_json_file(request_path, default={})
    history = request_data.get("history", [])
    event = {
        "created_at": timezone.now().isoformat(),
        "mode": mode,
        "backup_path": str(backup_path.relative_to(PROJECT_ROOT)) if backup_path else "",
        "status": parser_apply_status(apply_result),
        "note": apply_result["note"],
        "result": apply_result,
    }
    history.append(event)
    write_json_file(
        request_path,
        {
            "run_id": run_id,
            "latest": event,
            "history": history,
        },
    )
    refresh_parser_run_compare(run_dir)
    mode_label = "только изменённые записи" if mode == "changed_only" else "изменённые и новые записи"
    backup_message = f" Backup создан: {backup_path}." if backup_path else " Backup не создавался, потому что новых изменений не было."
    messages.warning(
        request,
        f"Внесение выполнено ({mode_label}); применено дополнений: {apply_result['applied']}, "
        f"уже было внесено: {apply_result['already_applied']}, "
        f"пропущено: {apply_result['skipped']}.{backup_message}",
    )
    return redirect("sources:parser_run_page", run_id=run_id, page_name="review_stage3.html")


def apply_parser_run_changes(run_dir, mode):
    return collect_parser_run_changes(run_dir, mode, write=False)


def collect_parser_run_changes(run_dir, mode, write=False):
    proposed_path = run_dir / "proposed_changes.tsv"
    if not proposed_path.exists():
        return {
            "applied": 0,
            "already_applied": 0,
            "skipped": 0,
            "unsupported": 0,
            "applied_candidate_ids": [],
            "already_applied_candidate_ids": [],
            "note": "Файл proposed_changes.tsv не найден.",
        }

    applied = 0
    already_applied = 0
    skipped = 0
    unsupported = 0
    applied_candidate_ids = []
    already_applied_candidate_ids = []
    with proposed_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))

    with transaction.atomic():
        for row in rows:
            field_name = row.get("field") or ""
            decision = row.get("decision") or ""
            source_id = row.get("editor_source_id") or ""
            candidate_value = row.get("candidate_value") or ""
            if field_name not in {"raw_publication_details", "public_review"} or decision != "safe_fill_empty":
                unsupported += 1
                continue
            if not source_id or not candidate_value.strip():
                skipped += 1
                continue
            try:
                source = Source.objects.select_for_update().get(source_id=source_id)
            except Source.DoesNotExist:
                skipped += 1
                continue
            current_value = getattr(source, field_name) or ""
            if current_value == candidate_value:
                already_applied += 1
                already_applied_candidate_ids.append(row.get("candidate_id") or "")
                continue
            if current_value.strip():
                skipped += 1
                continue
            if write:
                setattr(source, field_name, candidate_value)
                update_fields = [field_name]
                if hasattr(source, "updated_at"):
                    source.updated_at = timezone.now()
                    update_fields.append("updated_at")
                if field_name == "raw_publication_details" and (not source.data_source or source.data_source == "editor"):
                    source.data_source = f"parser run {run_dir.name}"
                    update_fields.append("data_source")
                source.save(update_fields=update_fields)
                sync_legacy_work_from_source(source, field_name, candidate_value)
            applied += 1
            applied_candidate_ids.append(row.get("candidate_id") or "")

    new_records_note = ""
    if mode == "changed_and_new":
        new_records_path = run_dir / "new_records.tsv"
        new_count = count_tsv_data_rows(new_records_path)
        if new_count:
            new_records_note = f" Новые записи пока не создавались автоматически: {new_count}."

    note = (
        f"Заполнены пустые raw-записи источника: {applied}. "
        f"Уже были внесены ранее: {already_applied}. "
        f"Пропущено без изменений: {skipped}. "
        f"Неподдержанных полей: {unsupported}."
        f"{new_records_note}"
    )
    if not applied and already_applied and not skipped and not unsupported:
        note = f"Изменений нет: эти дополнения уже внесены ранее ({already_applied}).{new_records_note}"

    return {
        "applied": applied,
        "already_applied": already_applied,
        "skipped": skipped,
        "unsupported": unsupported,
        "applied_candidate_ids": [value for value in applied_candidate_ids if value],
        "already_applied_candidate_ids": [value for value in already_applied_candidate_ids if value],
        "note": note,
    }


def sync_legacy_work_from_source(source, field_name, candidate_value):
    if field_name not in {"raw_publication_details", "public_review"}:
        return
    work = getattr(source, "legacy_work", None)
    if not work:
        return
    if field_name == "raw_publication_details":
        if not work.publication_details:
            work.publication_details = candidate_value
            work.save(update_fields=["publication_details"])
    elif field_name == "public_review":
        if not work.public_review:
            work.public_review = candidate_value
            work.save(update_fields=["public_review"])


def parser_apply_status(result):
    if result.get("applied"):
        return "applied"
    if result.get("already_applied") and not result.get("skipped") and not result.get("unsupported"):
        return "already_applied"
    return "no_changes_applied"


def refresh_parser_run_compare(run_dir):
    run_checked_subprocess(
        [
            sys.executable,
            str(PARSER_SCRIPT),
            "compare",
            "--run-dir",
            str(run_dir),
            "--editor-db",
            str(EDITOR_DB),
        ]
    )


def count_tsv_data_rows(path):
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def inject_parser_apply_status(content, run_dir):
    request_path = run_dir / "apply_request.json"
    request_data = read_json_file(request_path, default={})
    latest = request_data.get("latest") or {}
    if not latest:
        return content
    result = latest.get("result") or {}
    backup_path = latest.get("backup_path") or ""
    status_html = f"""
    <section class="review-section parser-apply-status">
      <div class="results-header">
        <h2>Статус внесения</h2>
      </div>
      <div class="empty-state">
        <strong>{html_escape(latest.get("status") or "")}</strong><br>
        {html_escape(latest.get("note") or "")}<br>
        Применено: {html_escape(str(result.get("applied", 0)))};
        уже было внесено: {html_escape(str(result.get("already_applied", 0)))};
        пропущено: {html_escape(str(result.get("skipped", 0)))};
        неподдержано: {html_escape(str(result.get("unsupported", 0)))}.<br>
        Backup: <code>{html_escape(backup_path or "не создавался")}</code>
      </div>
    </section>
"""
    return content.replace("    <section class=\"review-section\">\n      <div class=\"results-header\">\n        <h2>Внесение в базу</h2>", status_html + "\n    <section class=\"review-section\">\n      <div class=\"results-header\">\n        <h2>Внесение в базу</h2>", 1)


def inject_stage2_edits_status(content, run_dir):
    state = read_json_file(run_dir / "review_state.json", default={})
    edits = (state.get("stage2") or {}) if isinstance(state, dict) else {}
    if not edits:
        return content
    items = []
    labels = {
        "sourceType": "тип",
        "authors": "авторы",
        "title": "заглавие",
        "subtitle": "подзаголовок",
        "responsibility": "ответственность",
        "place": "место",
        "publisher": "издательство",
        "date": "дата",
        "extent": "объём",
        "notes": "примечание",
        "articlePages": "страницы статьи",
        "isbn": "ISBN",
        "issn": "ISSN",
        "doi": "DOI",
        "url": "URL",
    }
    for item_id, edit in sorted(edits.items()):
        values = edit.get("values") or {}
        shown = []
        for key, label in labels.items():
            value = values.get(key)
            if value:
                shown.append(f"{label}: {value}")
        selected = edit.get("selected") or ""
        editor_source_id = edit.get("editor_source_id") or ""
        candidate_id = edit.get("candidate_id") or ""
        items.append(
            "<li class=\"bibliography-item\">"
            f"<div class=\"citation-row\"><span class=\"record-prefix\">Правка:</span> {html_escape(values.get('title') or item_id)}</div>"
            f"<div class=\"field-note\">Выбрана строка: {html_escape(selected or 'не указано')}; "
            f"candidate: {html_escape(candidate_id or 'не указан')}; "
            f"запись базы: {html_escape(editor_source_id or 'не указана')}</div>"
            f"<div class=\"field-note\">{html_escape('; '.join(shown) or 'Поля не заполнены')}</div>"
            "</li>"
        )
    status_html = f"""
    <section class="review-section parser-stage2-edits">
      <div class="results-header">
        <h2>Правки второго этапа</h2>
        <div>Сохранённые редакторские правки: <strong>{len(items)}</strong>. Они зафиксированы в состоянии run и будут использоваться следующим reviewed apply-шагом.</div>
      </div>
      <ul class="bibliography-list">
        {''.join(items)}
      </ul>
    </section>
"""
    return content.replace("    <section class=\"review-section\">\n      <div class=\"results-header\">\n        <h2>Внесение в базу</h2>", status_html + "\n    <section class=\"review-section\">\n      <div class=\"results-header\">\n        <h2>Внесение в базу</h2>", 1)


def html_escape(value):
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def parser_run_dir_or_404(run_id):
    if not re.match(r"^[0-9A-Za-z._-]+$", run_id or ""):
        raise Http404("Parser run not found.")
    run_dir = (PARSER_RUNS_ROOT / run_id).resolve()
    if not run_dir.exists() or PARSER_RUNS_ROOT.resolve() not in run_dir.parents:
        raise Http404("Parser run not found.")
    return run_dir


def default_review_state(run_id):
    now = timezone.now().isoformat()
    return {
        "run_id": run_id,
        "created_at": now,
        "updated_at": now,
        "stage1": {},
        "stage0": {},
        "stage_authors": {},
        "stage2": {},
        "stage3": {},
        "events": [],
    }


def update_review_state(state, payload):
    stage = payload.get("stage")
    item_id = str(payload.get("item_id") or "").strip()
    if stage not in {"stage0", "stage_authors", "stage1", "stage2", "stage3"}:
        raise ValueError("Unknown review stage.")
    if stage in {"stage0", "stage_authors", "stage1", "stage2"} and not item_id:
        raise ValueError("item_id is required.")

    now = timezone.now().isoformat()
    if stage == "stage0":
        decision = payload.get("decision")
        if decision == "clear":
            state.setdefault("stage0", {}).pop(item_id, None)
        elif decision in {
            "use_existing_issue",
            "use_existing_collection",
            "create_issue",
            "create_periodical",
            "create_collection",
            "choose_periodical",
            "choose_issue",
            "choose_collection",
            "unresolved",
        }:
            state.setdefault("stage0", {})[item_id] = {
                "updated_at": now,
                "decision": decision,
                "candidate_id": payload.get("candidate_id") or "",
                "container_kind": payload.get("container_kind") or "",
                "container_action": payload.get("container_action") or "",
                "periodical_id": payload.get("periodical_id") or "",
                "issue_id": payload.get("issue_id") or "",
                "collection_id": payload.get("collection_id") or "",
            }
        else:
            raise ValueError("Unknown stage0 decision.")
    elif stage == "stage_authors":
        decision = payload.get("decision")
        if decision == "clear":
            state.setdefault("stage_authors", {}).pop(item_id, None)
        elif decision in {"use_existing_author", "create_author", "unresolved"}:
            state.setdefault("stage_authors", {})[item_id] = {
                "updated_at": now,
                "decision": decision,
                "candidate_author": payload.get("candidate_author") or "",
                "author_id": payload.get("author_id") or "",
            }
        else:
            raise ValueError("Unknown stage_authors decision.")
    elif stage == "stage1":
        decision = payload.get("decision")
        if decision == "clear":
            state.setdefault("stage1", {}).pop(item_id, None)
        elif decision in {"new", "keep_new", "keep_old"}:
            state.setdefault("stage1", {})[item_id] = {
                "updated_at": now,
                "decision": decision,
                "selected": payload.get("selected") or "",
                "candidate_id": payload.get("candidate_id") or "",
                "editor_source_id": payload.get("editor_source_id") or "",
                "match_score": payload.get("match_score"),
            }
        else:
            raise ValueError("Unknown stage1 decision.")
    elif stage == "stage2":
        action = payload.get("action")
        if action == "clear":
            state.setdefault("stage2", {}).pop(item_id, None)
        elif action == "save":
            values = payload.get("values")
            if not isinstance(values, dict):
                raise ValueError("stage2 values must be an object.")
            state.setdefault("stage2", {})[item_id] = {
                "updated_at": now,
                "action": "save",
                "selected": payload.get("selected") or "",
                "candidate_id": payload.get("candidate_id") or "",
                "editor_source_id": payload.get("editor_source_id") or "",
                "values": values,
            }
        else:
            raise ValueError("Unknown stage2 action.")
    else:
        state.setdefault("stage3", {})["latest"] = {
            "updated_at": now,
            "action": payload.get("action") or "",
            "mode": payload.get("mode") or "",
        }

    events = state.setdefault("events", [])
    event = {
        "created_at": now,
        "stage": stage,
        "item_id": item_id,
        "action": payload.get("action") or payload.get("decision") or "",
    }
    events.append(event)
    state["events"] = events[-200:]
    return state


def create_parser_batch_from_request(text, uploaded, batch_id=None, use_ai_markup=False, clear_work_file=False):
    text = text or ""
    uploaded_name = ""
    uploaded_text = ""
    if uploaded:
        uploaded_name = sanitize_filename(uploaded.name)
        uploaded_text = uploaded.read().decode("utf-8-sig")
    if not text.strip() and not uploaded_text.strip():
        raise ValueError("Введите текст или загрузите .txt файл.")

    batch_id = batch_id or next_parser_batch_id()
    batch_dir = INCOMING_ROOT / batch_id
    original_dir = batch_dir / "original"
    work_dir = batch_dir / "work"
    history_dir = work_dir / "history"
    original_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)

    timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    submitted_parts = []
    original_files = []
    if uploaded_text.strip():
        original_path = original_dir / f"{timestamp}-{uploaded_name or 'upload.txt'}"
        original_path.write_text(uploaded_text, encoding="utf-8")
        submitted_parts.append(uploaded_text)
        original_files.append(str(original_path.relative_to(PROJECT_ROOT)))
    if text.strip():
        original_path = original_dir / f"{timestamp}-textarea.txt"
        original_path.write_text(text, encoding="utf-8")
        submitted_parts.append(text)
        original_files.append(str(original_path.relative_to(PROJECT_ROOT)))

    current_path = work_dir / "current.txt"
    if current_path.exists():
        history_name = "current.before-clear" if clear_work_file else "current.before-append"
        copy2(current_path, history_dir / f"{history_name}.{timestamp}.txt")
        existing = "" if clear_work_file else current_path.read_text(encoding="utf-8")
    else:
        existing = ""
    normalized_append = normalize_submitted_bibliography_text("\n\n".join(submitted_parts))
    combined = "\n\n".join(part for part in [existing.strip(), normalized_append.strip()] if part)
    current_path.write_text(combined + "\n", encoding="utf-8")

    manifest_path = batch_dir / "batch_manifest.json"
    manifest = read_json_file(manifest_path, default={})
    if not manifest:
        manifest = {
            "batch_id": batch_id,
            "created_at": timezone.now().isoformat(),
            "original_files": [],
            "work_file": str(current_path.relative_to(PROJECT_ROOT)),
            "runs": [],
            "status": "in_progress",
        }
    manifest["updated_at"] = timezone.now().isoformat()
    manifest["original_files"].extend(original_files)

    run_id = run_parser_for_batch(batch_id, current_path, use_ai_markup=use_ai_markup)
    manifest["runs"].append(run_id)
    write_json_file(manifest_path, manifest)
    append_batch_event(
        batch_dir,
        {
            "event": "append_and_parse",
            "created_at": timezone.now().isoformat(),
            "source": "file+textarea" if uploaded_text.strip() and text.strip() else "file" if uploaded_text.strip() else "textarea",
            "use_ai_markup": use_ai_markup,
            "clear_work_file": clear_work_file,
            "original_files": original_files,
            "work_file": str(current_path.relative_to(PROJECT_ROOT)),
            "run_id": run_id,
        },
    )
    return batch_id, run_id


def run_parser_for_batch(batch_id, current_path, use_ai_markup=False):
    parser_input_path = current_path
    ai_markup_path = None
    if use_ai_markup:
        ai_result = run_checked_subprocess(
            [
                sys.executable,
                str(AI_MARKUP_SCRIPT),
                "--input",
                str(current_path),
                "--batch",
                batch_id,
            ]
        )
        ai_markup_path = parse_ai_markup_path(ai_result.stdout)
        parser_input_path = ai_markup_path

    run_cmd = [
        sys.executable,
        str(PARSER_SCRIPT),
        "run",
        "--input",
        str(parser_input_path),
        "--batch",
        batch_id,
        "--write-normalized",
    ]
    run_result = run_checked_subprocess(run_cmd)
    run_id = parse_run_id(run_result.stdout)
    compare_cmd = [
        sys.executable,
        str(PARSER_SCRIPT),
        "compare",
        "--run-dir",
        str(PARSER_RUNS_ROOT / run_id),
        "--editor-db",
        str(EDITOR_DB),
    ]
    run_checked_subprocess(compare_cmd)
    ensure_stage_pages(PARSER_RUNS_ROOT / run_id)
    if ai_markup_path:
        manifest_path = PARSER_RUNS_ROOT / run_id / "run_manifest.json"
        manifest = read_json_file(manifest_path, default={})
        manifest["ai_markup_path"] = str(ai_markup_path.relative_to(PROJECT_ROOT))
        manifest["ai_markup_used"] = True
        write_json_file(manifest_path, manifest)
    return run_id


def initial_parser_review_page(run_id):
    container_path = PARSER_RUNS_ROOT / run_id / "container_resolution.tsv"
    if count_tsv_data_rows(container_path) > 0:
        return "review_containers.html"
    author_path = PARSER_RUNS_ROOT / run_id / "author_resolution.tsv"
    if count_tsv_data_rows(author_path) > 0:
        return "review_authors.html"
    return "review_report.html"


def clear_parser_staging_data():
    stats = {"parser_runs": 0, "incoming_batches": 0, "normalized_files": 0}
    if PARSER_RUNS_ROOT.exists():
        for path in PARSER_RUNS_ROOT.iterdir():
            if path.is_dir():
                rmtree(path)
                stats["parser_runs"] += 1
            elif path.is_file():
                path.unlink()
    if INCOMING_ROOT.exists():
        for path in INCOMING_ROOT.iterdir():
            if path.is_dir():
                rmtree(path)
                stats["incoming_batches"] += 1
            elif path.is_file():
                path.unlink()
    normalized_dir = PROJECT_ROOT / "source" / "normalized_text"
    if normalized_dir.exists():
        for path in normalized_dir.glob("*.jsonl"):
            path.unlink()
            stats["normalized_files"] += 1
    PARSER_RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    INCOMING_ROOT.mkdir(parents=True, exist_ok=True)
    normalized_dir.mkdir(parents=True, exist_ok=True)
    return stats


def run_checked_subprocess(command):
    try:
        return subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(detail) from exc


def parse_run_id(output):
    for line in output.splitlines():
        if line.startswith("run_id="):
            return line.split("=", 1)[1].strip()
    raise ValueError(f"Parser did not report run_id. Output: {output}")


def parse_ai_markup_path(output):
    for line in output.splitlines():
        if line.startswith("ai_markup_path="):
            return Path(line.split("=", 1)[1].strip()).resolve()
    raise ValueError(f"AI markup did not report output path. Output: {output}")


def ensure_stage_pages(run_dir):
    for filename in ("review_stage2.html", "review_stage3.html"):
        target = run_dir / filename
        if target.exists():
            continue
        template = PARSER_REVIEW_TEMPLATES_ROOT / filename
        if template.exists():
            copy2(template, target)
        else:
            target.write_text(
                f"<!doctype html><meta charset='utf-8'><title>{filename}</title><p>Шаблон {filename} ещё не создан для этого run.</p>",
                encoding="utf-8",
            )


def next_parser_batch_id():
    today = timezone.localdate().strftime("%Y-%m-%d")
    INCOMING_ROOT.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"^{re.escape(today)}-(\d{{3}})$")
    last = 0
    for path in INCOMING_ROOT.iterdir():
        if not path.is_dir():
            continue
        match = pattern.match(path.name)
        if match:
            last = max(last, int(match.group(1)))
    return f"{today}-{last + 1:03d}"


def sanitize_filename(value):
    value = Path(value or "upload.txt").name
    value = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._ -]+", "-", value).strip(" .-")
    if not value:
        value = "upload.txt"
    if not value.lower().endswith(".txt"):
        value += ".txt"
    return value


def normalize_submitted_bibliography_text(value):
    value = value.replace("\ufeff", "").replace("\u00a0", " ")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(lines).strip()


def read_json_file(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_file(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_batch_event(batch_dir, event):
    event_path = batch_dir / "batch_events.jsonl"
    with event_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def list_parser_batches():
    if not INCOMING_ROOT.exists():
        return []
    batches = []
    for path in sorted([item for item in INCOMING_ROOT.iterdir() if item.is_dir()], reverse=True):
        manifest = read_json_file(path / "batch_manifest.json", default={})
        current = path / "work" / "current.txt"
        batches.append(
            {
                "batch_id": path.name,
                "path": str(path.relative_to(PROJECT_ROOT)),
                "updated_at": manifest.get("updated_at") or manifest.get("created_at") or "",
                "runs": manifest.get("runs", []),
                "current_exists": current.exists(),
                "current_size": current.stat().st_size if current.exists() else 0,
            }
        )
    return batches[:20]


def list_parser_runs():
    if not PARSER_RUNS_ROOT.exists():
        return []
    runs = []
    for path in sorted([item for item in PARSER_RUNS_ROOT.iterdir() if item.is_dir()], reverse=True):
        manifest = read_json_file(path / "run_manifest.json", default={})
        runs.append(
            {
                "run_id": path.name,
                "created_at": manifest.get("created_at", ""),
                "record_count": manifest.get("record_count", ""),
                "warning_count": manifest.get("warning_count", ""),
                "has_report": (path / "review_report.html").exists(),
            }
        )
    return runs[:20]


@staff_member_required
def dashboard(request):
    diagnostics = {
        "articles_without_container": Article.objects.filter(
            journal_issue__isnull=True,
            container_work__isnull=True,
            collection__isnull=True,
        ).count(),
        "articles_with_both_main_containers": Article.objects.filter(
            journal_issue__isnull=False,
            container_work__isnull=False,
        ).count(),
        "host_title_without_container": Article.objects.filter(
            work__host_title__gt="",
            journal_issue__isnull=True,
            container_work__isnull=True,
        ).count(),
        "empty_journals": Journal.objects.annotate(issue_count=Count("issues")).filter(issue_count=0).count(),
        "empty_issues": JournalIssue.objects.annotate(article_count=Count("articles")).filter(article_count=0).count(),
    }
    return render(
        request,
        "sources/dashboard.html",
        {
            "counts": {
                "works": Work.objects.count(),
                "articles": Article.objects.count(),
                "authors": Author.objects.count(),
                "sections": Section.objects.count(),
                "tags": Tag.objects.count(),
                "journals": Journal.objects.count(),
                "journal_issues": JournalIssue.objects.count(),
                "collections": collection_queryset().count(),
            },
            "diagnostics": diagnostics,
        },
    )


@staff_member_required
def google_sheets_sync(request):
    spreadsheet_id = settings.GOOGLE_SHEETS_SPREADSHEET_ID
    credentials_path = settings.GOOGLE_SHEETS_CREDENTIALS
    spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit" if spreadsheet_id else ""

    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        if not spreadsheet_id:
            messages.error(request, "Не задан GOOGLE_SHEETS_SPREADSHEET_ID.")
        elif not credentials_path:
            messages.error(request, "Не задан GOOGLE_SHEETS_CREDENTIALS.")
        elif not Path(credentials_path).exists():
            messages.error(request, f"JSON-ключ не найден: {credentials_path}")
        else:
            try:
                service = get_sheets_service(credentials_path)
                if action == "export":
                    write_sheet_values(service, spreadsheet_id, build_export_values())
                    messages.success(request, "Экспорт в Google Sheets завершён.")
                elif action in {"dry_run_import", "import"}:
                    values_by_sheet = read_sheet_values(service, spreadsheet_id)
                    is_dry_run = action == "dry_run_import"
                    if not is_dry_run:
                        backup_path = backup_google_import_database("before-google-import-web")
                        if backup_path:
                            messages.warning(request, f"Backup создан: {backup_path}")
                    stats = import_google_values(values_by_sheet, dry_run=is_dry_run)
                    if is_dry_run:
                        messages.warning(request, "Проверка завершена: изменения не сохранены.")
                    else:
                        refresh_target_model()
                    messages.success(request, f"Импорт завершён: {stats}")
                else:
                    messages.error(request, "Неизвестное действие Google Sheets.")
            except Exception as exc:
                messages.error(request, str(exc))
        return redirect("sources:google_sheets_sync")

    return render(
        request,
        "sources/google_sheets_sync.html",
        {
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_url": spreadsheet_url,
            "credentials_path": credentials_path,
            "credentials_exists": bool(credentials_path and Path(credentials_path).exists()),
            "total_works": Work.objects.count(),
            "total_authors": Author.objects.count(),
            "total_journals": Journal.objects.count(),
            "total_issues": JournalIssue.objects.count(),
            "total_articles": Article.objects.count(),
            "total_sections": Section.objects.count(),
            "total_tags": Tag.objects.count(),
        },
    )


@staff_member_required
def service_tools(request):
    spreadsheet_id = settings.GOOGLE_SHEETS_SPREADSHEET_ID
    credentials_path = settings.GOOGLE_SHEETS_CREDENTIALS
    spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit" if spreadsheet_id else ""

    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        try:
            if action in {"export", "dry_run_import", "import"}:
                result = run_google_sheet_action(action, spreadsheet_id, credentials_path)
            elif action in {"dry_run_merge_duplicate_issues", "merge_duplicate_issues"}:
                result = run_duplicate_issue_merge(apply=action == "merge_duplicate_issues")
            elif action in {"dry_run_cleanup_empty_containers", "cleanup_empty_containers"}:
                result = run_empty_container_cleanup(apply=action == "cleanup_empty_containers")
            elif action in {"dry_run_cleanup_unused_authors", "cleanup_unused_authors"}:
                result = run_unused_author_cleanup(apply=action == "cleanup_unused_authors")
            elif action in {"dry_run_merge_authors", "merge_authors"}:
                result = run_author_merge(
                    request.POST.get("target_author_id", ""),
                    request.POST.get("source_author_ids", ""),
                    apply=action == "merge_authors",
                )
            elif action in {"dry_run_cleanup_redundant_fields", "cleanup_redundant_fields"}:
                result = run_redundant_field_cleanup(apply=action == "cleanup_redundant_fields")
            else:
                result = {"level": "error", "message": "Неизвестная сервисная команда."}
        except Exception as exc:
            result = {"level": "error", "message": str(exc)}

        if result["level"] == "error":
            messages.error(request, result["message"])
        elif result["level"] == "warning":
            messages.warning(request, result["message"])
        else:
            messages.success(request, result["message"])
        return redirect("sources:service_tools")

    duplicate_plan = duplicate_issue_merge_plan()
    empty_plan = empty_container_cleanup_plan()
    author_plan = unused_author_cleanup_plan()
    redundant_plan = redundant_cleanup_plan()
    return render(
        request,
        "sources/service_tools.html",
        {
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_url": spreadsheet_url,
            "credentials_path": credentials_path,
            "credentials_exists": bool(credentials_path and Path(credentials_path).exists()),
            "duplicate_safe_groups": len(duplicate_plan["safe_groups"]),
            "duplicate_conflict_groups": len(duplicate_plan["conflict_groups"]),
            "empty_issue_count": len(empty_plan["empty_issue_ids"]),
            "empty_journal_count": len(empty_plan["empty_journal_ids"]),
            "empty_collection_count": len(empty_plan["empty_collection_ids"]),
            "unused_author_count": len(author_plan["unused_author_ids"]),
            "redundant_total": sum(len(rows) for rows in redundant_plan.values()),
        },
    )


@staff_member_required
def quick_create(request):
    authors = Author.objects.order_by("sort_name", "display_name", "author_id")
    journals = Journal.objects.order_by("title", "journal_id")

    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        try:
            if action == "author":
                obj = create_quick_author(request.POST.get("author_name", ""))
                messages.success(request, f"Автор создан: {obj.display_name}")
                return redirect("admin:sources_author_change", object_id=obj.author_id)
            if action in {"book", "container"}:
                obj = create_quick_work(
                    title=request.POST.get("work_title", ""),
                    author_id=request.POST.get("work_author_id", ""),
                    work_type=Work.WorkType.CONTAINER if action == "container" else Work.WorkType.BOOK,
                )
                messages.success(request, f"Запись создана: {obj.title}")
                return redirect("admin:sources_work_change", object_id=obj.work_id)
            if action == "journal":
                obj = create_quick_journal(request.POST.get("journal_title", ""))
                messages.success(request, f"Журнал создан: {obj.title}")
                return redirect("admin:sources_journal_change", object_id=obj.journal_id)
            if action == "journal_issue":
                obj = create_quick_journal_issue(
                    journal_id=request.POST.get("issue_journal_id", ""),
                    year=request.POST.get("issue_year", ""),
                    issue_number=request.POST.get("issue_number", ""),
                )
                messages.success(request, f"Выпуск создан: {obj}")
                return redirect("admin:sources_journalissue_change", object_id=obj.journal_issue_id)
            messages.error(request, "Неизвестный тип создаваемого объекта.")
        except Exception as exc:
            messages.error(request, str(exc))
        return redirect("sources:quick_create")

    return render(
        request,
        "sources/quick_create.html",
        {
            "authors": authors,
            "journals": journals,
        },
    )


def create_quick_author(name):
    name = clean_required(name, "Укажите имя автора.")
    existing = Author.objects.filter(display_name=name).first()
    if existing:
        return existing
    return Author.objects.create(
        author_id=next_id(Author, "author_id", "author"),
        display_name=name,
        sort_name=name,
    )


def create_quick_work(title, author_id, work_type):
    title = clean_required(title, "Укажите название.")
    language = default_language()
    work = Work.objects.create(
        work_id=next_id(Work, "work_id", "work"),
        source_number=next_synthetic_source_number(""),
        language=language,
        work_type=work_type,
        is_container=work_type == Work.WorkType.CONTAINER,
        title=title,
        description_status=Work.DescriptionStatus.NEEDS_REVIEW,
    )
    ensure_book_for_work(work)
    author_id = str(author_id or "").strip()
    if author_id:
        author = Author.objects.filter(author_id=author_id).first()
        if author is None:
            raise ValueError(f"Автор не найден: {author_id}")
        WorkAuthor.objects.create(
            work=work,
            author=author,
            sort_order=10,
            role="author",
            source_text=author.display_name,
            name_as_printed=author.display_name,
        )
        work.raw_author_string = author.display_name
        work.save(update_fields=["raw_author_string"])
    return work


def create_quick_journal(title):
    title = clean_required(title, "Укажите название журнала.")
    existing = Journal.objects.filter(title=title).first()
    if existing:
        return existing
    return Journal.objects.create(
        journal_id=next_id(Journal, "journal_id", "journal"),
        title=title,
    )


def create_quick_journal_issue(journal_id, year, issue_number):
    journal_id = clean_required(journal_id, "Выберите журнал.")
    journal = Journal.objects.filter(journal_id=journal_id).first()
    if journal is None:
        raise ValueError(f"Журнал не найден: {journal_id}")
    parsed_year = int_or_none_local(year)
    if parsed_year is None:
        raise ValueError("Укажите год выпуска числом.")
    issue_number = clean_required(issue_number, "Укажите номер выпуска.")
    existing = JournalIssue.objects.filter(journal=journal, year=parsed_year, issue_number=issue_number, volume="").first()
    if existing:
        return existing
    return JournalIssue.objects.create(
        journal_issue_id=next_id(JournalIssue, "journal_issue_id", "journal-issue"),
        journal=journal,
        year=parsed_year,
        publication_date=str(parsed_year),
        issue_number=issue_number,
    )


def clean_required(value, message):
    value = str(value or "").strip()
    if not value:
        raise ValueError(message)
    return value


def default_language():
    language = Language.objects.filter(code="ru").first() or Language.objects.order_by("sort_order", "title").first()
    if language is None:
        raise ValueError("В базе нет языка по умолчанию.")
    return language


def int_or_none_local(value):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Ожидалось целое число: {value}") from exc


def run_google_sheet_action(action, spreadsheet_id, credentials_path):
    if not spreadsheet_id:
        return {"level": "error", "message": "Не задан GOOGLE_SHEETS_SPREADSHEET_ID."}
    if not credentials_path:
        return {"level": "error", "message": "Не задан GOOGLE_SHEETS_CREDENTIALS."}
    if not Path(credentials_path).exists():
        return {"level": "error", "message": f"JSON-ключ не найден: {credentials_path}"}

    service = get_sheets_service(credentials_path)
    if action == "export":
        write_sheet_values(service, spreadsheet_id, build_export_values())
        return {"level": "success", "message": "Экспорт в Google Sheets завершён."}

    values_by_sheet = read_sheet_values(service, spreadsheet_id)
    is_dry_run = action == "dry_run_import"
    backup_path = None
    if not is_dry_run:
        backup_path = backup_google_import_database("before-google-import-web")
    stats = import_google_values(values_by_sheet, dry_run=is_dry_run)
    if not is_dry_run:
        refresh_target_model()
    prefix = "Проверка импорта завершена" if is_dry_run else "Импорт завершён"
    backup_note = f" Backup: {backup_path}." if backup_path else ""
    return {"level": "warning" if is_dry_run else "success", "message": f"{prefix}: {stats}.{backup_note}"}


@staff_member_required
def work_list(request):
    query = request.GET.get("q", "").strip()
    work_type = request.GET.get("type", "").strip()
    container_id = request.GET.get("container", "").strip()
    issue_id = request.GET.get("issue", "").strip()
    journal_id = request.GET.get("journal", "").strip()
    selected_container = Work.objects.filter(work_id=container_id).first() if container_id else None
    selected_issue = JournalIssue.objects.select_related("journal").filter(journal_issue_id=issue_id).first() if issue_id else None
    selected_journal = Journal.objects.filter(journal_id=journal_id).first() if journal_id else None
    works = (
        Work.objects.select_related(
            "source_section",
            "language",
            "article",
            "article__journal_issue",
            "article__journal_issue__journal",
            "article__container_work",
            "target_source",
            "target_source__article_placement",
            "target_source__article_placement__issue",
            "target_source__article_placement__issue__periodical",
            "target_source__article_placement__issue__source",
        )
        .prefetch_related(
            "authors",
            "tags",
            "workauthor_set__author",
            "worktag_set__tag",
            "group_items__group",
            "target_source__source_authors__author",
            "target_source__source_tags__tag",
            "target_source__group_items__group",
        )
        .annotate(contained_article_count=Count("contained_articles", distinct=True))
        .order_by("source_sequence", "source_number")
    )
    if selected_container:
        works = works.filter(article__container_work=selected_container)
    if selected_issue:
        works = works.filter(article__journal_issue=selected_issue)
    if selected_journal:
        works = works.filter(article__journal_issue__journal=selected_journal)
    if work_type in {Work.WorkType.BOOK, Work.WorkType.CONTAINER, Work.WorkType.ARTICLE, Work.WorkType.UNKNOWN}:
        works = works.filter(work_type=work_type)
    works = casefold_filter(
        works,
        query,
        [
            "work_id",
            "source_django_id",
            "source_number",
            "title",
            "subtitle",
            "raw_author_string",
            "publication_details",
            "article__journal_issue_id",
            "article__journal_issue__journal_id",
            "article__journal_issue__journal__title",
            "article__container_work_id",
            "article__container_work__title",
            lambda work: " ".join(author.display_name for author in work.authors.all()),
        ],
    )
    paginator = Paginator(works, 100)
    page_obj = paginator.get_page(request.GET.get("page"))
    for work in page_obj:
        enrich_work_for_list(work)
    return render(
        request,
        "sources/work_list.html",
        {
            "page_obj": page_obj,
            "query": query,
            "work_type": work_type,
            "selected_container": selected_container,
            "selected_issue": selected_issue,
            "selected_journal": selected_journal,
            "result_count": paginator.count,
        },
    )


@staff_member_required
def work_inspect(request, pk):
    work = get_object_or_404(
        Work.objects.select_related(
            "source_section",
            "language",
            "article",
            "article__journal_issue",
            "article__journal_issue__journal",
            "article__container_work",
            "article__collection",
            "target_source",
            "target_source__article_placement",
            "target_source__article_placement__issue",
            "target_source__article_placement__issue__periodical",
            "target_source__article_placement__issue__source",
            "target_source__article_placement__issue__legacy_journal_issue",
            "target_source__article_placement__issue__legacy_journal_issue__journal",
        ).prefetch_related("authors", "target_source__source_authors__author"),
        pk=pk,
    )
    context = build_work_inspect_context(work)
    return render(request, "sources/work_inspect.html", context)


@staff_member_required
def work_relations(request, pk):
    work = get_object_or_404(
        Work.objects.select_related(
            "source_section",
            "language",
            "article",
            "article__container_work",
            "article__journal_issue",
            "article__journal_issue__journal",
            "article__collection",
            "target_source",
            "target_source__article_placement",
            "target_source__article_placement__issue",
            "target_source__article_placement__issue__periodical",
        ).prefetch_related("authors"),
        pk=pk,
    )
    context = build_work_relations_context(work)
    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        if action == "create_parent_and_link":
            result = create_parent_and_link_work(work, request.POST, context)
            if result["error"]:
                messages.error(request, result["error"])
            else:
                messages.success(request, result["message"])
            return redirect("sources:work_relations", pk=work.pk)
        if action == "split_journal_issue_title":
            result = split_journal_issue_title_for_work(work, context)
            if result["error"]:
                messages.error(request, result["error"])
            else:
                messages.success(request, result["message"])
            return redirect("sources:work_relations", pk=work.pk)
        if action == "refine_journal_title_from_work":
            result = refine_journal_title_from_work(work, request.POST, context)
            if result["error"]:
                messages.error(request, result["error"])
            else:
                messages.success(request, result["message"])
            return redirect("sources:work_relations", pk=work.pk)
        target_id = request.POST.get("target_container_id", "").strip()
        result = link_work_to_container(work, target_id, context)
        if result["error"]:
            messages.error(request, result["error"])
        else:
            messages.success(request, result["message"])
        return redirect("sources:work_relations", pk=work.pk)
    return render(request, "sources/work_relations.html", context)


@staff_member_required
def issue_convert_to_collection(request, issue_id):
    issue = get_object_or_404(
        JournalIssue.objects.select_related("journal", "target_issue", "target_issue__periodical", "target_issue__source"),
        pk=issue_id,
    )
    plan = build_issue_to_collection_plan(issue)
    if request.method == "POST":
        if request.POST.get("confirm") != "yes":
            messages.error(request, "Подтвердите, что статьи будут перенесены из выпуска журнала в сборник.")
            return redirect("sources:issue_convert_to_collection", issue_id=issue.pk)
        result = apply_issue_to_collection(issue)
        if result["error"]:
            messages.error(request, result["error"])
        else:
            messages.success(request, result["message"])
        return redirect("sources:issue_convert_to_collection", issue_id=issue.pk)
    return render(request, "sources/issue_convert_to_collection.html", {"issue": issue, "plan": plan})


def build_work_inspect_context(work):
    source = related_or_none(work, "target_source")
    article = related_or_none(work, "article")
    placement = related_or_none(source, "article_placement") if source else None
    described_issues = described_issue_mentions(work, source)
    legacy_issue = article.journal_issue if article and article.journal_issue_id else None
    target_issue = placement.issue if placement else None
    warnings = work_inspect_warnings(work, source, article, placement, described_issues)
    authors = list(work.authors.all())
    author_relations = list(WorkAuthor.objects.select_related("author").filter(work=work).order_by("sort_order", "id"))
    for relation in author_relations:
        relation.role_label = contributor_role_label_ru(relation.role) if relation.role else "автор"
    source_authors = list(source.source_authors.select_related("author")) if source else []
    split_suggestion = work_multi_issue_split_suggestion(work, source, legacy_issue, target_issue, authors)
    bibliographic_description = editor_bibliographic_description(work, article, legacy_issue, target_issue, authors)
    return {
        "work": work,
        "source": source,
        "article": article,
        "placement": placement,
        "legacy_issue": legacy_issue,
        "target_issue": target_issue,
        "authors": authors,
        "author_relations": author_relations,
        "source_authors": source_authors,
        "bibliographic_description": bibliographic_description,
        "technical_bibliographic_line": work_debug_line(work),
        "bibliographic_line": bibliographic_description or work_debug_line(work),
        "described_issues": described_issues,
        "described_issue_text": "; ".join(described_issues) if described_issues else "",
        "legacy_issue_label": describe_journal_issue(legacy_issue) if legacy_issue else "",
        "target_issue_label": describe_issue(target_issue) if target_issue else "",
        "split_suggestion": split_suggestion,
        "warnings": warnings,
        "admin_urls": work_inspect_admin_urls(work, source, article, placement, legacy_issue, target_issue, authors),
        "journal_articles_url": journal_articles_url(legacy_issue, target_issue),
        "issue_articles_url": issue_articles_url(legacy_issue, target_issue),
    }


def build_work_relations_context(work):
    article = related_or_none(work, "article")
    authors = list(work.authors.all())
    source = related_or_none(work, "target_source")
    placement = related_or_none(source, "article_placement") if source else None
    target_issue = placement.issue if placement else None
    parent_fragment = extract_parent_fragment(work.publication_details)
    parsed_parent = parse_parent_fragment(parent_fragment)
    article_pages = (
        (article.pages_raw or article.pages).strip()
        if article and (article.pages_raw or article.pages)
        else extract_container_pages(work.publication_details)
    )
    candidates = suggest_container_candidates(work, parsed_parent)
    primary_candidate = candidates[0] if candidates else None
    siblings = sibling_container_candidates(primary_candidate["work"] if primary_candidate else None)
    parent_draft = parent_draft_from_parsed_fragment(parsed_parent)
    can_create_parent = bool(parent_fragment and not primary_candidate and not (article and article.container_work_id))
    journal_split = journal_issue_title_split_context(work, article)
    journal_title_refinement = journal_title_refinement_context(work, article)
    return {
        "work": work,
        "article": article,
        "authors": authors,
        "current_bibliographic_description": editor_bibliographic_description(
            work,
            article,
            article.journal_issue if article and article.journal_issue_id else None,
            target_issue,
            authors,
        ),
        "bibliographic_line": work_debug_line(work),
        "current_type_label": relation_work_type_label(work, article),
        "current_container": article.container_work if article and article.container_work_id else None,
        "current_container_label": work_container_label(article.container_work) if article and article.container_work_id else "",
        "current_journal_issue": article.journal_issue if article and article.journal_issue_id else None,
        "current_journal_issue_label": describe_journal_issue(article.journal_issue) if article and article.journal_issue_id else "",
        "article_pages": article_pages,
        "parent_fragment": parent_fragment,
        "parsed_parent": parsed_parent,
        "parent_draft": parent_draft,
        "can_create_parent": can_create_parent,
        "journal_split": journal_split,
        "journal_title_refinement": journal_title_refinement,
        "candidates": candidates,
        "primary_candidate": primary_candidate,
        "siblings": siblings,
        "admin_urls": {
            "work": reverse("admin:sources_work_change", args=[work.pk]),
            "article": reverse("admin:sources_article_change", args=[article.pk]) if article else "",
            "authors": [(author, reverse("admin:sources_author_change", args=[author.pk])) for author in work.authors.all()],
        },
    }


def editor_bibliographic_description(work, article, legacy_issue, target_issue, authors):
    author_text = "; ".join(author.display_name for author in authors) or work.raw_author_string
    title_line = " ".join(bit for bit in [author_text, work.title] if bit).strip()
    if article and article.container_work_id:
        container = article.container_work
        container_bits = [container.title]
        pub_bits = []
        if container.publication_place:
            pub_bits.append(container.publication_place)
        year = container.inferred_year or container.publication_date
        if year:
            pub_bits.append(str(year))
        if pub_bits:
            container_bits.append(", ".join(pub_bits))
        result = f"{title_line} // {'. — '.join(container_bits)}."
        pages = article.pages or article.pages_raw
        if pages:
            result = result.rstrip(".") + f". — С. {pages}."
        return result
    if article and (legacy_issue or target_issue):
        issue_label = ""
        journal_title = ""
        issue_year = None
        issue_number = ""
        pages = article.pages or article.pages_raw or work.article_pages
        if legacy_issue:
            journal_title = legacy_issue.journal.title
            issue_year = legacy_issue.year
            issue_number = legacy_issue.issue_number
        elif target_issue:
            journal_title = target_issue.periodical.title if target_issue.periodical else target_issue.title
            issue_year = target_issue.year
            issue_number = target_issue.issue_number
        issue_bits = [journal_title]
        if issue_year:
            issue_bits.append(str(issue_year))
        if issue_number:
            issue_bits.append(issue_number_label(issue_number))
        issue_label = ". — ".join(bit for bit in issue_bits if bit)
        result = f"{title_line} // {issue_label}." if issue_label else title_line
        if pages:
            result = result.rstrip(".") + f". — С. {pages}."
        return result
    return standalone_work_bibliographic_description(work, author_text)


def standalone_work_bibliographic_description(work, author_text=""):
    title_line = " ".join(bit for bit in [author_text, work.title] if bit).strip() or work.title
    place = work.publication_place
    publisher = work.publisher
    year = work.inferred_year or work.publication_date
    if (not place or not publisher) and "___" in str(work.publication_details or ""):
        parsed_parent = parse_parent_fragment(extract_parent_fragment(work.publication_details))
        place = place or parsed_parent.get("publication_place", "")
        publisher = publisher or parsed_parent.get("publisher", "")
        year = year or parsed_parent.get("year")
    pub = ""
    if place and publisher:
        pub = f"{place}: {publisher}"
    elif place:
        pub = place
    elif publisher:
        pub = publisher
    if year:
        pub = f"{pub}, {year}" if pub else str(year)
    parts = [title_line]
    if pub:
        parts.append(pub)
    extent = work.extent or work.physical_description
    if extent:
        parts.append(extent)
    return ". — ".join(parts).rstrip(".") + "."


def extract_parent_fragment(publication_details):
    if "___" not in str(publication_details or ""):
        return ""
    return normalize_whitespace_for_inspect(str(publication_details).split("___", 1)[1])


def extract_container_pages(publication_details):
    before_parent = str(publication_details or "").split("___", 1)[0]
    matches = re.findall(r"(?:—|-)\s*(?:С\.\s*)?(?P<pages>\d+(?:\s*[–—-]\s*\d+)?)\s*$", before_parent.strip())
    if not matches:
        return ""
    return normalize_whitespace_for_inspect(matches[-1])


def parse_parent_fragment(fragment):
    fragment = normalize_whitespace_for_inspect(fragment)
    if not fragment:
        return {"raw": "", "year": None, "part_number": "", "title": "", "publication_place": "", "publisher": ""}
    years = re.findall(r"\b((?:17|18|19|20)\d{2})\b", fragment)
    part_match = re.search(r"(Ч\.\s*\d+(?:\.\s*Т\.\s*\d+)?)", fragment, flags=re.IGNORECASE)
    title = fragment
    publication_place = ""
    publisher = ""
    if part_match:
        title = fragment[:part_match.start()].strip(" .;—-")
    else:
        title, publication_place, publisher = split_parent_title_and_place(fragment)
    return {
        "raw": fragment,
        "year": int(years[-1]) if years else None,
        "part_number": normalize_part_marker(part_match.group(1)) if part_match else "",
        "title": title,
        "publication_place": publication_place,
        "publisher": publisher,
    }


def split_parent_title_and_place(fragment):
    without_year = re.sub(r"(?:\s*[—-]?\s*,?\s*(?:17|18|19|20)\d{2})+\s*$", "", fragment).strip(" .;,—-")
    dash_match = re.match(r"^(?P<title>.+?)\s+[—-]\s+(?P<pub>.+)$", without_year)
    if dash_match:
        title = dash_match.group("title").strip(" .;,—-")
        pub = dash_match.group("pub").strip(" .;,—-")
        if ":" in pub:
            place, publisher = pub.split(":", 1)
            return title, place.strip(" .;,—-"), publisher.strip(" .;,—-")
        return title, pub, ""
    dot_pub_match = re.match(r"^(?P<title>[^.]+)\.\s*(?P<pub>.+)$", without_year)
    if dot_pub_match:
        title = dot_pub_match.group("title").strip(" .;,—-")
        pub = dot_pub_match.group("pub").strip(" .;,—-")
        if ":" in pub:
            place, publisher = pub.split(":", 1)
            return title, place.strip(" .;,—-"), publisher.strip(" .;,—-")
        return title, pub, ""
    match = re.match(r"^(?P<title>.+)\.\s*(?P<place>[^.;,]+)\s*,?$", without_year)
    if not match:
        return without_year, "", ""
    return match.group("title").strip(" .;,—-"), match.group("place").strip(" .;,—-"), ""


def parent_draft_from_parsed_fragment(parsed_parent):
    year = parsed_parent.get("year")
    return {
        "title": parsed_parent.get("title", ""),
        "publication_place": parsed_parent.get("publication_place", ""),
        "publisher": parsed_parent.get("publisher", ""),
        "publication_date": str(year) if year else "",
        "inferred_year": year,
        "work_type": Work.WorkType.BOOK,
    }


def normalize_part_marker(value):
    value = normalize_whitespace_for_inspect(value)
    match = re.search(r"Ч\.\s*(?P<part>\d+)(?:\.\s*Т\.\s*(?P<volume>\d+))?", value, flags=re.IGNORECASE)
    if match:
        result = f"Ч.{match.group('part')}"
        if match.group("volume"):
            result += f". Т.{match.group('volume')}"
        return result
    return re.sub(r"\s+", " ", value).strip()


def suggest_container_candidates(child_work, parsed_parent):
    if not parsed_parent.get("raw"):
        return []
    candidates = []
    queryset = Work.objects.exclude(pk=child_work.pk).filter(work_type__in=[Work.WorkType.BOOK, Work.WorkType.CONTAINER])
    year = parsed_parent.get("year")
    part_number = parsed_parent.get("part_number")
    if year:
        queryset = queryset.filter(Q(inferred_year=year) | Q(publication_date__contains=str(year)))
    for candidate in queryset.prefetch_related("authors").order_by("source_number", "work_id")[:500]:
        reasons = []
        score = 0
        if year and (candidate.inferred_year == year or str(year) in str(candidate.publication_date or "")):
            score += 2
            reasons.append(f"Год совпадает: {year}")
        candidate_part = normalize_part_marker(candidate.part_number)
        if part_number and candidate_part == part_number:
            score += 3
            reasons.append(f"Часть совпадает: {part_number}")
        title_similarity = rough_title_similarity(parsed_parent.get("title", ""), candidate.title)
        if title_similarity >= 0.75:
            score += 2
            reasons.append("Название похоже")
        elif title_similarity >= 0.35:
            score += 1
            reasons.append("Название похоже, но не совпадает дословно")
        if score < 3:
            continue
        title_mismatch = bool(parsed_parent.get("title")) and normalize_for_relation_match(parsed_parent["title"]) != normalize_for_relation_match(candidate.title)
        candidates.append({
            "work": candidate,
            "label": work_container_label(candidate),
            "reasons": reasons,
            "title_mismatch": title_mismatch,
            "score": score,
        })
    candidates.sort(key=lambda row: (-row["score"], row["work"].source_number, row["work"].work_id))
    return candidates[:5]


def rough_title_similarity(left, right):
    left_tokens = set(re.findall(r"[\wА-Яа-яЁё]+", normalize_for_relation_match(left)))
    right_tokens = set(re.findall(r"[\wА-Яа-яЁё]+", normalize_for_relation_match(right)))
    if not left_tokens or not right_tokens:
        return 0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def normalize_for_relation_match(value):
    return re.sub(r"\s+", " ", str(value or "").casefold().replace("ё", "е")).strip(" .;:,")


def sibling_container_candidates(container):
    if not container:
        return []
    prefix = container.title.split(" в ", 1)[0].strip()
    if len(prefix) < 10:
        return []
    siblings = []
    for work in Work.objects.exclude(pk=container.pk).filter(work_type__in=[Work.WorkType.BOOK, Work.WorkType.CONTAINER]).order_by("inferred_year", "part_number", "source_number")[:1000]:
        if prefix.casefold() in work.title.casefold():
            siblings.append({"work": work, "label": work_container_short_label(work)})
    return siblings[:12]


def work_container_label(work):
    bits = [work.title]
    if work.part_number:
        bits.append(work.part_number)
    if work.publication_place or work.publisher or work.inferred_year or work.publication_date:
        pub = []
        if work.publication_place:
            pub.append(work.publication_place)
        if work.publisher:
            pub.append(work.publisher)
        year = work.inferred_year or work.publication_date
        if year:
            pub.append(str(year))
        bits.append(": ".join(pub[:2]) + (f", {pub[2]}" if len(pub) > 2 else ""))
    if work.extent:
        bits.append(work.extent)
    return ". — ".join(str(bit).strip(" .") for bit in bits if str(bit or "").strip())


def work_container_short_label(work):
    bits = [work.work_id]
    if work.part_number:
        bits.append(work.part_number)
    if work.inferred_year or work.publication_date:
        bits.append(str(work.inferred_year or work.publication_date))
    bits.append(work.title)
    return " — ".join(bits)


def relation_work_type_label(work, article):
    if article and article.container_work_id:
        return "статья/раздел в книге"
    if work.work_type == Work.WorkType.ARTICLE:
        return "статья"
    if work.work_type == Work.WorkType.BOOK:
        return "самостоятельная книга"
    if work.work_type == Work.WorkType.CONTAINER:
        return "контейнер"
    return "не определён"


def journal_issue_title_split_context(work, article):
    if not article or not article.journal_issue_id or not article.journal_issue:
        return {"can_split": False}
    issue = article.journal_issue
    journal = issue.journal
    parsed = split_issue_suffix_from_journal_title(journal.title)
    if not parsed:
        return {"can_split": False}
    source = related_or_none(work, "target_source")
    placement = related_or_none(source, "article_placement") if source else None
    target_issue = placement.issue if placement else None
    nearby_work = nearby_periodical_context_work(parsed["journal_title"], issue.year)
    return {
        "can_split": True,
        "current_journal_title": journal.title,
        "current_issue_label": describe_journal_issue(issue),
        "current_year": issue.year,
        "proposed_journal_title": parsed["journal_title"],
        "proposed_issue_number": parsed["issue_number"],
        "proposed_year": issue.year or (target_issue.year if target_issue else None),
        "nearby_work": nearby_work,
        "nearby_work_label": work_container_label(nearby_work) if nearby_work else "",
    }


def split_issue_suffix_from_journal_title(title):
    match = re.match(
        r"^(?P<journal>.+?)[\.\s]+(?P<issue>(?:Вып|Выпуск)\.?\s*[\w\dIVXLCivxlcА-Яа-яЁё–—-]+)\s*$",
        normalize_whitespace_for_inspect(title),
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    journal_title = match.group("journal").strip(" .;—-")
    issue_number = normalize_issue_number_text(match.group("issue"))
    if not journal_title or not issue_number:
        return None
    return {"journal_title": journal_title, "issue_number": issue_number}


def journal_title_refinement_context(work, article):
    if not article or not article.journal_issue_id or not article.journal_issue:
        return {"can_refine": False}
    issue = article.journal_issue
    journal = issue.journal
    if not journal or not journal.title:
        return {"can_refine": False}
    nearby_work = nearby_periodical_context_work(journal.title, issue.year)
    if not nearby_work:
        return {"can_refine": False}
    current_title = normalize_for_relation_match(journal.title)
    proposed_title = normalize_for_relation_match(nearby_work.title)
    if not current_title or current_title == proposed_title:
        return {"can_refine": False}
    if current_title not in proposed_title:
        return {"can_refine": False}
    periodical = None
    source = related_or_none(work, "target_source")
    placement = related_or_none(source, "article_placement") if source else None
    if placement and placement.issue_id:
        periodical = placement.issue.periodical
    existing_journal = Journal.objects.filter(title=nearby_work.title).first()
    existing_periodical = Periodical.objects.filter(title=nearby_work.title).first()
    affected_articles = Article.objects.filter(journal_issue__journal=journal).count()
    affected_placements = ArticlePlacement.objects.filter(issue__periodical=periodical).count() if periodical else 0
    return {
        "can_refine": True,
        "context_work": nearby_work,
        "context_work_label": work_container_label(nearby_work),
        "current_journal": journal,
        "current_journal_title": journal.title,
        "current_periodical": periodical,
        "current_periodical_title": periodical.title if periodical else "",
        "proposed_title": nearby_work.title,
        "issue": issue,
        "issue_label": describe_journal_issue(issue),
        "issue_number": issue.issue_number,
        "year": issue.year,
        "existing_journal": existing_journal,
        "existing_periodical": existing_periodical,
        "will_reuse_existing": bool(existing_journal or existing_periodical),
        "affected_articles": affected_articles,
        "affected_placements": affected_placements,
    }


def normalize_issue_number_text(value):
    value = normalize_whitespace_for_inspect(value).replace("Выпуск", "Вып.").replace("выпуск", "Вып.")
    value = re.sub(r"(?i)^вып\.?\s*", "Вып. ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def issue_number_label(value):
    value = str(value or "").strip()
    if not value:
        return ""
    if re.match(r"(?i)^вып", value):
        return value
    return f"№ {value}"


def nearby_periodical_context_work(journal_title, year):
    normalized = normalize_for_relation_match(journal_title)
    candidates = Work.objects.filter(work_type__in=[Work.WorkType.CONTAINER, Work.WorkType.BOOK]).order_by("inferred_year", "source_number", "work_id")
    best = None
    best_score = -1
    for work in candidates[:2000]:
        title = normalize_for_relation_match(work.title)
        if normalized not in title:
            continue
        score = 1
        if year and work.inferred_year and abs(work.inferred_year - year) <= 5:
            score += 2
        if work.work_type == Work.WorkType.CONTAINER:
            score += 1
        if score > best_score:
            best = work
            best_score = score
    return best


def link_work_to_container(work, target_id, context):
    if not target_id:
        return {"error": "Не выбран контейнер.", "message": ""}
    target = Work.objects.filter(pk=target_id).first()
    if not target:
        return {"error": f"Контейнер {target_id} не найден.", "message": ""}
    if target.pk == work.pk:
        return {"error": "Запись нельзя связать сама с собой.", "message": ""}
    article = related_or_none(work, "article")
    if article and article.container_work_id == target.pk:
        return {"error": "Запись уже связана с этим контейнером.", "message": ""}
    if article and article.container_work_id and article.container_work_id != target.pk:
        return {"error": f"Запись уже связана с другим контейнером: {article.container_work_id}. Автоматическая замена запрещена.", "message": ""}
    if article and (article.journal_issue_id or article.collection_id):
        return {"error": "У записи уже есть другое размещение. Сначала проверьте существующую связь вручную.", "message": ""}
    backup = backup_sqlite_database("before-work-container-link")
    with transaction.atomic():
        article = Article.objects.filter(work=work).first()
        if not article:
            article = Article.objects.create(article_id=next_article_id_for_work(work), work=work)
        article.container_work = target
        pages = context.get("article_pages", "")
        if pages:
            if not article.pages:
                article.pages = pages
            if not article.pages_raw:
                article.pages_raw = pages
        article.save()
        if work.work_type != Work.WorkType.ARTICLE:
            work.work_type = Work.WorkType.ARTICLE
            work.save(update_fields=["work_type"])
    backup_note = f" Backup: {backup}" if backup else " Backup не создан: текущая база не SQLite-файл."
    return {"error": "", "message": f"Запись {work.work_id} связана с книгой {target.work_id} как статья/раздел.{backup_note}"}


def split_journal_issue_title_for_work(work, context):
    article = related_or_none(work, "article")
    split = context.get("journal_split") or journal_issue_title_split_context(work, article)
    if not split.get("can_split"):
        return {"error": "Для этой записи не найдено ошибочное название журнала с номером выпуска.", "message": ""}
    if not article or not article.journal_issue_id:
        return {"error": "Статья не связана с legacy-выпуском журнала.", "message": ""}
    backup = backup_sqlite_database("before-split-journal-issue-title")
    with transaction.atomic():
        journal = get_or_create_normalized_journal(split["proposed_journal_title"])
        periodical = get_or_create_normalized_periodical(split["proposed_journal_title"], journal)
        legacy_issue = get_or_create_normalized_journal_issue(
            journal,
            split.get("proposed_year"),
            split["proposed_issue_number"],
        )
        target_issue = get_or_create_normalized_target_issue(
            periodical,
            legacy_issue,
            split.get("proposed_year"),
            split["proposed_issue_number"],
        )
        article.journal_issue = legacy_issue
        article.save(update_fields=["journal_issue"])
        source = related_or_none(work, "target_source")
        placement = related_or_none(source, "article_placement") if source else None
        if placement:
            placement.issue = target_issue
            if not placement.pages_raw and article.pages_raw:
                placement.pages_raw = article.pages_raw
            placement.save()
    backup_note = f" Backup: {backup}" if backup else " Backup не создан: текущая база не SQLite-файл."
    return {
        "error": "",
        "message": (
            f"Журнал и выпуск разделены: статья {work.work_id} связана с выпуском "
            f"«{split['proposed_issue_number']}», {split.get('proposed_year') or '-'} журнала «{split['proposed_journal_title']}».{backup_note}"
        ),
    }


def get_or_create_normalized_journal(title):
    journal = Journal.objects.filter(title=title).first()
    if journal:
        return journal
    return Journal.objects.create(journal_id=next_id(Journal, "journal_id", "journal"), title=title)


def get_or_create_normalized_periodical(title, journal):
    periodical = Periodical.objects.filter(title=title).first()
    if periodical:
        return periodical
    return Periodical.objects.create(
        periodical_id=journal.journal_id if not Periodical.objects.filter(periodical_id=journal.journal_id).exists() else next_id(Periodical, "periodical_id", "periodical"),
        legacy_journal=journal if not hasattr(journal, "target_periodical") else None,
        title=title,
    )


def get_or_create_normalized_journal_issue(journal, year, issue_number):
    issue = JournalIssue.objects.filter(journal=journal, year=year, issue_number=issue_number).first()
    if issue:
        return issue
    return JournalIssue.objects.create(
        journal_issue_id=next_id(JournalIssue, "journal_issue_id", "journal-issue"),
        journal=journal,
        year=year,
        issue_number=issue_number,
    )


def get_or_create_normalized_target_issue(periodical, legacy_issue, year, issue_number):
    issue = Issue.objects.filter(periodical=periodical, year=year, issue_number=issue_number).first()
    if issue:
        return issue
    return Issue.objects.create(
        issue_id=legacy_issue.journal_issue_id if not Issue.objects.filter(issue_id=legacy_issue.journal_issue_id).exists() else next_id(Issue, "issue_id", "issue"),
        legacy_journal_issue=legacy_issue if not hasattr(legacy_issue, "target_issue") else None,
        issue_type=Issue.IssueType.PERIODICAL_ISSUE,
        periodical=periodical,
        year=year,
        issue_number=issue_number,
    )


def create_parent_and_link_work(work, post_data, context):
    article = related_or_none(work, "article")
    if article and article.container_work_id:
        return {"error": "Запись уже связана с родительской записью. Автоматическая замена запрещена.", "message": ""}
    if article and (article.journal_issue_id or article.collection_id):
        return {"error": "У записи уже есть другое размещение. Сначала проверьте существующую связь вручную.", "message": ""}
    parent_title = str(post_data.get("parent_title", "")).strip()
    if not parent_title:
        return {"error": "Укажите название родительской записи.", "message": ""}
    publication_place = str(post_data.get("parent_publication_place", "")).strip()
    publisher = str(post_data.get("parent_publisher", "")).strip()
    publication_date = str(post_data.get("parent_publication_date", "")).strip()
    if publication_date and not re.fullmatch(r"(?:17|18|19|20)\d{2}", publication_date):
        return {"error": "Год родительской записи должен быть четырёхзначным.", "message": ""}
    inferred_year = int(publication_date) if publication_date else None
    parent_work_type = post_data.get("parent_work_type", Work.WorkType.BOOK)
    if parent_work_type not in {Work.WorkType.BOOK, Work.WorkType.CONTAINER}:
        parent_work_type = Work.WorkType.BOOK
    duplicates = duplicate_parent_candidates(parent_title, inferred_year, exclude_work=work)
    if duplicates:
        labels = "; ".join(f"{duplicate.work_id} — {duplicate.title}" for duplicate in duplicates[:3])
        return {"error": f"Похоже, такая родительская запись уже есть. Сначала выберите существующую запись: {labels}", "message": ""}
    backup = backup_sqlite_database("before-create-parent-container-link")
    with transaction.atomic():
        source_number = next_synthetic_source_number("")
        parent = Work.objects.create(
            work_id=next_work_id(),
            source_number=source_number,
            source_sequence=source_number,
            source_section=work.source_section,
            language=work.language,
            work_type=parent_work_type,
            is_container=parent_work_type == Work.WorkType.CONTAINER,
            title=parent_title,
            publication_place=publication_place,
            publisher=publisher,
            publication_date=publication_date,
            inferred_year=inferred_year,
            publication_details=context.get("parent_fragment", ""),
            description_status=Work.DescriptionStatus.NEEDS_REVIEW,
        )
        ensure_book_for_work(parent)
        source_id = parent.work_id
        if Source.objects.filter(source_id=source_id).exists():
            source_id = next_id(Source, "source_id", "source")
        Source.objects.create(
            source_id=source_id,
            legacy_work=parent,
            source_number=parent.source_number,
            source_sequence=parent.source_sequence,
            source_type=Source.SourceType.MONOGRAPH,
            section=parent.source_section,
            language=parent.language,
            title=parent.title,
            publication_place=parent.publication_place,
            publisher=parent.publisher,
            publication_date=parent.publication_date,
            inferred_year=parent.inferred_year,
            raw_publication_details=parent.publication_details,
            data_source="editor",
            description_status=Source.DescriptionStatus.NEEDS_REVIEW,
        )
        article = Article.objects.filter(work=work).first()
        if not article:
            article = Article.objects.create(article_id=next_article_id_for_work(work), work=work)
        article.container_work = parent
        pages = context.get("article_pages", "")
        if pages:
            if not article.pages:
                article.pages = pages
            if not article.pages_raw:
                article.pages_raw = pages
        article.save()
        if work.work_type != Work.WorkType.ARTICLE:
            work.work_type = Work.WorkType.ARTICLE
            work.save(update_fields=["work_type"])
    backup_note = f" Backup: {backup}" if backup else " Backup не создан: текущая база не SQLite-файл."
    return {"error": "", "message": f"Создана родительская запись {parent.work_id} и связана с {work.work_id}.{backup_note}"}


def refine_journal_title_from_work(work, post_data, context):
    article = related_or_none(work, "article")
    refinement = context.get("journal_title_refinement") or journal_title_refinement_context(work, article)
    if not refinement.get("can_refine"):
        return {"error": "Для этой записи не найдено подходящее уточнение названия журнала.", "message": ""}
    context_work_id = str(post_data.get("context_work_id", "")).strip()
    context_work = refinement["context_work"]
    if context_work_id != context_work.work_id:
        return {"error": "Выбранная запись-источник названия не совпадает с предложением на странице.", "message": ""}
    if not article or not article.journal_issue_id:
        return {"error": "Статья не связана с выпуском журнала.", "message": ""}
    legacy_issue = article.journal_issue
    current_journal = legacy_issue.journal
    source = related_or_none(work, "target_source")
    placement = related_or_none(source, "article_placement") if source else None
    target_issue = placement.issue if placement else None
    proposed_title = context_work.title.strip()
    if not proposed_title:
        return {"error": "У записи-источника нет названия.", "message": ""}
    if current_journal.title == proposed_title and (not target_issue or not target_issue.periodical or target_issue.periodical.title == proposed_title):
        return {"error": "Журнал уже имеет это название.", "message": ""}
    backup = backup_sqlite_database("before-refine-journal-title")
    with transaction.atomic():
        existing_journal = Journal.objects.filter(title=proposed_title).exclude(pk=current_journal.pk).first()
        if existing_journal:
            legacy_issue.journal = existing_journal
            legacy_issue.save(update_fields=["journal"])
            final_journal = existing_journal
        else:
            current_journal.title = proposed_title
            current_journal.save(update_fields=["title"])
            final_journal = current_journal

        final_periodical = None
        if target_issue:
            current_periodical = target_issue.periodical
            existing_periodical = Periodical.objects.filter(title=proposed_title).exclude(pk=current_periodical.pk if current_periodical else None).first()
            if existing_periodical:
                target_issue.periodical = existing_periodical
                target_issue.save(update_fields=["periodical"])
                final_periodical = existing_periodical
            elif current_periodical:
                current_periodical.title = proposed_title
                if final_journal and current_periodical.legacy_journal_id in {None, final_journal.pk}:
                    current_periodical.legacy_journal = final_journal
                    current_periodical.save(update_fields=["title", "legacy_journal"])
                else:
                    current_periodical.save(update_fields=["title"])
                final_periodical = current_periodical
            else:
                final_periodical = get_or_create_normalized_periodical(proposed_title, final_journal)
                target_issue.periodical = final_periodical
                target_issue.save(update_fields=["periodical"])
    backup_note = f" Backup: {backup}" if backup else " Backup не создан: текущая база не SQLite-файл."
    final_title = final_periodical.title if final_periodical else final_journal.title
    return {
        "error": "",
        "message": (
            f"Название журнала уточнено по записи {context_work.work_id}: «{final_title}». "
            f"Выпуск «{legacy_issue.issue_number or '-'}», {legacy_issue.year or '-'} сохранён.{backup_note}"
        ),
    }


def duplicate_parent_candidates(title, year, exclude_work=None):
    normalized_title = normalize_for_relation_match(title)
    matches = []
    for candidate in Work.objects.exclude(pk=exclude_work.pk if exclude_work else None).filter(work_type__in=[Work.WorkType.BOOK, Work.WorkType.CONTAINER]).order_by("source_number", "work_id"):
        if normalize_for_relation_match(candidate.title) != normalized_title:
            continue
        if year and not (candidate.inferred_year == year or str(year) in str(candidate.publication_date or "")):
            continue
        matches.append(candidate)
        if len(matches) >= 5:
            break
    return matches


def next_article_id_for_work(work):
    candidate = f"article-from-{work.work_id}"
    if not Article.objects.filter(article_id=candidate).exists():
        return candidate
    number = Article.objects.count() + 1
    while True:
        candidate = f"article-{number:06d}"
        if not Article.objects.filter(article_id=candidate).exists():
            return candidate
        number += 1


def work_multi_issue_split_suggestion(work, source, legacy_issue, target_issue, authors):
    candidates = [
        work.publication_details,
        source.raw_publication_details if source else "",
        work_debug_line(work),
    ]
    for candidate in candidates:
        result = split_multi_issue_bibliographic_line(candidate)
        if result["can_split"]:
            return result

    journal_title = ""
    first_year = None
    if legacy_issue and legacy_issue.journal:
        journal_title = legacy_issue.journal.title
        first_year = legacy_issue.year
    elif target_issue:
        if target_issue.periodical:
            journal_title = target_issue.periodical.title
        elif target_issue.legacy_journal_issue and target_issue.legacy_journal_issue.journal:
            journal_title = target_issue.legacy_journal_issue.journal.title
        first_year = target_issue.year or (target_issue.legacy_journal_issue.year if target_issue.legacy_journal_issue else None)
    issue_numbers = issue_numbers_from_value(work.volume_number or (source.volume_number if source else ""))
    if not (journal_title and first_year and len(issue_numbers) == 2):
        return no_split_result("Автоматическое разделение не предложено: выпусков меньше двух или не хватает журнала/года.")
    author_text = "; ".join(author.display_name for author in authors) or work.raw_author_string
    prefix = normalize_whitespace_for_inspect(f"{author_text} {work.title}")
    issues = [
        {"journal": journal_title, "year": str(first_year), "issue": issue_numbers[0]},
        {"journal": journal_title, "year": str(first_year + 1), "issue": issue_numbers[1]},
    ]
    return split_result(prefix, journal_title, issues, inferred=True)


def split_multi_issue_bibliographic_line(value):
    value = normalize_bibliographic_split_text(value)
    if "//" not in value or ";" not in value:
        return no_split_result("Автоматическое разделение не предложено: нет явного списка выпусков.")
    prefix, host = [part.strip() for part in value.split("//", 1)]
    if not prefix or not host:
        return no_split_result("Автоматическое разделение не предложено: не удалось отделить статью от журнала.")
    journal_match = re.match(r"(?P<journal>.+?)(?:\.\s*[—-]\s*|\s+[—-]\s*)(?P<rest>.+)$", host)
    if not journal_match:
        return no_split_result("Автоматическое разделение не предложено: не удалось уверенно выделить журнал.")
    journal = normalize_whitespace_for_inspect(journal_match.group("journal"))
    parts = [part.strip(" .") for part in journal_match.group("rest").split(";") if part.strip(" .")]
    if len(parts) < 2:
        return no_split_result("Автоматическое разделение не предложено: найден только один выпуск.")
    issues = []
    for part in parts:
        match = re.search(r"(?P<year>(?:17|18|19|20)\d{2}).*?№\s*(?P<issue>[\dIVXLCivxlc]+(?:\s*[–—-]\s*[\dIVXLCivxlc]+)?)", part)
        if not match:
            return no_split_result("Автоматическое разделение не предложено: после точки с запятой непонятная форма выпуска.")
        issues.append({"journal": journal, "year": match.group("year"), "issue": normalize_issue_for_split(match.group("issue"))})
    return split_result(prefix, journal, issues, inferred=False)


def split_result(prefix, journal, issues, inferred=False):
    suggested_lines = [
        f"{prefix} // {journal}. — {issue['year']}. — № {issue['issue']}."
        for issue in issues
    ]
    message = "В описании перечислены два выпуска одного журнала. Безопаснее обработать их как две отдельные строки импорта."
    if inferred:
        message += " Второй год предложен по простой последовательности годов; перед импортом проверьте строки."
    return {
        "can_split": True,
        "message": message,
        "suggested_lines": suggested_lines,
        "copy_text": "\n".join(suggested_lines),
        "issues": issues,
        "inferred": inferred,
    }


def no_split_result(reason):
    return {"can_split": False, "message": reason, "suggested_lines": [], "copy_text": "", "issues": [], "inferred": False}


def normalize_bibliographic_split_text(value):
    value = str(value or "").strip()
    value = re.sub(r"\s+", " ", value)
    return value


def issue_numbers_from_value(value):
    numbers = []
    for match in re.finditer(r"№\s*(?P<issue>[\dIVXLCivxlc]+(?:\s*[–—-]\s*[\dIVXLCivxlc]+)?)", str(value or "")):
        number = normalize_issue_for_split(match.group("issue"))
        if number and number not in numbers:
            numbers.append(number)
    return numbers


def normalize_issue_for_split(value):
    return normalize_whitespace_for_inspect(str(value or "").replace("-", "–").replace("—", "–"))


def described_issue_mentions(work, source=None):
    fields = [
        work.volume_number,
        work.publication_date,
        work.publication_details,
        work.host_title,
        work.article_pages,
    ]
    if source:
        fields.extend([
            source.volume_number,
            source.publication_date,
            source.raw_publication_details,
            source.raw_host_title,
        ])
    text = " ; ".join(str(value or "") for value in fields if str(value or "").strip())
    raw_mentions = re.findall(r"(?:\b(?:17|18|19|20)\d{2}\b[^\d№;]{0,24})?№\s*[\dIVXLCivxlc]+(?:\s*[–—-]\s*[\dIVXLCivxlc]+)?", text)
    mentions = []
    for mention in raw_mentions:
        cleaned = normalize_whitespace_for_inspect(mention)
        if cleaned and cleaned not in mentions:
            mentions.append(cleaned)
    return mentions


def normalize_whitespace_for_inspect(value):
    return re.sub(r"\s+", " ", str(value or "").replace("—", " ").strip(" .;,"))


def work_inspect_warnings(work, source, article, placement, described_issues):
    warnings = []
    if work.work_type == Work.WorkType.ARTICLE and not article and not placement:
        warnings.append("Статья не связана с выпуском или сборником.")
    if article and not (article.journal_issue_id or article.container_work_id or article.collection_id):
        warnings.append("Статья не связана с выпуском или сборником.")
    if source and source.source_type == Source.SourceType.ARTICLE and not placement:
        warnings.append("Target-запись статьи не имеет ArticlePlacement.")
    if placement and placement.issue and placement.issue.issue_type == Issue.IssueType.PERIODICAL_ISSUE and not placement.issue.periodical:
        warnings.append("Статья связана с выпуском, но выпуск не связан с журналом/periodical.")
    if len(described_issues) > 1 and placement:
        warnings.append("Возможная проблема: описание содержит несколько выпусков, но заведено одно размещение.")
    return unique_preserve_order(warnings)


def unique_preserve_order(values):
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def work_inspect_admin_urls(work, source, article, placement, legacy_issue, target_issue, authors):
    urls = {
        "work": reverse("admin:sources_work_change", args=[work.pk]),
        "source": reverse("admin:sources_source_change", args=[source.pk]) if source else "",
        "article": reverse("admin:sources_article_change", args=[article.pk]) if article else "",
        "placement": reverse("admin:sources_articleplacement_change", args=[placement.pk]) if placement else "",
        "legacy_issue": reverse("admin:sources_journalissue_change", args=[legacy_issue.pk]) if legacy_issue else "",
        "issue": reverse("admin:sources_issue_change", args=[target_issue.pk]) if target_issue else "",
        "authors": [(author, reverse("admin:sources_author_change", args=[author.pk])) for author in authors],
    }
    return urls


def journal_articles_url(legacy_issue, target_issue):
    if legacy_issue and legacy_issue.journal_id:
        return f"{reverse('sources:work_list')}?journal={legacy_issue.journal_id}"
    if target_issue and target_issue.legacy_journal_issue and target_issue.legacy_journal_issue.journal_id:
        return f"{reverse('sources:work_list')}?journal={target_issue.legacy_journal_issue.journal_id}"
    return ""


def issue_articles_url(legacy_issue, target_issue):
    if legacy_issue:
        return f"{reverse('sources:work_list')}?issue={legacy_issue.pk}"
    if target_issue and target_issue.legacy_journal_issue:
        return f"{reverse('sources:work_list')}?issue={target_issue.legacy_journal_issue.pk}"
    return ""


@staff_member_required
def delete_work(request, pk):
    if request.method != "POST":
        return redirect("sources:work_list")
    work = get_object_or_404(Work, pk=pk)
    title = work.title
    result = delete_selected_works([work.work_id])
    if result["error"]:
        messages.error(request, result["error"])
    else:
        messages.success(request, f"Удалено: {title}")
    return redirect(request.POST.get("next") or "sources:work_list")


@staff_member_required
def duplicate_work(request, pk):
    if request.method != "POST":
        return redirect("sources:work_list")
    source = get_object_or_404(Work, pk=pk)
    backup = backup_sqlite_database("before-duplicate-work")
    with transaction.atomic():
        duplicate = create_work_duplicate(source)
        refresh_target_model()
    messages.success(request, f"Создан дубль: {duplicate.work_id}. Backup: {backup}")
    return redirect("admin:sources_work_change", object_id=duplicate.work_id)


@staff_member_required
def detach_work_to_book(request, pk):
    if request.method != "POST":
        return redirect("sources:work_list")
    work = get_object_or_404(Work, pk=pk)
    result = detach_selected_articles_to_books([work.work_id])
    if result["error"]:
        messages.error(request, result["error"])
    else:
        messages.success(request, result["message"])
    return redirect(request.POST.get("next") or "sources:work_list")


def enrich_work_for_list(work):
    article = related_or_none(work, "article")
    source = related_or_none(work, "target_source")
    placement = related_or_none(source, "article_placement") if source else None
    target_issue = placement.issue if placement else None
    work.list_container = None
    work.list_container_kind = ""
    work.can_delete_from_list = not bool(work.contained_article_count)
    work.can_detach_to_book = False
    if article:
        work.can_detach_to_book = bool(article.journal_issue_id or article.container_work_id or article.collection_id)
        if article.journal_issue:
            work.list_container = article.journal_issue
            work.list_container_kind = "journal_issue"
        if article.container_work:
            work.list_container = article.container_work
            work.list_container_kind = "container_work"
        if article.collection:
            work.list_container = article.collection
            work.list_container_kind = "legacy_collection"
    work.is_list_container = is_container_work(work) or bool(work.contained_article_count)
    work.editor_bibliographic_description = editor_bibliographic_description(
        work,
        article,
        article.journal_issue if article and article.journal_issue_id else None,
        target_issue,
        list(work.authors.all()),
    )
    work.full_bibliographic_debug = work_debug_line(work)


def is_container_work(work):
    return work.work_type == Work.WorkType.CONTAINER or work.is_container


def work_debug_line(work):
    values = [
        author_string(work),
        work.title,
        work.parallel_title,
        work.subtitle,
        work.title_remainder,
        work.volume_number,
        work.part_number,
        work.part_title,
        work.responsibility_statement or work.responsibility_note,
        work.edition_statement,
        work.additional_edition_statement,
        work.publication_place,
        work.publisher,
        work.publication_date,
        work.extent,
        work.illustrations,
        work.dimensions,
        work.circulation,
        work.accompanying_material,
        work.series_statement,
        work.notes,
        work.publication_details,
        work.isbn,
        work.issn,
    ]
    return " — ".join(str(value).strip() for value in values if str(value or "").strip())


def author_string(work):
    names = [author.display_name for author in work.authors.all()]
    return "; ".join(names) or work.raw_author_string


def related_or_none(obj, name):
    try:
        return getattr(obj, name)
    except ObjectDoesNotExist:
        return None


def casefold_filter(queryset, query, accessors):
    needle = (query or "").casefold()
    if not needle:
        return queryset
    return [obj for obj in iter_queryset_safely(queryset) if casefold_match(obj, needle, accessors)]


def iter_queryset_safely(queryset):
    iterator = getattr(queryset, "iterator", None)
    if iterator is None:
        return iter(queryset)
    return iterator(chunk_size=200)


def casefold_match(obj, needle, accessors):
    for accessor in accessors:
        if callable(accessor):
            value = accessor(obj)
        else:
            value = nested_attr(obj, accessor)
        if needle in str(value or "").casefold():
            return True
    return False


def nested_attr(obj, path):
    value = obj
    for part in path.split("__"):
        if value is None:
            return ""
        value = getattr(value, part, "")
    return value


def reverse_url(name, *args):
    return reverse(name, args=args)


def describe_journal_issue(issue):
    bits = [issue.journal.title]
    if issue.year:
        bits.append(str(issue.year))
    if issue.issue_number:
        bits.append(issue_number_label(issue.issue_number))
    if issue.volume:
        bits.append(f"т. {issue.volume}")
    return ", ".join(bits)

def describe_issue(issue):
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
        bits.append(issue_number_label(issue.issue_number))
    if issue.volume:
        bits.append(f"т. {issue.volume}")
    return ", ".join(bits)


@staff_member_required
def section_list(request):
    sections = Section.objects.select_related("parent").annotate(work_count=Count("source_works")).order_by("sort_order", "source_code")
    return render(request, "sources/section_list.html", {"sections": sections})


@staff_member_required
def periodical_list(request):
    query = request.GET.get("q", "").strip()
    record_type = request.GET.get("type", "").strip()

    journals = (
        Journal.objects.annotate(
            issue_count=Count("issues", distinct=True),
            article_count=Count("issues__articles", distinct=True),
        )
        .order_by("title")
    )
    collections = collection_queryset().annotate(contained_count=Count("contained_articles", distinct=True))
    if query:
        journals = casefold_filter(journals, query, ["journal_id", "title"])
        collections = casefold_filter(collections, query, ["work_id", "title", "raw_author_string", "publication_details"])

    rows = []
    if record_type in {"", "journal"}:
        for journal in journals:
            rows.append(
                {
                    "kind": "journal",
                    "id": journal.journal_id,
                    "title": journal.title,
                    "meta": f"{journal.issue_count} вып.; {journal.article_count} ст.",
                    "issue_count": journal.issue_count,
                    "article_count": journal.article_count,
                    "edit_url": "admin:sources_journal_change",
                    "edit_pk": journal.pk,
                    "detail_url": reverse_url("sources:journal_detail", journal.pk),
                    "work_list_url": f"{reverse_url('sources:work_list')}?journal={journal.pk}",
                    "convert_url": reverse_url("sources:convert_single_journal_article", journal.pk),
                    "can_convert_to_book": journal.article_count == 1,
                }
            )
    if record_type in {"", "collection"}:
        for collection in collections:
            rows.append(
                {
                    "kind": "collection",
                    "id": collection.work_id,
                    "title": collection.title,
                    "meta": f"{collection.inferred_year or 'без года'}; {collection.article_count} ст.",
                    "issue_count": "",
                    "article_count": collection.article_count,
                    "edit_url": "admin:sources_work_change",
                    "edit_pk": collection.pk,
                    "detail_url": reverse_url("sources:collection_detail", collection.pk),
                    "work_list_url": f"{reverse_url('sources:work_list')}?container={collection.pk}",
                    "convert_url": reverse_url("sources:convert_single_collection_article", collection.pk),
                    "can_convert_to_book": collection.article_count == 1,
                }
            )
    rows.sort(key=lambda row: (row["title"].casefold(), row["id"]))
    paginator = Paginator(rows, 100)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "sources/periodical_list.html",
        {
            "page_obj": page_obj,
            "result_count": paginator.count,
            "query": query,
            "record_type": record_type,
        },
    )


@staff_member_required
def journal_normalize(request):
    source_query = request.GET.get("source_q", "").strip()
    target_query = request.GET.get("target_q", "").strip()
    source_id = (request.POST.get("source_journal") if request.method == "POST" else request.GET.get("source_journal", "")).strip()
    target_id = (request.POST.get("target_journal") if request.method == "POST" else request.GET.get("target_journal", "")).strip()
    result = None

    if request.method == "POST":
        if request.POST.get("action") != "apply":
            messages.error(request, "Неизвестное действие.")
        elif not request.POST.get("confirm_apply"):
            messages.error(request, "Перед переносом подтвердите, что preview проверен.")
        elif not source_id or not target_id:
            messages.error(request, "Выберите ошибочный и правильный журнал.")
        elif source_id == target_id:
            messages.error(request, "Ошибочный и правильный журнал не должны совпадать.")
        else:
            result = apply_journal_normalization_plan(source_id, target_id, user=request.user if request.user.is_authenticated else None)
            messages.success(
                request,
                f"Нормализация выполнена: перенесено выпусков {len(result['moved_issues'])}, "
                f"слито выпусков {len(result['merged_issues'])}, перенесено статей {result['moved_articles']}. "
                f"Backup: {result.get('backup_path') or 'не создан'}."
            )

    source_options = journal_normalize_options(source_query, selected_id=source_id)
    target_options = journal_normalize_options(target_query, selected_id=target_id)
    source_journal = Journal.objects.filter(pk=source_id).first() if source_id else None
    target_journal = Journal.objects.filter(pk=target_id).first() if target_id else None
    plan = None
    if source_journal and target_journal and source_journal.pk != target_journal.pk:
        plan = build_journal_normalization_plan(source_journal.pk, target_journal.pk)

    return render(
        request,
        "sources/journal_normalize.html",
        {
            "source_query": source_query,
            "target_query": target_query,
            "source_id": source_id,
            "target_id": target_id,
            "source_options": source_options,
            "target_options": target_options,
            "source_journal": source_journal,
            "target_journal": target_journal,
            "plan": plan,
            "result": result,
        },
    )


def journal_normalize_options(query, selected_id=""):
    queryset = Journal.objects.annotate(
        issue_count=Count("issues", distinct=True),
        article_count=Count("issues__articles", distinct=True),
    ).order_by("title", "journal_id")
    if query:
        queryset = casefold_filter(queryset, query, ["journal_id", "title"])
    options = list(queryset[:120])
    if selected_id and not any(journal.pk == selected_id for journal in options):
        selected = (
            Journal.objects.filter(pk=selected_id)
            .annotate(issue_count=Count("issues", distinct=True), article_count=Count("issues__articles", distinct=True))
            .first()
        )
        if selected:
            options.insert(0, selected)
    return options


@staff_member_required
def relation_tool(request):
    filters = relation_filters(request)
    left_kind = request.GET.get("left", "work").strip()
    left_kind = left_kind if left_kind in LEFT_PANEL_KINDS else "work"
    right_kind = request.GET.get("right", "journal_issue").strip()
    right_query = request.GET.get("rq", "").strip()
    right_kind = right_kind if right_kind in RIGHT_PANEL_KINDS else "journal_issue"

    if request.method == "POST":
        action = request.POST.get("action", "link").strip()
        selected_ids = request.POST.getlist("selected_ids")
        target_kind = request.POST.get("target_kind", "").strip()
        target_id = request.POST.get("target_id", "").strip()
        if not selected_ids:
            messages.error(request, "Выберите одну или несколько строк слева.")
        else:
            result = apply_relation_action(
                action=action,
                left_kind=left_kind,
                selected_ids=selected_ids,
                target_kind=target_kind,
                target_id=target_id,
                title=request.POST.get("action_title", "").strip(),
            )
            if result["error"]:
                messages.error(request, result["error"])
            else:
                messages.success(request, result["message"])
        return redirect(f"{request.path}?{request.GET.urlencode()}")

    left_items = relation_left_queryset(left_kind, filters)
    paginator = Paginator(left_items, 80)
    page_obj = paginator.get_page(request.GET.get("page"))
    targets = right_panel_queryset(right_kind, right_query)[:200]

    return render(
        request,
        "sources/relation_tool.html",
        {
            "page_obj": page_obj,
            "filters": filters,
            "left_kind": left_kind,
            "left_kinds": LEFT_PANEL_KINDS,
            "right_kind": right_kind,
            "right_query": right_query,
            "right_kinds": RIGHT_PANEL_KINDS,
            "targets": targets,
            "result_count": paginator.count,
        },
    )


@staff_member_required
def journal_detail(request, pk):
    journal = get_object_or_404(Journal, pk=pk)
    issues = journal.issues.annotate(article_count=Count("articles", distinct=True)).order_by("year", "issue_number", "volume")
    articles = Article.objects.select_related("work", "journal_issue").filter(journal_issue__journal=journal).order_by("work__source_sequence", "work__source_number")
    return render(
        request,
        "sources/journal_detail.html",
        {
            "journal": journal,
            "issues": issues,
            "articles": articles[:200],
            "article_count": articles.count(),
        },
    )


@staff_member_required
def journal_issue_detail(request, pk, issue_id):
    journal = get_object_or_404(Journal, pk=pk)
    issue = get_object_or_404(
        JournalIssue.objects.select_related("journal", "target_issue", "target_issue__periodical"),
        pk=issue_id,
        journal=journal,
    )
    articles = (
        Article.objects.select_related("work")
        .prefetch_related("work__authors")
        .filter(journal_issue=issue)
        .order_by("work__source_sequence", "work__source_number", "work__work_id")
    )
    warnings = []
    if articles.count() == 1 and not issue.issue_number and not issue.volume:
        warnings.append("В выпуске одна статья и нет номера выпуска. Проверьте, не является ли этот выпуск сборником.")
    return render(
        request,
        "sources/journal_issue_detail.html",
        {
            "journal": journal,
            "issue": issue,
            "issue_label": describe_journal_issue(issue),
            "articles": articles,
            "article_count": articles.count(),
            "warnings": warnings,
        },
    )


@staff_member_required
def collection_detail(request, pk):
    container = get_object_or_404(Work, pk=pk)
    articles = Article.objects.select_related("work").filter(container_work=container).order_by("work__source_sequence", "work__source_number")
    return render(
        request,
        "sources/collection_detail.html",
        {
            "container": container,
            "articles": articles[:200],
            "article_count": articles.count(),
        },
    )


@staff_member_required
def convert_single_journal_article(request, pk):
    if request.method != "POST":
        return redirect("sources:journal_detail", pk=pk)
    journal = get_object_or_404(Journal, pk=pk)
    articles = list(Article.objects.select_related("work", "journal_issue").filter(journal_issue__journal=journal))
    if len(articles) != 1:
        messages.error(request, f"Преобразование возможно только для журнала с одной статьей. Сейчас статей: {len(articles)}.")
        return redirect("sources:journal_detail", pk=pk)
    article = articles[0]
    work = article.work
    with transaction.atomic():
        ensure_book_for_work(work)
        work.work_type = Work.WorkType.BOOK
        work.host_title = ""
        if article.journal_issue:
            work.publication_details = append_publication_detail(
                work.publication_details,
                journal_issue_raw_detail(article.journal_issue),
            )
            if not work.inferred_year:
                work.inferred_year = article.journal_issue.year
        work.save(update_fields=["work_type", "host_title", "publication_details", "inferred_year"])
        article.delete()
    messages.success(request, f"Статья преобразована в источник: {work.title}")
    return redirect(request.POST.get("next") or "sources:work_list")


def journal_issue_raw_detail(issue):
    bits = [issue.journal.title]
    if issue.year:
        bits.append(str(issue.year))
    if issue.issue_number:
        bits.append(f"№ {issue.issue_number}")
    if issue.volume:
        bits.append(f"т. {issue.volume}")
    if issue.part_number:
        bits.append(f"ч. {issue.part_number}")
    if issue.publication_details:
        bits.append(issue.publication_details)
    return " — ".join(str(bit).strip() for bit in bits if str(bit or "").strip())


def append_publication_detail(existing, addition):
    existing = str(existing or "").strip()
    addition = str(addition or "").strip()
    if not addition:
        return existing
    if not existing:
        return addition
    if addition.casefold() in existing.casefold():
        return existing
    return f"{existing}\n{addition}"


@staff_member_required
def convert_single_collection_article(request, pk):
    if request.method != "POST":
        return redirect("sources:collection_detail", pk=pk)
    container = get_object_or_404(Work, pk=pk)
    articles = list(Article.objects.select_related("work").filter(container_work=container))
    if len(articles) != 1:
        messages.error(request, f"Преобразование возможно только для сборника с одной статьей. Сейчас статей: {len(articles)}.")
        return redirect("sources:collection_detail", pk=pk)
    article = articles[0]
    work = article.work
    with transaction.atomic():
        ensure_book_for_work(work)
        merge_missing_work_fields(work, container)
        work.work_type = Work.WorkType.BOOK
        work.host_title = container.title
        work.article_pages = work.article_pages or article.pages
        work.save()
        article.delete()
    messages.success(request, f"Статья сборника преобразована в источник: {work.title}")
    return redirect(request.POST.get("next") or "sources:work_list")


def collection_queryset():
    return (
        Work.objects.annotate(article_count=Count("contained_articles", distinct=True))
        .filter(Q(article_count__gt=0) | Q(is_container=True) | Q(work_type=Work.WorkType.CONTAINER))
        .order_by("title", "inferred_year", "work_id")
    )


def merge_missing_work_fields(target, source):
    for field in [
        "subtitle",
        "responsibility_note",
        "publication_place",
        "publisher",
        "physical_description",
        "publication_details",
        "public_review",
    ]:
        if not getattr(target, field) and getattr(source, field):
            setattr(target, field, getattr(source, field))
    if not target.inferred_year and source.inferred_year:
        target.inferred_year = source.inferred_year
    if not target.source_section_id and source.source_section_id:
        target.source_section = source.source_section


MERGE_ISSUE_FIELDS = [
    "title",
    "parallel_title",
    "title_remainder",
    "responsibility_statement",
    "publication_date",
    "part_number",
    "gross_number",
    "date_text",
    "chronology",
    "enumeration",
    "publication_place",
    "publisher",
    "publication_details",
    "issn",
    "isbn",
    "notes",
]


def duplicate_issue_merge_plan():
    grouped = defaultdict(list)
    for issue in JournalIssue.objects.select_related("journal").annotate(article_count=Count("articles", distinct=True)):
        key = (
            issue.journal_id,
            str(issue.year or ""),
            clean_merge_value(issue.issue_number),
            clean_merge_value(issue.volume),
        )
        grouped[key].append(issue)

    safe_groups = []
    conflict_groups = []
    for key, issues in grouped.items():
        if len(issues) < 2:
            continue
        conflicts = issue_merge_conflicts(issues)
        row = {
            "key": key,
            "issues": sorted(issues, key=lambda issue: issue.journal_issue_id),
            "conflicts": conflicts,
        }
        if conflicts:
            conflict_groups.append(row)
        else:
            safe_groups.append(row)
    write_duplicate_issue_merge_report(safe_groups, conflict_groups)
    return {"safe_groups": safe_groups, "conflict_groups": conflict_groups}


def issue_merge_conflicts(issues):
    conflicts = {}
    for field in MERGE_ISSUE_FIELDS:
        values = {
            clean_merge_value(getattr(issue, field))
            for issue in issues
            if clean_merge_value(getattr(issue, field))
        }
        if len(values) > 1:
            conflicts[field] = " | ".join(sorted(values))
    return conflicts


def clean_merge_value(value):
    return str(value or "").strip()


def write_duplicate_issue_merge_report(safe_groups, conflict_groups):
    reports_dir = settings.PROJECT_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "merge_duplicate_journal_issues.tsv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = ["status", "key", "issue_ids", "article_counts", "conflicts"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for status, groups in [("safe", safe_groups), ("conflict", conflict_groups)]:
            for group in groups:
                issues = group["issues"]
                writer.writerow(
                    {
                        "status": status,
                        "key": " | ".join(group["key"]),
                        "issue_ids": "; ".join(issue.journal_issue_id for issue in issues),
                        "article_counts": "; ".join(str(issue.article_count) for issue in issues),
                        "conflicts": "; ".join(f"{key}: {value}" for key, value in group["conflicts"].items()),
                    }
                )
    return path


def run_duplicate_issue_merge(apply):
    plan = duplicate_issue_merge_plan()
    if not apply:
        return {
            "level": "warning",
            "message": (
                "Проверка дублей выпусков завершена. "
                f"Можно слить автоматически: {len(plan['safe_groups'])}; "
                f"требуют ручной проверки: {len(plan['conflict_groups'])}. "
                "Отчёт: reports/merge_duplicate_journal_issues.tsv"
            ),
        }

    if not plan["safe_groups"]:
        return {"level": "warning", "message": "Нет безопасных групп дублей выпусков для автоматического слияния."}

    backup = backup_sqlite_database("before-merge-duplicate-journal-issues")
    merged_groups = 0
    removed_issues = 0
    moved_articles = 0
    with transaction.atomic():
        for group in plan["safe_groups"]:
            target = sorted(
                group["issues"],
                key=lambda issue: (-issue.article_count, issue.journal_issue_id),
            )[0]
            sources = [issue for issue in group["issues"] if issue.journal_issue_id != target.journal_issue_id]
            for source in sources:
                moved_articles += Article.objects.filter(journal_issue=source).update(journal_issue=target)
                copy_missing_issue_fields(target, source)
            target.save()
            removed_issues += JournalIssue.objects.filter(journal_issue_id__in=[source.journal_issue_id for source in sources]).delete()[0]
            merged_groups += 1
    refresh_target_model()
    return {
        "level": "success",
        "message": (
            f"Слито групп выпусков: {merged_groups}; удалено дублей: {removed_issues}; "
            f"перенесено статей: {moved_articles}. Backup: {backup}. "
            f"Конфликтных групп не тронуто: {len(plan['conflict_groups'])}."
        ),
    }


def copy_missing_issue_fields(target, source):
    for field in MERGE_ISSUE_FIELDS:
        if not clean_merge_value(getattr(target, field)) and clean_merge_value(getattr(source, field)):
            setattr(target, field, getattr(source, field))


def empty_container_cleanup_plan():
    empty_issue_ids = list(
        JournalIssue.objects.annotate(article_count=Count("articles", distinct=True))
        .filter(article_count=0)
        .values_list("journal_issue_id", flat=True)
    )
    empty_journal_ids = list(
        Journal.objects.annotate(issue_count=Count("issues", distinct=True))
        .filter(issue_count=0)
        .values_list("journal_id", flat=True)
    )
    empty_collection_ids = list(
        Work.objects.annotate(article_count=Count("contained_articles", distinct=True))
        .filter(Q(is_container=True) | Q(work_type=Work.WorkType.CONTAINER), article_count=0)
        .values_list("work_id", flat=True)
    )
    return {
        "empty_issue_ids": empty_issue_ids,
        "empty_journal_ids": empty_journal_ids,
        "empty_collection_ids": empty_collection_ids,
    }


def run_empty_container_cleanup(apply):
    plan = empty_container_cleanup_plan()
    total = len(plan["empty_issue_ids"]) + len(plan["empty_journal_ids"]) + len(plan["empty_collection_ids"])
    if not apply:
        return {
            "level": "warning",
            "message": (
                "Проверка пустых контейнеров завершена. "
                f"Пустые выпуски: {len(plan['empty_issue_ids'])}; "
                f"пустые журналы: {len(plan['empty_journal_ids'])}; "
                f"пустые сборники: {len(plan['empty_collection_ids'])}."
            ),
        }
    if not total:
        return {"level": "warning", "message": "Пустых журналов, выпусков и сборников не найдено."}

    backup = backup_sqlite_database("before-cleanup-empty-containers")
    with transaction.atomic():
        deleted_issues = JournalIssue.objects.filter(journal_issue_id__in=plan["empty_issue_ids"]).delete()[0]
        journal_ids = set(plan["empty_journal_ids"])
        journal_ids.update(
            Journal.objects.annotate(issue_count=Count("issues", distinct=True))
            .filter(issue_count=0)
            .values_list("journal_id", flat=True)
        )
        deleted_journals = Journal.objects.filter(journal_id__in=journal_ids).delete()[0]
        deleted_collections = 0
        for work in Work.objects.filter(work_id__in=plan["empty_collection_ids"]):
            if not Article.objects.filter(container_work=work).exists():
                delete_work_and_generated_target(work)
                deleted_collections += 1
    refresh_target_model()
    return {
        "level": "success",
        "message": (
            f"Удалено пустых выпусков: {deleted_issues}; журналов: {deleted_journals}; "
            f"сборников: {deleted_collections}. Backup: {backup}."
        ),
    }


def unused_author_cleanup_plan():
    return {
        "unused_author_ids": list(
            Author.objects.annotate(work_count=Count("works", distinct=True))
            .filter(work_count=0)
            .values_list("author_id", flat=True)
        )
    }


def run_unused_author_cleanup(apply):
    plan = unused_author_cleanup_plan()
    if not apply:
        return {"level": "warning", "message": f"Авторов без привязанных записей: {len(plan['unused_author_ids'])}."}
    if not plan["unused_author_ids"]:
        return {"level": "warning", "message": "Авторов без привязанных записей не найдено."}
    backup = backup_sqlite_database("before-cleanup-unused-authors")
    deleted = Author.objects.filter(author_id__in=plan["unused_author_ids"]).delete()[0]
    refresh_target_model()
    return {"level": "success", "message": f"Удалено авторов без привязок: {deleted}. Backup: {backup}."}


def run_author_merge(target_author_id, source_author_ids_text, apply):
    source_ids = split_service_ids(source_author_ids_text)
    if apply:
        result = apply_author_merge(source_ids=source_ids, target_id=target_author_id)
        if result["error"]:
            return {"level": "error", "message": result["error"]}
        return {"level": "success", "message": result["message"]}

    plan = merge_authors_plan(source_ids=source_ids, target_id=target_author_id)
    if plan["errors"]:
        return {"level": "error", "message": " ".join(plan["errors"])}
    aliases_preview = "; ".join(plan["aliases"][:20])
    if len(plan["aliases"]) > 20:
        aliases_preview += f"; ... ещё {len(plan['aliases']) - 20}"
    return {
        "level": "warning",
        "message": (
            f"Проверка слияния авторов: целевой автор {plan['target'].author_id} — {plan['target'].display_name}; "
            f"дублей: {len(plan['sources'])}; связей WorkAuthor: {plan['work_link_count']}; "
            f"связей SourceAuthor: {plan['source_link_count']}; aliases после слияния: {aliases_preview or 'нет'}."
        ),
    }


def split_service_ids(value):
    return [item.strip() for item in re.split(r"[\s,;]+", str(value or "")) if item.strip()]


def run_redundant_field_cleanup(apply):
    plan = redundant_cleanup_plan()
    counts = {key: len(rows) for key, rows in plan.items()}
    total = sum(counts.values())
    if not apply:
        return {"level": "warning", "message": f"Проверка дублирующих полей завершена: {counts}."}
    if not total:
        return {"level": "warning", "message": "Дублирующих полей для очистки не найдено."}
    backup = backup_sqlite_database("before-service-cleanup-redundant-fields")
    with transaction.atomic():
        cleared = apply_redundant_cleanup(plan)
    refresh_target_model()
    return {"level": "success", "message": f"Очищено дублирующих полей: {cleared}. Backup: {backup}."}


RIGHT_PANEL_KINDS = {
    "journal_issue": "Выпуски журналов",
    "container_work": "Сборники",
    "author": "Авторы",
    "tag": "Тэги",
    "section": "Категории",
    "language": "Языки",
}

LEFT_PANEL_KINDS = {
    "work": "Издания",
    "journal": "Журналы",
    "container_work": "Сборники",
}


def relation_filters(request):
    return {
        "q": request.GET.get("q", "").strip(),
        "type": request.GET.get("type", "").strip(),
        "authors": request.GET.get("authors", "").strip(),
        "placement": request.GET.get("placement", "").strip(),
    }


def relation_left_queryset(kind, filters):
    if kind == "journal":
        journals = Journal.objects.annotate(
            issue_count=Count("issues", distinct=True),
            article_count=Count("issues__articles", distinct=True),
        ).order_by("title", "journal_id")
        return casefold_filter(journals, filters["q"], ["journal_id", "title"])

    if kind == "container_work":
        containers = (
            Work.objects.select_related("source_section", "language")
            .annotate(article_count=Count("contained_articles", distinct=True))
            .filter(Q(article_count__gt=0) | Q(is_container=True) | Q(work_type=Work.WorkType.CONTAINER))
            .order_by("title", "inferred_year", "work_id")
        )
        return casefold_filter(containers, filters["q"], ["work_id", "title", "publication_details"])

    return relation_work_queryset(filters)


def relation_work_queryset(filters):
    works = (
        Work.objects.select_related(
            "source_section",
            "language",
            "article__journal_issue",
            "article__journal_issue__journal",
            "article__container_work",
        )
        .prefetch_related("authors", "tags")
        .order_by("source_sequence", "source_number", "work_id")
    )
    if filters["type"] in {Work.WorkType.BOOK, Work.WorkType.CONTAINER, Work.WorkType.ARTICLE, Work.WorkType.UNKNOWN}:
        works = works.filter(work_type=filters["type"])

    if filters["authors"] == "with":
        works = works.filter(authors__isnull=False).distinct()
    elif filters["authors"] == "without":
        works = works.filter(authors__isnull=True)

    if filters["placement"] == "standalone":
        works = works.filter(article__isnull=True)
    elif filters["placement"] == "journal":
        works = works.filter(article__journal_issue__isnull=False)
    elif filters["placement"] == "container":
        works = works.filter(article__container_work__isnull=False)
    elif filters["placement"] == "unplaced_article":
        works = works.filter(
            article__isnull=False,
            article__journal_issue__isnull=True,
            article__container_work__isnull=True,
            article__collection__isnull=True,
        )

    return casefold_filter(
        works,
        filters["q"],
        [
            "work_id",
            "title",
            "subtitle",
            "raw_author_string",
            "publication_details",
            "article__journal_issue_id",
            "article__journal_issue__journal_id",
            "article__journal_issue__journal__title",
            "article__container_work_id",
            "article__container_work__title",
            lambda work: " ".join(author.display_name for author in work.authors.all()),
        ],
    )


def right_panel_queryset(kind, query):
    if kind == "journal_issue":
        queryset = JournalIssue.objects.select_related("journal").annotate(article_count=Count("articles", distinct=True)).order_by("journal__title", "year", "issue_number", "volume")
        return casefold_filter(queryset, query, ["journal_issue_id", "journal__title", "issue_number", "volume", "publication_details"])

    if kind == "container_work":
        queryset = Work.objects.annotate(article_count=Count("contained_articles", distinct=True)).filter(Q(article_count__gt=0) | Q(is_container=True) | Q(work_type=Work.WorkType.CONTAINER)).order_by("title", "inferred_year", "work_id")
        return casefold_filter(queryset, query, ["work_id", "title", "publication_details"])

    if kind == "author":
        queryset = Author.objects.annotate(work_count=Count("works", distinct=True)).order_by("sort_name", "display_name")
        return casefold_filter(queryset, query, ["author_id", "display_name", "sort_name", "aliases"])

    if kind == "tag":
        queryset = Tag.objects.annotate(work_count=Count("works", distinct=True)).order_by("sort_order", "title")
        return casefold_filter(queryset, query, ["tag_id", "title"])

    if kind == "section":
        queryset = Section.objects.annotate(work_count=Count("source_works", distinct=True)).order_by("sort_order", "source_code")
        return casefold_filter(queryset, query, ["section_id", "source_code", "title"])

    queryset = Language.objects.annotate(work_count=Count("works", distinct=True)).order_by("sort_order", "title")
    return casefold_filter(queryset, query, ["language_id", "code", "title"])


def apply_relation(work_ids, target_kind, target_id):
    works = list(Work.objects.filter(work_id__in=work_ids))
    if not works:
        return {"error": "Выбранные записи не найдены.", "message": ""}

    target = relation_target(target_kind, target_id)
    if target is None:
        return {"error": "Выбранный объект справа не найден.", "message": ""}

    with transaction.atomic():
        if target_kind == "journal_issue":
            for work in works:
                work.work_type = Work.WorkType.ARTICLE
                work.host_title = ""
                work.save(update_fields=["work_type", "host_title"])
                article = Article.objects.filter(work=work).first()
                if article is None:
                    article = Article(article_id=f"article-for-{work.work_id}", work=work)
                article.work = work
                article.journal_issue = target
                article.container_work = None
                article.collection = None
                article.pages = work.article_pages or article.pages
                article.save()
            return {"error": "", "message": f"Связано с выпуском журнала: {len(works)} записей."}

        if target_kind == "container_work":
            for work in works:
                if work == target:
                    continue
                work.work_type = Work.WorkType.ARTICLE
                work.host_title = ""
                work.save(update_fields=["work_type", "host_title"])
                article = Article.objects.filter(work=work).first()
                if article is None:
                    article = Article(article_id=f"article-for-{work.work_id}", work=work)
                article.work = work
                article.container_work = target
                article.journal_issue = None
                article.collection = None
                article.pages = work.article_pages or article.pages
                article.save()
            return {"error": "", "message": f"Связано со сборником: {len(works)} записей."}

        if target_kind == "author":
            created = 0
            for index, work in enumerate(works, start=1):
                _, is_created = WorkAuthor.objects.get_or_create(
                    work=work,
                    author=target,
                    role="",
                    defaults={"sort_order": index * 10, "source_text": target.display_name},
                )
                created += int(is_created)
            return {"error": "", "message": f"Автор привязан. Новых связей: {created}."}

        if target_kind == "tag":
            created = 0
            for index, work in enumerate(works, start=1):
                _, is_created = WorkTag.objects.get_or_create(
                    work=work,
                    tag=target,
                    defaults={"sort_order": index * 10, "source_text": target.title},
                )
                created += int(is_created)
            return {"error": "", "message": f"Тэг привязан. Новых связей: {created}."}

        if target_kind == "section":
            Work.objects.filter(work_id__in=[work.work_id for work in works]).update(source_section=target)
            return {"error": "", "message": f"Категория установлена: {len(works)} записей."}

        Work.objects.filter(work_id__in=[work.work_id for work in works]).update(language=target)
        return {"error": "", "message": f"Язык установлен: {len(works)} записей."}


def apply_relation_action(action, left_kind, selected_ids, target_kind, target_id, title):
    if action == "link":
        if left_kind != "work":
            return {"error": "Обычная установка связи доступна только для изданий слева.", "message": ""}
        if target_kind not in RIGHT_PANEL_KINDS:
            return {"error": "Неизвестный тип связи.", "message": ""}
        if not target_id:
            return {"error": "Выберите один объект справа.", "message": ""}
        refresh_target_after = target_kind in {"journal_issue", "container_work"}
        result = apply_relation(selected_ids, target_kind, target_id)
        if not result["error"] and refresh_target_after:
            refresh_target_model()
        return result

    if action == "works_to_journal":
        if left_kind != "work":
            return {"error": "Для этого действия выберите слева режим «Издания».", "message": ""}
        return convert_selected_works_to_journals(selected_ids)

    if action == "works_to_collection":
        if left_kind != "work":
            return {"error": "Для этого действия выберите слева режим «Издания».", "message": ""}
        return convert_selected_works_to_collections(selected_ids)

    if action == "works_to_book":
        if left_kind != "work":
            return {"error": "Для этого действия выберите слева режим «Издания».", "message": ""}
        return convert_selected_containers_to_books(selected_ids)

    if action == "detach_to_book":
        if left_kind != "work":
            return {"error": "Для этого действия выберите слева режим «Издания».", "message": ""}
        return detach_selected_articles_to_books(selected_ids)

    if action == "delete_works":
        if left_kind != "work":
            return {"error": "Удаление записей доступно только в режиме «Издания».", "message": ""}
        return delete_selected_works(selected_ids)

    if action == "journals_to_collection":
        if left_kind != "journal":
            return {"error": "Для этого действия выберите слева режим «Журналы».", "message": ""}
        return convert_selected_journals_to_collection(selected_ids, title)

    if action == "merge_collections":
        if left_kind != "container_work":
            return {"error": "Для этого действия выберите слева режим «Сборники».", "message": ""}
        if target_kind != "container_work" or not target_id:
            return {"error": "Справа выберите целевой сборник.", "message": ""}
        return merge_selected_collections(selected_ids, target_id)

    return {"error": "Неизвестное действие.", "message": ""}


def convert_selected_works_to_journals(work_ids):
    works = list(Work.objects.filter(work_id__in=work_ids).select_related("language", "source_section"))
    if len(works) != len(set(work_ids)):
        return {"error": "Одна или несколько выбранных записей не найдены.", "message": ""}
    if not works:
        return {"error": "Записи не выбраны.", "message": ""}

    backup = backup_sqlite_database("before-relation-works-to-journal")
    created = 0
    with transaction.atomic():
        for work in works:
            journal_id = f"journal-from-{work.work_id}"
            journal = Journal.objects.filter(title=work.title).first()
            journal_created = False
            if journal is None:
                journal, journal_created = Journal.objects.update_or_create(
                    journal_id=journal_id,
                    defaults={
                        "title": work.title,
                        "parallel_title": work.parallel_title,
                        "title_remainder": work.title_remainder or work.subtitle,
                        "responsibility_statement": work.responsibility_statement or work.responsibility_note,
                        "place": work.publication_place,
                        "publisher": work.publisher,
                        "issn": work.issn,
                        "start_year": work.inferred_year,
                        "end_year": work.inferred_year,
                        "description": append_note("", f"Created from {work.work_id}"),
                    },
                )
            JournalIssue.objects.update_or_create(
                journal_issue_id=f"journal-issue-from-{work.work_id}",
                defaults={
                    "journal": journal,
                    "title": work.title,
                    "parallel_title": work.parallel_title,
                    "title_remainder": work.title_remainder or work.subtitle,
                    "responsibility_statement": work.responsibility_statement or work.responsibility_note,
                    "year": work.inferred_year,
                    "publication_date": work.publication_date,
                    "issue_number": work.volume_number,
                    "part_number": work.part_number,
                    "publication_place": work.publication_place,
                    "publisher": work.publisher,
                    "publication_details": work.publication_details,
                    "issn": work.issn,
                    "isbn": work.isbn,
                    "notes": append_note(work.notes, f"Journal issue created from bibliographic source {work.work_id}"),
                },
            )
            work.description_status = Work.DescriptionStatus.NEEDS_REVIEW
            work.notes = append_note(work.notes, f"Journal structure created: {journal_id}")
            work.save(update_fields=["description_status", "notes"])
            created += int(journal_created)

    refresh_target_model()
    backup_note = f" Backup: {backup}" if backup else ""
    return {"error": "", "message": f"Создано/обновлено журналов: {len(works)}; новых: {created}.{backup_note}"}


def convert_selected_works_to_collections(work_ids):
    works = list(Work.objects.filter(work_id__in=work_ids))
    if len(works) != len(set(work_ids)):
        return {"error": "Одна или несколько выбранных записей не найдены.", "message": ""}
    if not works:
        return {"error": "Записи не выбраны.", "message": ""}
    blocked = [
        work.work_id
        for work in works
        if Article.objects.filter(work=work).filter(
            Q(container_work__isnull=False) | Q(collection__isnull=False) | Q(journal_issue__isnull=False)
        ).exists()
    ]
    if blocked:
        return {"error": "Нельзя сделать контейнерами записи, которые уже являются частью другой сущности: " + ", ".join(blocked), "message": ""}

    backup = backup_sqlite_database("before-relation-works-to-collection")
    with transaction.atomic():
        for work in works:
            work.work_type = Work.WorkType.CONTAINER
            work.is_container = True
            work.description_status = Work.DescriptionStatus.NEEDS_REVIEW
            work.notes = append_note(work.notes, "Marked as collection/container source.")
            work.save(update_fields=["work_type", "is_container", "description_status", "notes"])
            ensure_book_for_work(work)

    refresh_target_model()
    backup_note = f" Backup: {backup}" if backup else ""
    return {"error": "", "message": f"Сборниками помечено записей: {len(works)}.{backup_note}"}


def convert_selected_containers_to_books(work_ids):
    works = list(Work.objects.filter(work_id__in=work_ids))
    if len(works) != len(set(work_ids)):
        return {"error": "Одна или несколько выбранных записей не найдены.", "message": ""}
    if not works:
        return {"error": "Записи не выбраны.", "message": ""}
    blocked = [work.work_id for work in works if Article.objects.filter(container_work=work).exists()]
    if blocked:
        return {"error": "Нельзя преобразовать в книгу контейнеры со связанными записями: " + ", ".join(blocked), "message": ""}

    backup = backup_sqlite_database("before-relation-containers-to-book")
    with transaction.atomic():
        for work in works:
            work.work_type = Work.WorkType.BOOK
            work.is_container = False
            work.description_status = Work.DescriptionStatus.NEEDS_REVIEW
            work.notes = append_note(work.notes, "Converted from container to ordinary book.")
            work.save(update_fields=["work_type", "is_container", "description_status", "notes"])
            ensure_book_for_work(work)

    refresh_target_model()
    backup_note = f" Backup: {backup}" if backup else ""
    return {"error": "", "message": f"В обычные книги преобразовано записей: {len(works)}.{backup_note}"}


def detach_selected_articles_to_books(work_ids):
    works = list(Work.objects.filter(work_id__in=work_ids))
    if len(works) != len(set(work_ids)):
        return {"error": "Одна или несколько выбранных записей не найдены.", "message": ""}
    if not works:
        return {"error": "Записи не выбраны.", "message": ""}
    missing_article = [work.work_id for work in works if not Article.objects.filter(work=work).exists()]
    if missing_article:
        return {"error": "В обычную книгу можно преобразовать только записи-статьи: " + ", ".join(missing_article), "message": ""}
    blocked_containers = [work.work_id for work in works if Article.objects.filter(container_work=work).exists()]
    if blocked_containers:
        return {"error": "Нельзя отвязать записи, которые сами содержат связанные статьи: " + ", ".join(blocked_containers), "message": ""}

    backup = backup_sqlite_database("before-relation-detach-to-book")
    with transaction.atomic():
        for work in works:
            Article.objects.filter(work=work).delete()
            work.work_type = Work.WorkType.BOOK
            work.is_container = False
            work.host_title = ""
            work.description_status = Work.DescriptionStatus.NEEDS_REVIEW
            work.notes = append_note(work.notes, "Detached from container/journal and converted to ordinary book.")
            work.save(update_fields=["work_type", "is_container", "host_title", "description_status", "notes"])
            ensure_book_for_work(work)

    refresh_target_model()
    backup_note = f" Backup: {backup}" if backup else ""
    return {"error": "", "message": f"В обычные книги преобразовано записей: {len(works)}.{backup_note}"}


def delete_selected_works(work_ids):
    works = list(Work.objects.filter(work_id__in=work_ids))
    if len(works) != len(set(work_ids)):
        return {"error": "Одна или несколько выбранных записей не найдены.", "message": ""}
    if not works:
        return {"error": "Записи не выбраны.", "message": ""}
    blocked = [work.work_id for work in works if Article.objects.filter(container_work=work).exists()]
    if blocked:
        return {"error": "Нельзя удалить контейнеры со связанными записями: " + ", ".join(blocked), "message": ""}

    backup = backup_sqlite_database("before-relation-delete-works")
    with transaction.atomic():
        for work in works:
            delete_work_and_generated_target(work)

    refresh_target_model()
    backup_note = f" Backup: {backup}" if backup else ""
    return {"error": "", "message": f"Удалено записей: {len(works)}.{backup_note}"}


def convert_selected_journals_to_collection(journal_ids, title):
    journals = list(Journal.objects.filter(journal_id__in=journal_ids).prefetch_related("issues__articles__work"))
    if len(journals) != len(set(journal_ids)):
        return {"error": "Один или несколько выбранных журналов не найдены.", "message": ""}
    if not journals:
        return {"error": "Журналы не выбраны.", "message": ""}

    article_count = Article.objects.filter(journal_issue__journal__in=journals).count()
    if article_count == 0:
        return {"error": "В выбранных журналах нет статей.", "message": ""}

    journal_ids_before_delete = [journal.journal_id for journal in journals]
    container_work_id = f"work-container-{sorted(journal_ids_before_delete)[0]}"
    container_title = title or sorted(journals, key=lambda journal: journal.journal_id)[0].title
    sample_article = Article.objects.select_related("work").filter(journal_issue__journal__in=journals).first()
    issue_details = list(
        JournalIssue.objects.filter(journal__in=journals, publication_details__gt="")
        .order_by("journal_id", "year", "issue_number", "volume")
        .values_list("publication_details", flat=True)
    )
    year = (
        JournalIssue.objects.filter(journal__in=journals, year__isnull=False)
        .order_by("year")
        .values_list("year", flat=True)
        .first()
    )

    backup = backup_sqlite_database("before-relation-journals-to-collection")
    with transaction.atomic():
        container, _ = Work.objects.update_or_create(
            work_id=container_work_id,
            defaults={
                "source_sequence": None,
                "source_number": next_synthetic_source_number(container_work_id),
                "source_page_marker": "",
                "source_section": sample_article.work.source_section,
                "language": sample_article.work.language,
                "work_type": Work.WorkType.CONTAINER,
                "is_container": True,
                "raw_author_string": "",
                "title": container_title,
                "publication_date": str(year) if year else "",
                "inferred_year": year,
                "publication_details": "\n".join(issue_details),
                "description_status": Work.DescriptionStatus.NEEDS_REVIEW,
                "notes": "Created from relation editor journals: " + ", ".join(journal_ids_before_delete),
            },
        )
        ensure_book_for_work(container)
        moved = Article.objects.filter(journal_issue__journal__in=journals).update(journal_issue=None, container_work=container)
        JournalIssue.objects.filter(journal__in=journals).delete()
        Journal.objects.filter(journal_id__in=journal_ids_before_delete).delete()

    refresh_target_model()
    backup_note = f" Backup: {backup}" if backup else ""
    return {"error": "", "message": f"Создан/обновлен сборник {container_work_id}; перенесено статей: {moved}.{backup_note}"}


def merge_selected_collections(source_ids, target_id):
    if target_id in source_ids:
        return {"error": "Целевой сборник не должен быть выбран слева как исходный.", "message": ""}
    target = Work.objects.filter(work_id=target_id).first()
    sources = list(Work.objects.filter(work_id__in=source_ids))
    if target is None:
        return {"error": "Целевой сборник не найден.", "message": ""}
    if len(sources) != len(set(source_ids)):
        return {"error": "Один или несколько исходных сборников не найдены.", "message": ""}

    backup = backup_sqlite_database("before-relation-merge-collections")
    source_titles = [source.title for source in sources]
    with transaction.atomic():
        moved = Article.objects.filter(container_work__in=sources).update(container_work=target)
        for source in sources:
            if not source.contained_articles.exists():
                delete_work_and_generated_target(source)

    refresh_target_model()
    backup_note = f" Backup: {backup}" if backup else ""
    return {"error": "", "message": f"В сборник «{target.title}» перенесено статей: {moved}. Источники: {'; '.join(source_titles)}.{backup_note}"}


def delete_work_and_generated_target(work):
    Collection.objects.filter(parent_work=work).delete()
    source = Source.objects.filter(source_id=work.work_id).first()
    if source:
        source.described_issues.all().delete()
        source.delete()
    work.delete()


def create_work_duplicate(source):
    duplicate = Work.objects.create(
        work_id=next_work_id(),
        source_django_id=None,
        source_sequence=None,
        source_number=next_synthetic_source_number(""),
        source_page_marker=source.source_page_marker,
        source_section=source.source_section,
        language=source.language,
        work_type=Work.WorkType.BOOK,
        is_container=False,
        raw_author_string=source.raw_author_string,
        title=source.title,
        parallel_title=source.parallel_title,
        subtitle=source.subtitle,
        title_remainder=source.title_remainder,
        volume_number=source.volume_number,
        part_number=source.part_number,
        part_title=source.part_title,
        responsibility_note=source.responsibility_note,
        responsibility_statement=source.responsibility_statement,
        host_title="",
        edition_statement=source.edition_statement,
        additional_edition_statement=source.additional_edition_statement,
        publication_place=source.publication_place,
        publisher=source.publisher,
        publication_date=source.publication_date,
        manufacture_place=source.manufacture_place,
        manufacturer=source.manufacturer,
        manufacture_date=source.manufacture_date,
        copyright_date=source.copyright_date,
        physical_description=source.physical_description,
        extent=source.extent,
        illustrations=source.illustrations,
        dimensions=source.dimensions,
        accompanying_material=source.accompanying_material,
        circulation=source.circulation,
        article_pages=source.article_pages,
        page_start=source.page_start,
        page_end=source.page_end,
        publication_details=source.publication_details,
        series_statement=source.series_statement,
        notes=append_note(source.notes, f"Duplicated from {source.work_id}"),
        bibliography_note=source.bibliography_note,
        index_note=source.index_note,
        contents_note=source.contents_note,
        isbn=source.isbn,
        issn=source.issn,
        doi=source.doi,
        url=source.url,
        access_date=source.access_date,
        content_type=source.content_type,
        media_type=source.media_type,
        carrier_type=source.carrier_type,
        description_status=Work.DescriptionStatus.NEEDS_REVIEW,
        public_review=source.public_review,
        inferred_year=source.inferred_year,
    )
    ensure_book_for_work(duplicate)
    for relation in WorkAuthor.objects.filter(work=source).select_related("author"):
        WorkAuthor.objects.create(
            work=duplicate,
            author=relation.author,
            sort_order=relation.sort_order,
            role=relation.role,
            source_text=relation.source_text,
            name_as_printed=relation.name_as_printed,
            include_in_responsibility=relation.include_in_responsibility,
            is_primary_heading=relation.is_primary_heading,
        )
    for relation in WorkTag.objects.filter(work=source).select_related("tag"):
        WorkTag.objects.create(
            work=duplicate,
            tag=relation.tag,
            sort_order=relation.sort_order,
            source_text=relation.source_text,
        )
    for item in WorkGroupItem.objects.filter(work=source).select_related("group"):
        WorkGroupItem.objects.create(group=item.group, work=duplicate, sort_order=item.sort_order)
    return duplicate


def ensure_book_for_work(work):
    book = Book.objects.filter(work=work).first()
    if book:
        return book
    book_id = f"book-from-{work.work_id}"
    if Book.objects.filter(book_id=book_id).exists():
        book_id = next_id(Book, "book_id", "book")
    return Book.objects.create(book_id=book_id, work=work)


def append_note(existing, note):
    return f"{existing}\n{note}".strip() if existing else note


def next_synthetic_source_number(existing_work_id):
    existing = Work.objects.filter(work_id=existing_work_id).values_list("source_number", flat=True).first()
    if existing:
        return existing
    current = Work.objects.filter(source_number__gte=900000000).aggregate(max_number=Max("source_number"))["max_number"]
    return (current or 900000000) + 1


def next_work_id():
    number = Work.objects.count() + 1
    while True:
        candidate = f"work-{number:06d}"
        if not Work.objects.filter(work_id=candidate).exists():
            return candidate
        number += 1


def next_id(model, field_name, prefix):
    last = 0
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    for value in model.objects.values_list(field_name, flat=True):
        match = pattern.match(str(value))
        if match:
            last = max(last, int(match.group(1)))
    return f"{prefix}-{last + 1:06d}"


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


def refresh_target_model():
    call_command("convert_legacy_to_target", "--apply", "--reset", verbosity=0)


def relation_target(kind, target_id):
    model_by_kind = {
        "journal_issue": JournalIssue,
        "container_work": Work,
        "author": Author,
        "tag": Tag,
        "section": Section,
        "language": Language,
    }
    model = model_by_kind.get(kind)
    if model is None:
        return None
    return model.objects.filter(pk=target_id).first()
