# Bibliobon Data Editor

Separate local Django editor for the Bibliobon canonical data workspace.

This app is intentionally independent from:

```text
/Users/oleg/Projects/websites/bibliobon-catalog
```

It uses its own SQLite database:

```text
data/editor.sqlite
```

## Bootstrap

From the project root:

```bash
python3 editor/manage.py migrate
python3 editor/manage.py import_bootstrap_jsonl
```

The import command reads the generated JSONL files from `data/`.

## Local Login

A local superuser was created for immediate use:

```text
username: editor
password: editor
```

This account is stored only in `data/editor.sqlite`.

## Run

```bash
python3 editor/manage.py runserver 127.0.0.1:8010
```

Open:

```text
http://127.0.0.1:8010/
```

## Current Scope

Implemented:

- list and search all works;
- show legacy and target relation summaries under each work row;
- two-panel relation editor for works, journals, collection containers, and target entities;
- relation-editor actions for converting/merging journals into collections and merging collections;
- list sections/categories;
- two-panel journal/collection page, with linked records shown next to the selected container;
- inspect journal issues and articles;
- inspect collection/container articles;
- convert a journal with exactly one article into one source;
- convert a collection/container work with exactly one article into one source;
- Django admin for direct editorial changes.
- Google Sheets export/import commands for editor data;
- Google Sheets sync page for export, dry-run import, and real import with backup;
- unused-data cleanup command with dry-run by default.
- target `Source`/`Periodical`/`Issue`/`ArticlePlacement` tables and first-pass legacy conversion.

The relation editor is available at:

```text
http://127.0.0.1:8010/relations/
```

Current target modes:

- journal issues;
- collection/container works;
- authors;
- tags;
- sections;
- languages.

Still to add:

- validation report pages;
- export to `data/bibliobon.sqlite`;
- `data/site_contract.json` generation.

## Google Sheets

Web page:

```text
http://127.0.0.1:8001/google-sheets/
```

Set credentials via environment variables or command options:

```bash
export GOOGLE_SHEETS_SPREADSHEET_ID="..."
export GOOGLE_SHEETS_CREDENTIALS="/path/to/service-account.json"
```

Export:

```bash
python3 editor/manage.py export_google_sheet
```

Dry-run import:

```bash
python3 editor/manage.py import_google_sheet --dry-run
```

Apply import:

```bash
python3 editor/manage.py import_google_sheet
```

The import creates a backup of `data/editor.sqlite` before applying changes.
Relation sheets (`WorkAuthors`, `WorkTags`, `WorkGroupItems`) are treated as
replace-all lists: removing a row from a relation sheet removes that relation.

The exporter creates missing sheets and expands existing sheet grids before
writing, so large tabs such as `Works` and `ArticlePlacements` can be rewritten
from scratch.

Active bibliography-editing sheets:

- `Works`;
- `Authors`;
- `WorkAuthors`;
- `Journals`;
- `JournalIssues`;
- `ArticlePlacements`;
- `ContainerWorks`;
- `WorkGroups`;
- `WorkGroupItems`.

Reference sheets for languages, tags, and sections are intentionally excluded
from the active workflow for now.

## Cleanup

Dry-run cleanup report:

```bash
python3 editor/manage.py cleanup_unused
```

Apply cleanup:

```bash
python3 editor/manage.py cleanup_unused --apply
```

The apply mode creates a backup before deleting unused rows.

Redundant field cleanup:

```bash
python3 editor/manage.py cleanup_redundant_fields
python3 editor/manage.py cleanup_redundant_fields --apply
```

This cleanup is for mechanical duplicates only. Apply mode backs up SQLite and
refreshes the target model.
Run this command after real Google Sheets imports when table edits may have
reintroduced redundant legacy fields.

## Target Conversion

Report-only conversion preview:

```bash
python3 editor/manage.py convert_legacy_to_target
```

Apply conversion to target tables:

```bash
python3 editor/manage.py convert_legacy_to_target --apply --reset
```

Apply mode creates a backup before rewriting target rows. The latest report is
written to `reports/target_conversion_report.json`.

## Journal To Collection Corrections

Dry-run:

```bash
python3 editor/manage.py convert_journal_to_collection --journal-id journal-002687
```

Apply:

```bash
python3 editor/manage.py convert_journal_to_collection --journal-id journal-002687 --apply
```

Merge several mistaken journals into one collection:

```bash
python3 editor/manage.py convert_journal_to_collection \
  --journal-id journal-002668 \
  --journal-id journal-002733 \
  --title "Историческая энциклопедия Сибири: в 3 т. / гл. ред. В.А. Ламин" \
  --apply
```

Apply mode creates a backup, moves articles from journal issues to the new
collection container work, removes the emptied journal/issue rows, and refreshes
the target model.

The same journal-to-collection correction and collection merge workflows are
also available from `/relations/`:

- choose `Журналы` in the left panel to convert one journal or merge selected
  journals into one collection;
- choose `Сборники` in the left panel and `Сборники` on the right to merge
  selected source collections into the selected target collection.
