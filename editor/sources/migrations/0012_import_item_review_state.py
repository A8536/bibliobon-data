# Generated for Bibliobon import workflow review-state update on 2026-06-15

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sources", "0011_import_workflow"),
    ]

    operations = [
        migrations.AlterField(
            model_name="importitem",
            name="status",
            field=models.CharField(
                choices=[
                    ("parsed", "Parsed"),
                    ("needs_review", "Needs review"),
                    ("found_existing_no_changes", "Found existing, no changes"),
                    ("found_existing_with_differences", "Found existing with differences"),
                    ("structural_conflict", "Structural conflict"),
                    ("ready", "Ready"),
                    ("applied", "Applied"),
                    ("rejected", "Rejected"),
                    ("postponed", "Postponed"),
                    ("error", "Error"),
                ],
                db_index=True,
                default="parsed",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="importitem",
            name="matched_existing_type",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="importitem",
            name="matched_existing_id",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="importitem",
            name="comparison_json",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
