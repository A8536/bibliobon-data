# Scripts

Planned repeatable commands:

- `bootstrap_from_site_db.py` - seed this project from the current Django SQLite database.
- `import_google_sheet.py` - import editorial sheet data into this project.
- `validate_data.py` - produce validation errors and review reports.
- `export_site_sqlite.py` - write `data/bibliobon.sqlite`.
- `export_build_manifest.py` - summarize the latest data build.
- `parse_bibliography.py` - create staging parser runs from raw `.txt` or
  `.jsonl` bibliography records without touching `data/editor.sqlite`.

Prefer commands that can run safely in dry-run mode before modifying data.

## Bibliography Parser

Create a staging parser run:

```bash
python3 scripts/parse_bibliography.py run \
  --input source/incoming/<batch>/extracted/records.txt \
  --batch <batch> \
  --write-normalized
```

The command writes `data/parser_runs/<run_id>/` with raw records, parse
candidates, warnings, and a local `parser.sqlite`. It does not compare with or
write to the editor database.

Compare a parser run with the editor database:

```bash
python3 scripts/parse_bibliography.py compare \
  --run-dir data/parser_runs/<run_id> \
  --editor-db data/editor.sqlite
```

The compare step opens the editor database read-only and writes review reports
beside the parser run: `match_candidates.tsv`, `proposed_changes.tsv`,
`conflicts.tsv`, `new_records.tsv`, and `merge_plan.json`.
