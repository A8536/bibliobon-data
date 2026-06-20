# Agent Instructions

## Project Purpose

- This project is the canonical data workspace for the Bibliobon bibliography database.
- The Django site at `/Users/oleg/Projects/websites/bibliobon-catalog` should become a consumer of exported data from this project.
- Work here should focus on data structure, quality, validation, diagnostics, and repeatable exports.
- Do not change the public site design from this project.
- Do not deploy from this project.

## Project Memory

- Keep `TODO.md` as the current task backlog.
- Keep `CONTEXT.md` as the current data-model and workflow context.
- Keep `DATA_CHANGELOG.md` as the durable log of data-contract changes for the site.
- Keep `reports/` for generated diagnostics.
- Keep `docs/reference/current-site/` as a read-only snapshot of useful docs copied from the current Django project.
- Do not log every tiny edit. Log durable context that helps continue the project on another computer or in another session.

## Data Safety

- The current source of truth at project start is the existing Django database and the working Google Sheets document, not the original Excel file.
- Import from Excel is legacy and should not be used as the active workflow.
- Before any mass rewrite of a SQLite database, create a backup.
- If working with Google Sheets, preserve manual edits and prefer dry-run/diagnostic passes before writes.
- Never overwrite the Django site database without an explicit backup and explicit user approval.

## Local Checks

- Prefer scripts that are repeatable and produce reports.
- Add validation before importing generated data into the Django site.
- When changing the Django site importer later, run:

```bash
cd /Users/oleg/Projects/websites/bibliobon-catalog/app
../.venv/bin/python manage.py check
```

- Run focused tests when import/export, parsing, or filtering logic changes.
