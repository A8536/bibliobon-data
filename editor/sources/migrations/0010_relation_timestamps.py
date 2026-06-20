# Generated for Bibliobon relation audit fields on 2026-06-06

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sources", "0009_source_provenance_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="articleplacement",
            name="created_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="articleplacement",
            name="updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sourceauthor",
            name="created_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sourceauthor",
            name="updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sourcegroupitem",
            name="created_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sourcegroupitem",
            name="updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sourcetag",
            name="created_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sourcetag",
            name="updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
