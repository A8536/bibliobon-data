# Generated for Bibliobon field contract alignment on 2026-06-06

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sources", "0008_add_work_page_bounds"),
    ]

    operations = [
        migrations.AddField(
            model_name="source",
            name="data_source",
            field=models.CharField(blank=True, default="editor", max_length=128),
        ),
        migrations.AddField(
            model_name="source",
            name="first_seen_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="source",
            name="updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
