# Bibliobon Data

Canonical data workspace for the Bibliobon bibliography catalog.

The goal is to separate editorial data work from the Django publishing site:

```text
Google Sheets / manual TSV / scripts
        ↓
bibliobon-data validation + normalization
        ↓
data/bibliobon.sqlite + reports + site_contract.json
        ↓
bibliobon-catalog Django import
```

## Related Projects

- Site: `/Users/oleg/Projects/websites/bibliobon-catalog`
- Production site: `https://biblio.bonistika.info/`
- Current working Google Sheets document: `1zYFOqM-wBT6mFYLmblaGRBlV0rJeWAaLrEsCMJYKHTw`

## Directory Layout

- `editor/` - separate local Django editor for the canonical data workspace.
- `source/` - editable source snapshots and raw input exports for this data project.
- `data/` - generated machine-readable data, including the future `bibliobon.sqlite`.
- `data/manual/` - manual overrides, merge rules, controlled vocabularies, and curated corrections.
- `reports/` - generated diagnostics for review.
- `scripts/` - repeatable import, validation, normalization, and export commands.
- `docs/` - project documentation.
- `docs/reference/current-site/` - copied reference docs from the current Django site.

## Starting Point

The current data model already exists in the Django project. This project should first capture that state, assign stable data-project identifiers, then export a site-ready SQLite database under an explicit contract.

Do not treat `django_id` as the future canonical identifier. Preserve it as a compatibility/reference field during migration, but introduce stable IDs such as `work_id`, `author_id`, `journal_id`, `issue_id`, `tag_id`, and `group_id`.

## Bootstrap Command

```bash
python3 scripts/bootstrap_from_site_db.py \
  --source /Users/oleg/Projects/websites/bibliobon-catalog/app/db.sqlite3
```

The bootstrap command reads the current Django SQLite database and writes:

- JSONL snapshots under `data/`;
- `data/build_manifest.json`;
- diagnostics under `reports/`.

## Editor Site

The project now includes a separate local editor site:

```bash
python3 editor/manage.py runserver 127.0.0.1:8010
```

It uses `data/editor.sqlite` and is independent from the public Django site.
See `editor/README.md`.

Current export command:

```bash
python3 editor/manage.py export_site_artifact
```

The Django site should later import from:

```text
/Users/oleg/Projects/data/bibliobon-data/data/bibliobon.sqlite
```

The export command also writes:

```text
/Users/oleg/Projects/data/bibliobon-data/data/site_contract.json
/Users/oleg/Projects/data/bibliobon-data/data/build_manifest.json
```
