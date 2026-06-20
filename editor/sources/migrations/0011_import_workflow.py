# Generated for Bibliobon import workflow MVP on 2026-06-15

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sources", "0010_relation_timestamps"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ImportBatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=512)),
                ("source_name", models.CharField(blank=True, max_length=512)),
                ("source_type", models.CharField(choices=[("plain_text", "Plain text"), ("file", "File"), ("csv", "CSV"), ("json", "JSON"), ("manual", "Manual")], default="plain_text", max_length=32)),
                ("raw_input", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("parsed", "Parsed"), ("review_required", "Review required"), ("ready_to_apply", "Ready to apply"), ("applied", "Applied"), ("cancelled", "Cancelled")], db_index=True, default="draft", max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("parsed_at", models.DateTimeField(blank=True, null=True)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                ("notes", models.TextField(blank=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="bibliography_imports", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.CreateModel(
            name="ImportItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("raw_text", models.TextField()),
                ("detected_type", models.CharField(choices=[("book", "Book"), ("journal_article", "Journal article"), ("newspaper_article", "Newspaper article"), ("collection_article", "Collection article"), ("journal", "Journal"), ("journal_issue", "Journal issue"), ("collection", "Collection"), ("volume", "Volume"), ("author", "Author"), ("unknown", "Unknown")], db_index=True, default="unknown", max_length=32)),
                ("status", models.CharField(choices=[("parsed", "Parsed"), ("needs_review", "Needs review"), ("ready", "Ready"), ("applied", "Applied"), ("rejected", "Rejected"), ("postponed", "Postponed"), ("error", "Error")], db_index=True, default="parsed", max_length=32)),
                ("confidence", models.FloatField(default=0)),
                ("parsed_data_json", models.JSONField(blank=True, default=dict)),
                ("normalized_data_json", models.JSONField(blank=True, default=dict)),
                ("errors_json", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("import_batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="sources.importbatch")),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.CreateModel(
            name="ImportEntity",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("entity_type", models.CharField(choices=[("author", "Author"), ("book", "Book"), ("article", "Article"), ("journal", "Journal"), ("journal_issue", "Journal issue"), ("collection", "Collection"), ("collection_volume", "Collection volume"), ("publisher", "Publisher"), ("theme", "Theme"), ("section", "Section")], db_index=True, max_length=32)),
                ("label", models.CharField(max_length=1024)),
                ("normalized_key", models.CharField(db_index=True, max_length=1024)),
                ("data_json", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("unresolved", "Unresolved"), ("will_create", "Will create"), ("linked_existing", "Linked existing"), ("will_update_existing", "Will update existing"), ("ignored", "Ignored"), ("applied", "Applied"), ("error", "Error")], db_index=True, default="unresolved", max_length=32)),
                ("confidence", models.FloatField(default=0)),
                ("matched_existing_type", models.CharField(blank=True, max_length=64)),
                ("matched_existing_id", models.CharField(blank=True, max_length=128)),
                ("created_entity_type", models.CharField(blank=True, max_length=64)),
                ("created_entity_id", models.CharField(blank=True, max_length=128)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("import_batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="entities", to="sources.importbatch")),
            ],
            options={"ordering": ["entity_type", "label", "id"]},
        ),
        migrations.CreateModel(
            name="ImportGroup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("group_type", models.CharField(choices=[("journal_issue_group", "Journal issue group"), ("collection_volume_group", "Collection volume group"), ("author_group", "Author group"), ("standalone_books", "Standalone books"), ("unresolved", "Unresolved")], db_index=True, max_length=64)),
                ("label", models.CharField(max_length=1024)),
                ("status", models.CharField(choices=[("needs_review", "Needs review"), ("partially_ready", "Partially ready"), ("ready", "Ready"), ("applied", "Applied"), ("postponed", "Postponed"), ("error", "Error")], db_index=True, default="needs_review", max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("import_batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="groups", to="sources.importbatch")),
                ("root_entity", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="root_import_groups", to="sources.importentity")),
            ],
            options={"ordering": ["group_type", "label", "id"]},
        ),
        migrations.CreateModel(
            name="ImportEntityRelation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("relation_type", models.CharField(db_index=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("child_entity", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="parent_relations", to="sources.importentity")),
                ("import_batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="entity_relations", to="sources.importbatch")),
                ("parent_entity", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="child_relations", to="sources.importentity")),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.CreateModel(
            name="ImportDecision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("decision_type", models.CharField(choices=[("create", "Create"), ("link_existing", "Link existing"), ("update_existing", "Update existing"), ("skip", "Skip"), ("reject", "Reject"), ("postpone", "Postpone"), ("split_group", "Split group"), ("move_to_group", "Move to group")], db_index=True, max_length=32)),
                ("target_type", models.CharField(blank=True, max_length=64)),
                ("target_id", models.CharField(blank=True, max_length=128)),
                ("payload_json", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="bibliography_import_decisions", to=settings.AUTH_USER_MODEL)),
                ("entity", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="decisions", to="sources.importentity")),
                ("group", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="decisions", to="sources.importgroup")),
                ("import_batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="decisions", to="sources.importbatch")),
                ("item", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="decisions", to="sources.importitem")),
            ],
            options={"ordering": ["-updated_at", "-id"]},
        ),
        migrations.CreateModel(
            name="ImportMatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("existing_type", models.CharField(max_length=64)),
                ("existing_id", models.CharField(max_length=128)),
                ("score", models.FloatField(db_index=True, default=0)),
                ("match_reason_json", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("entity", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="matches", to="sources.importentity")),
                ("import_batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="matches", to="sources.importbatch")),
            ],
            options={"ordering": ["-score", "existing_type", "existing_id"]},
        ),
        migrations.CreateModel(
            name="ImportApplyLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("applied_at", models.DateTimeField(auto_now_add=True)),
                ("summary_json", models.JSONField(blank=True, default=dict)),
                ("created_entities_json", models.JSONField(blank=True, default=list)),
                ("updated_entities_json", models.JSONField(blank=True, default=list)),
                ("created_relations_json", models.JSONField(blank=True, default=list)),
                ("rejected_items_json", models.JSONField(blank=True, default=list)),
                ("decisions_json", models.JSONField(blank=True, default=list)),
                ("raw_input", models.TextField(blank=True)),
                ("applied_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="bibliography_import_apply_logs", to=settings.AUTH_USER_MODEL)),
                ("import_batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="apply_logs", to="sources.importbatch")),
            ],
            options={"ordering": ["-applied_at", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="importentity",
            constraint=models.UniqueConstraint(fields=("import_batch", "entity_type", "normalized_key"), name="sources_import_entity_unique_key"),
        ),
        migrations.AddConstraint(
            model_name="importentityrelation",
            constraint=models.UniqueConstraint(fields=("import_batch", "parent_entity", "child_entity", "relation_type"), name="sources_import_relation_unique"),
        ),
    ]
