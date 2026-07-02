# Data Changelog

## 2026-06-27.04

Started the PSZRI law verification workflow design.

Workflow decisions:

- new law records verified by Gemini with Google Search grounding will first be
  written to staging files, not directly to `data/editor.sqlite`;
- input batches should live under
  `source/incoming/law_verification/<batch>/original/laws.txt`;
- verification outputs should live under `data/verification_runs/<run_id>/`;
- Gemini output should include both ready citation strings and structured
  fields for later canonical `Source` import;
- grounding chunks from the Gemini response must be extracted into reviewable
  output so editors can see which sites supported the verification.

Added:

- local ignored secret placeholder `secrets/gemini_api_key.env`;
- `source/incoming/law_verification/README.md`;
- `data/verification_runs/README.md`;
- `scripts/verify_laws_with_gemini.py`, a staging-only CLI that reads
  `.txt`/`.csv`/`.jsonl`, supports mock runs, calls Gemini with Google Search
  grounding, and writes JSONL/CSV/TSV/manifest outputs under
  `data/verification_runs/<run_id>/`;
- explicit staging fields for optional `Собрание узаконений` references such as
  `СУ-1875, № 51, от 20 июня, ст. 581`;
- a single-file `--input` workflow: one run reads one `.txt`/`.csv`/`.jsonl`
  file that can contain many law records;
- prompt version `pszri-law-verification-0.2`, with strict `СЗ`/`СУ`
  expansion and citation hierarchy rules for periodic legal registries;
- prompt/config version `pszri-law-verification-0.3`, removing
  `response_mime_type="application/json"` because Gemini Google Search
  grounding currently rejects tool use combined with JSON response MIME type;
- fatal API errors such as unsupported API location now stop the run after the
  first failed record and mark the remaining records as skipped;
- added `notebooks/pszri_law_verification_colab.ipynb`, a Google Colab version
  of the PSZRI law verification workflow with package installation, API key
  input, one-file upload, Gemini processing, and zipped result download.

## 2026-06-28.01

Reviewed and corrected `notebooks/book_colab.ipynb`, the Colab workflow for
ordinary book bibliography verification.

Fixes:

- removed leftover PSZRI/law wording from the book prompt;
- renamed output artifacts from `verified_laws.*` to `verified_books.*`;
- changed generated record ids from `law-*` to `book-*`;
- added `MAX_ATTEMPTS = 3` and retry logic so API/model/JSON parsing failures
  are retried twice before a record is marked `error`;
- added `attempt_count` and `attempt_errors` to JSONL/CSV outputs;
- cleared stale notebook outputs from a previous mixed law/book run.

Validation:

- notebook JSON parses successfully;
- code cells compile after ignoring Colab shell magic;
- a local smoke test with a fake Gemini response created
  `verified_books.jsonl`, `verified_books.csv`, `grounding_sources.tsv`, and
  `run_manifest.json` with the expected columns.

## 2026-06-28.02

Updated `notebooks/book_colab.ipynb` table output to match editor database
field names.

Changes:

- changed review/import table output from `verified_books.csv` to
  tab-delimited `verified_books.tsv`;
- mapped Gemini book fields to database-facing names:
  `author`, `title`, `title_remainder`, `publication_place`,
  `publication_date`, `inferred_year`, `extent`, `isbn`,
  `citation_gost_2018_full`, and `citation_gost_2003_short`;
- converted `publication_year` to `inferred_year` only when it is exactly a
  four-digit year;
- converted numeric `total_pages` to `extent` as `N с.`;
- added TSV service columns `work_id`, `source_number`, `row_type`, and
  `editor_note`;
- removed technical columns `record_id`, `source_file`, `source_line`, and
  `grounding_uris` from the TSV output;
- changed `grounding_sources.tsv` to link by `source_number` instead of
  internal record id.

Validation:

- notebook JSON parses successfully;
- code cells compile after ignoring Colab shell magic;
- a local smoke test confirmed the TSV columns and field conversions.

## 2026-07-01.01

Moved the ordinary-book Colab workflow to a text-first editing process.

Added:

- `notebooks/book_colab.py` as the source-of-truth percent-cell notebook;
- `scripts/percent_py_to_ipynb.py` to regenerate a runnable `.ipynb` from the
  text `.py` source;
- `notebooks/README.md` documenting the edit/regenerate workflow.

Workflow:

```bash
python3 scripts/percent_py_to_ipynb.py \
  notebooks/book_colab.py \
  notebooks/book_colab.ipynb
```

Validation:

- fetched `origin/main`; the remote tree currently does not contain
  `notebooks/book_colab.ipynb`, so there was no GitHub notebook version to
  compare in this local remote;
- `notebooks/book_colab.py` and `scripts/percent_py_to_ipynb.py` compile;
- regenerated `notebooks/book_colab.ipynb` from the `.py` source;
- smoke-tested the generated TSV output shape.

## 2026-07-01.02

Reviewed uploaded `notebooks/book_colab-2.py` against the text-first
`notebooks/book_colab.py` workflow and ported the useful changes.

Accepted changes:

- Gemini now returns a root JSON array of book objects, even for a single book;
- multivolume input rows can split into several independent TSV rows, one per
  confirmed volume;
- prompt now includes explicit rules for "склеенные" multivolume records;
- model field `author_last_name` was replaced by `raw_author_string`, which
  still maps to TSV/database-facing `author`;
- `verified_books.tsv` is flattened from one input row to one or more import
  rows;
- `grounding_sources.tsv` includes `volume_context` so sources can be reviewed
  against the generated volume rows.

Rejected cleanup:

- did not keep `book_colab-2.py` because it was a Colab export with `!pip`
  syntax and a broken indentation block, not a valid source `.py` file.

Validation:

- `notebooks/book_colab.py` compiles;
- regenerated `notebooks/book_colab.ipynb`;
- smoke-tested a two-volume fake Gemini response and confirmed two TSV import
  rows plus `volume_context` in `grounding_sources.tsv`.

## 2026-07-02.01

Added checkpoint/resume support to the ordinary-book Colab workflow.

Behavior:

- `notebooks/book_colab.py` mounts Google Drive by default and writes
  checkpoints under
  `MyDrive/bibliobon_colab_checkpoints/<input-file-stem>/`;
- current JSONL/TSV/grounding/manifest outputs and zip archive are saved after
  each processed record by default;
- if a Colab session disconnects, rerunning the notebook with the same input
  file loads saved `ok` rows and continues from the remaining rows;
- manifest now includes checkpoint metadata and `run_status`.

Validation:

- regenerated `notebooks/book_colab.ipynb`;
- smoke-tested a simulated disconnect after the first row and confirmed the
  second run resumed from the checkpoint.

## 2026-07-02.02

Added Google Drive API key loading to the ordinary-book Colab workflow.

Behavior:

- `notebooks/book_colab.py` first checks environment variable
  `GEMINI_API_KEY`;
- if missing, it mounts Google Drive and reads
  `MyDrive/bibliobon_colab_secrets/gemini_api_key.env`;
- the key file may contain either `GEMINI_API_KEY=...` or just the raw key;
- if the file is missing, the notebook falls back to the hidden manual prompt.

## 2026-07-02.03

Reworked `notebooks/book_colab.py` from a book-only verifier into a
single-cell mixed bibliography verifier.

Changes:

- replaced the book-only prompt with a universal prompt for monographs,
  multivolume parts, journal articles, collection articles, book chapters,
  conference materials, newspaper articles, electronic resources, and unknown
  records;
- Gemini now returns a root JSON array of importable objects with
  `record_type`, `source`, and optional `host`;
- source fields use database-facing names such as `title`, `title_remainder`,
  `publication_place`, `publication_date`, `extent`,
  `citation_gost_2018_full`, and `citation_gost_2003_short`;
- article/chapter outputs include article fields plus `host_*` columns for the
  publication source;
- output files were renamed to `verified_bibliography.jsonl` and
  `verified_bibliography.tsv`;
- the generated Colab notebook now contains one code cell instead of separate
  setup/work cells.

Validation:

- regenerated `notebooks/book_colab.ipynb`;
- `notebooks/book_colab.py` compiles;
- smoke-tested fake monograph and journal-article responses and confirmed the
  mixed TSV output shape.

No database schema, editor database, public-site code, or site export contract
was changed.

## 2026-06-27.03

Added the first source-first citation review queue.

Editor UI:

- added staff-only page `/sources/citations/`;
- default queue shows `Source` records missing at least one stored citation
  text field;
- added filters for query, source type, record subtype, citation status, and
  missing citation variant;
- rows show filled/empty badges for `citation_gost_2018_full`,
  `citation_gost_2003_short`, `citation_host_full`, and
  `citation_host_short`;
- rows link to the single-source citation editor and, when available, the
  linked legacy work inspect page;
- added queue links from the shared editor navigation, work list page, and
  single-source citation editor.

No schema changes, citation generation, or public-site changes were made.

## 2026-06-27.02

Added the first source-first editor UI for stored citation variants.

Editor UI:

- added staff-only page `/sources/<source_id>/citations/`;
- page shows `Source` identity, linked legacy `Work`, citation-oriented
  structural context, current helper previews, and a form for the six stored
  citation fields;
- saving updates only `Source.citation_gost_2018_full`,
  `Source.citation_gost_2003_short`, `Source.citation_host_full`,
  `Source.citation_host_short`, `Source.citation_status`, and
  `Source.citation_note`;
- linked existing work inspect/list pages to the new page as `Библиография`.

No public-site code or citation generation was changed.

## 2026-06-27.01

Implemented the first source-first stored citation block.

Model changes:

- added citation fields to canonical `Source` only:
  `citation_gost_2018_full`, `citation_gost_2003_short`,
  `citation_host_full`, `citation_host_short`, `citation_status`,
  `citation_note`;
- did not add these fields to legacy `Work`;
- existing rows migrate with blank citation strings and blank citation status.

Helper/API changes:

- added `editor/sources/citations.py` with `standalone_citation()`,
  `host_citation()`, and `citation_status_label()`;
- helpers prefer reviewed stored citation strings and fall back conservatively
  to existing raw/title text without generating ГОСТ records.

Export contract:

- bumped `site_contract.json` to contract version `0.6.0` and artifact schema
  version `6`;
- `sources` now exports all six `citation_*` fields;
- structured fields remain canonical for search, matching, relations, imports,
  diagnostics, and validation;
- stored citation fields are preferred public display strings when filled;
- `citation_host_full` and `citation_host_short` are for using a `Source` as a
  parent/container reference after `//` in article citations.

## 2026-05-27.1

Added first staging-only bibliography parser iteration:

- `scripts/parse_bibliography.py`;
- `source/incoming/README.md`;
- `source/normalized_text/README.md`;
- `data/parser_runs/README.md`.

The parser reads `.txt` or `.jsonl` raw bibliography records and writes
staging artifacts under `data/parser_runs/<run_id>/`:

- `run_manifest.json`;
- `raw_records.jsonl`;
- `parsed_candidates.jsonl`;
- `parser_warnings.tsv`;
- `parser.sqlite`.

Parser runs are explicitly separate from `data/editor.sqlite`. Candidate
matching, merge planning, and applying safe reviewed changes remain future
separate steps.

## 2026-05-27.2

Created the first real parser batch for:

```text
source/incoming/260527-1_1-20.txt
```

Batch layout:

- `source/incoming/260527-1_1-20/original/260527-1_1-20.txt`;
- `source/incoming/260527-1_1-20/extracted/260527-1_1-20.txt`;
- `source/normalized_text/260527-1_1-20.jsonl`;
- `data/parser_runs/2026-05-27-001/`.

Added read-only parser-run comparison against `data/editor.sqlite`. The compare
step writes:

- `match_candidates.tsv`;
- `proposed_changes.tsv`;
- `conflicts.tsv`;
- `new_records.tsv`;
- `merge_plan.json`.
- `review_report.html`.

No editor database changes are applied by the parser or compare step.

Updated `review_report.html` to support the first editorial review stage:

- starts with `Совпадения`;
- shows only pairs with `match_score >= 0.8`;
- renders candidate and editor records as bibliography lines;
- highlights whole differing fields;
- places records without a strong match into `Новые`;
- uses static review buttons only, with no apply behavior.

## 2026-05-25.1

Created `bibliobon-data` as the planned canonical data workspace for Bibliobon bibliography data.

Added:

- project memory files: `README.md`, `CONTEXT.md`, `TODO.md`, `AGENTS.md`;
- working directories: `source/`, `data/`, `data/manual/`, `reports/`, `scripts/`, `docs/`;
- copied current-site reference docs into `docs/reference/current-site/`.

Initial decision:

- The Django site should become a consumer of a generated data export from this project.
- The active source is the current Django database plus Google Sheets manual edits, not the legacy Excel import.
- Future data exports should use stable data-project IDs rather than Django primary keys.

No site contract has been created yet.

## 2026-05-25.2

Added repeatable bootstrap from the current Django SQLite database:

- `scripts/bootstrap_from_site_db.py`;
- JSONL exports under `data/`;
- `data/build_manifest.json`;
- diagnostic reports under `reports/`.

Initial bootstrap ID decision:

- preserve Django primary keys as `source_django_id`;
- assign deterministic compatibility IDs such as `work-043693`, `author-000001`, `journal-000001`, and `journal-issue-000001`;
- treat these as bootstrap stable IDs until a later explicit curated-ID migration.

The bootstrap is read-only against the Django site database and does not define the final `data/site_contract.json` yet.

## 2026-05-25.3

Added a separate local editor Django app under `editor/`.

Data/editor decision:

- the editor uses its own SQLite database at `data/editor.sqlite`;
- the initial editor database is imported from bootstrap JSONL files, not read directly from the public site database;
- stable data-project IDs are primary keys in the editor models;
- the public `bibliobon-catalog` site remains a future consumer of generated export artifacts.

Implemented initial editor scope:

- works list/search;
- section/category list;
- journals and collection-like container works list;
- detail pages for journals and containers;
- conversion of a one-article journal or one-article container into a single source;
- Django admin for direct editorial changes.

Google Sheets import/export and final site export are not yet implemented in the editor.

## 2026-05-25.4

Accepted the target canonical model for future work:

- `Source`;
- `Author` / `SourceAuthor`;
- `Section`;
- `Tag` / `SourceTag`;
- `Periodical`;
- `Issue`;
- `ArticlePlacement`;
- `SourceGroup` / `SourceGroupItem`.

Added:

- `docs/DATA_MODEL.md`;
- `docs/MIGRATION_STRATEGY.md`.

Important decisions:

- data structure work and legacy cleanup are separate tracks;
- new imports should target the canonical model directly;
- collection-like containers should be modeled as `Issue`, not as a separate active `Collection`;
- one-article containers are kept structured when they are bibliographically real containers;
- accidental one-article container structures should be convertible to standalone `Source`;
- the public `bibliobon-catalog` site should not be changed again until the data model, cleanup tools, and export contract are stable.

## 2026-05-25.5

Added an MVP two-panel relation editor in the local data editor:

- left panel: searchable/filterable bibliography records;
- right panel target modes: journal issues, collection/container works, authors, tags, sections, languages;
- selected left records can be linked to one selected right-side target.

Current implementation still runs on the editor MVP compatibility models (`Work`, `Article`, etc.) and should be adapted when the editor is refactored to the target `Source` / `Issue` / `ArticlePlacement` schema.

## 2026-05-25.6

Added ГОСТ-ready structured fields to the editor compatibility models:

- title and responsibility fields on works/sources;
- edition, publication, manufacture, physical-description, series, notes, identifier, and resource-type fields on works/sources;
- heading and authority fields on authors;
- name-as-printed and heading/responsibility flags on work-author links;
- periodical metadata on journals;
- issue/container title, numbering, chronology, publication, identifier, and notes fields on journal issues;
- raw and normalized page/location fields on article placements.

Citation renderer profiles were not implemented yet and remain tracked in `TODO.md`.

## 2026-05-25.7

Added first-pass ГОСТ field backfill:

- management command `backfill_gost_fields`;
- bootstrap importer now fills obvious structured fields on future JSONL imports.

The backfill is intentionally conservative and non-destructive. It preserves raw
legacy fields and only fills obvious mappings such as author heading names,
responsibility statements, publication dates from normalized years, issue
enumeration, and article page bounds.

## 2026-05-25.8

Added Google Sheets import/export for the local editor:

- `export_google_sheet`;
- `import_google_sheet`;
- dry-run import support;
- automatic `data/editor.sqlite` backup before real import;
- canonical ID-based sheet schema instead of public-site Django IDs.

Relation sheets are authoritative replace-all lists:

- `WorkAuthors`;
- `WorkTags`;
- `WorkGroupItems`.

Removing a row from those sheets removes the corresponding relation on import.

Added unused-data cleanup:

- `cleanup_unused` dry-run report;
- `cleanup_unused --apply` with automatic SQLite backup.

Current cleanup categories:

- unused authors;
- unused leaf tags;
- empty journal issues;
- empty journals;
- empty work groups;
- unused legacy collections.

## 2026-05-25.9

Exported the editor data to the working Google Sheets document:

```text
1zYFOqM-wBT6mFYLmblaGRBlV0rJeWAaLrEsCMJYKHTw
```

Updated the Google Sheets writer to resize sheet grids before batch writing, so
large editor tabs are not limited by the default 1000 rows.

Verified the exported data with dry-run import:

```text
updated=14591, created=0, relations_replaced=3, skipped=0
```

## 2026-05-25.10

Cleaned unused editor data with automatic SQLite backups:

- removed authors not attached to any work;
- removed unused leaf tags;
- removed empty journal issues;
- removed journals left without issues after cleanup;
- removed unused legacy collections.

Backups created:

- `data/backups/editor.before-cleanup-unused.20260525-122906.sqlite`;
- `data/backups/editor.before-cleanup-unused.20260525-123001.sqlite`.

After cleanup, the unused-data dry run reports zero rows in all current cleanup
categories. The working Google Sheets document was re-exported from the cleaned
editor database:

```text
works=5747, authors=1556, journals=323, issues=1815, article_placements=4558
```

Updated the editor periodicals page so selecting a journal or collection on
`/periodicals/` shows the linked article/source records below the container
lists.

## 2026-05-25.11

Added target editor tables alongside the legacy compatibility models:

- `Source`;
- `SourceAuthor`;
- `SourceTag`;
- `Periodical`;
- `Issue`;
- `ArticlePlacement`;
- `SourceGroup`;
- `SourceGroupItem`.

Added `convert_legacy_to_target` management command. The command supports
report-only mode and `--apply --reset` mode with automatic SQLite backup before
writing.

First conversion result:

```text
sources=5747
source_authors=5033
source_tags=2999
periodicals=323
issues=2060
article_placements=4558
source_groups=9
source_group_items=108
skipped_article_placements=0
```

Backup created:

```text
data/backups/editor.before-target-conversion.20260525-124256.sqlite
```

The conversion report is written to:

```text
reports/target_conversion_report.json
```

The report currently flags 23 standalone records that look like articles but do
not yet have a normalized article placement. These are preserved for manual
review instead of being split automatically.

Changed `/periodicals/` from long full-page tables to a two-panel editor view:
container lists on the left, linked records for the selected journal or
collection on the right.

## 2026-05-25.12

Updated `/works/` so every work row includes a compact relation summary:

- legacy links: authors, tags, section, article placement, container status, and
  work groups;
- target links: `Source`, `SourceAuthor`, `SourceTag`, `ArticlePlacement`,
  `Issue`, and `SourceGroup`.

The works search now also matches stable editor IDs, source Django IDs, and
source numbers. Example checked:

```text
/works/?q=work-046972
```

`work-046972` is an article source with no authors, tag
`Деятельность бонистов`, and placement in the journal issue
`Нумизматика, 2003, № 2 (2)`, pages `4`.

## 2026-05-25.13

Added `convert_journal_to_collection` command for repeatable correction of
legacy journals that are actually collection containers. The command supports
dry-run by default and `--apply` with SQLite backup, then refreshes target
`Source`/`Issue` tables.

Applied corrections:

- `journal-002687` -> `work-container-journal-002687`;
- merged `journal-002668` and `journal-002733` ->
  `work-container-journal-002668`.

Result:

```text
works=5749
journals=320
journal_issues=1812
article_placements=4558
```

Backups created:

```text
data/backups/editor.before-journal-to-collection.20260525-145723.sqlite
data/backups/editor.before-target-conversion.20260525-145723.sqlite
data/backups/editor.before-journal-to-collection.20260525-145735.sqlite
data/backups/editor.before-target-conversion.20260525-145735.sqlite
```

The working Google Sheets document was re-exported after the correction.

## 2026-05-25.14

Extended `/relations/` with editor actions:

- left panel can switch between works, journals, and collection/container works;
- selected journals can be converted or merged into one collection container;
- selected collection containers can be merged into a selected target collection;
- ordinary relation linking remains available for works.

All mass-edit actions create a SQLite backup and refresh the target
`Source`/`Issue` model after applying. Collection merges move article
placements to the target collection and mark source containers as
`needs_review`; source container work rows are preserved instead of being
deleted automatically.

## 2026-05-25.15

Added editor Google Sheets sync page:

```text
/google-sheets/
```

The page provides:

- export from editor SQLite to the working Google Sheets document;
- dry-run import from Google Sheets without writing to SQLite;
- real import from Google Sheets with SQLite backup;
- target `Source`/`Issue` model refresh after real import.

Default settings now point to the working spreadsheet and local service-account
key unless overridden by environment variables:

```text
GOOGLE_SHEETS_SPREADSHEET_ID=1zYFOqM-wBT6mFYLmblaGRBlV0rJeWAaLrEsCMJYKHTw
GOOGLE_SHEETS_CREDENTIALS=secrets/google-service-account.json
```

Verified the web dry-run import against the working spreadsheet:

```text
updated=14340, created=0, relations_replaced=3, skipped=0
```

## 2026-05-25.16

Added `cleanup_redundant_fields` command for safe mechanical cleanup of fields
that duplicate normalized structured data.

Current rules:

- clear `Work.physical_description` when it exactly equals non-empty
  `Work.extent`;
- clear `JournalIssue.enumeration` when it is the same as `issue_number` after
  removing a leading issue-number marker such as `№ `.

Applied cleanup with SQLite backup and target-model refresh:

```text
physical_description=832
enumeration=1639
```

Backups created:

```text
data/backups/editor.before-cleanup-redundant-fields.20260525-164018.sqlite
data/backups/editor.before-target-conversion.20260525-164018.sqlite
```

Verified examples:

```text
work-043997: extent='Самиздат.', physical_description=''
journal-issue-010379: issue_number='11 (53)', enumeration=''
```

The working Google Sheets document was re-exported after cleanup.

## 2026-05-25.17

Extended `cleanup_redundant_fields` with article/container publication-place
cleanup.

Rule:

- if an article `Work.publication_place` exactly matches its journal issue or
  collection container publication place, clear the article-level
  `publication_place`.

Applied cleanup:

```text
article_publication_place=125
```

Backups created:

```text
data/backups/editor.before-cleanup-redundant-fields.20260525-170325.sqlite
data/backups/editor.before-target-conversion.20260525-170325.sqlite
```

122 articles still have article-level publication place while the container has
no publication place. These were intentionally left unchanged; they are
candidates for a separate reviewed "move place up to container" workflow.

The working Google Sheets document was re-exported after cleanup.

## 2026-05-25.18

Extended `cleanup_redundant_fields` with article page cleanup.

Rule:

- if article `Work.article_pages` exactly matches `Article.pages_raw` or legacy
  `Article.pages`, clear `Work.article_pages`.

Rationale:

- article page location belongs to `ArticlePlacement`;
- `ArticlePlacement.pages_raw` preserves the original page string;
- `ArticlePlacement.page_start` and `page_end` store normalized numeric bounds
  when parsing is confident.

Applied cleanup:

```text
article_pages=3048
```

Backups created:

```text
data/backups/editor.before-cleanup-redundant-fields.20260525-172945.sqlite
data/backups/editor.before-target-conversion.20260525-172945.sqlite
```

After cleanup:

```text
Work.article_pages nonempty=0
Article.pages_raw nonempty=3048
Article.page_start/page_end present=3046
```

The working Google Sheets document was re-exported after cleanup.

## 2026-05-25.19

After a large manual Google Sheets update, reran the repeatable redundant-field
cleanup.

Applied cleanup:

```text
article_publication_place=11
article_pages=373
physical_description=0
enumeration=0
```

Backups created:

```text
data/backups/editor.before-cleanup-redundant-fields.20260525-203526.sqlite
data/backups/editor.before-target-conversion.20260525-203527.sqlite
```

Repeat dry-run after cleanup reports zero redundant rows for all current rules.
The target model was refreshed and the working Google Sheets document was
re-exported.

Multiple-placement normalization for articles published in several journal
issues was intentionally not run. It remains tracked in `TODO.md`.

## 2026-05-25.20

Reduced the active Google Sheets export/import schema to bibliography-editing
tabs only:

- `Works`;
- `Authors`;
- `WorkAuthors`;
- `Journals`;
- `JournalIssues`;
- `ArticlePlacements`;
- `ContainerWorks`;
- `WorkGroups`;
- `WorkGroupItems`.

The following tabs are no longer part of the active export/import set and can be
handled later:

- `Sections`;
- `Tags`;
- `WorkTags`;
- `Languages`.

Expanded the `Works` sheet with existing editor fields needed for cleanup of
physical description and responsibility data:

- `edition_statement`;
- `additional_edition_statement`;
- `illustrations`;
- `dimensions`;
- `accompanying_material`;
- `series_statement`;
- `notes`.

No database migration was needed: these fields already existed in the editor
models. Export to the working Google Sheets document completed, and dry-run
import with the new schema passed:

```text
updated=14004, created=0, relations_replaced=2, skipped=0
```

## 2026-05-26.1

Resolved Google Sheets import failure caused by duplicate journal-issue identity
rows after a large manual sheet update.

Changes:

- removed the legacy SQLite uniqueness constraint on
  `JournalIssue(journal, year, issue_number, volume)` in the editor database;
- Google Sheets import now writes a duplicate issue report instead of aborting
  before import;
- duplicate report path:

```text
reports/google_sheet_duplicate_journal_issues.tsv
```

Rationale:

- the old constraint did not include `gross_number`, so distinct issues such as
  `№ 11 (52)` and `№ 11 (53)` could not both be imported after normalization;
- some remaining duplicate issue rows are real cleanup candidates and should be
  merged through a separate reviewed workflow rather than blocked at SQLite
  constraint level.

Imported the working Google Sheets document after the fix:

```text
updated=14004, created=0, relations_replaced=2, skipped=0,
duplicate_issue_keys=51
```

Backups created:

```text
data/backups/editor.before-google-import.20260525-234313.sqlite
data/backups/editor.before-target-conversion.20260525-234338.sqlite
```

Target model was refreshed after import. Redundant-field cleanup dry-run reports
zero rows, and `manage.py check` passes.

## 2026-05-26.2

Added `normalize_article_issue_fields` command.

Current safe rules:

- move `JournalIssue.publication_details` to the linked article `Work.publication_details`
  only when the journal issue has exactly one linked article and the article work
  field is blank;
- clear `JournalIssue.publication_details` when it exactly matches the only
  linked article work field;
- clear article `Work.volume_number` when it matches the linked
  `JournalIssue.issue_number` after removing a leading issue marker such as
  `№ `.

Applied safe normalization:

```text
moved_issue_details_to_article=1165
cleared_issue_details_same_existing=0
cleared_article_volume_number=95
```

Left for review:

```text
issue_details_conflicts=1
issue_details_ambiguous_multi_article=625
article_volume_number_differs=196
duplicate physical issue keys including gross_number=12
```

Reports:

```text
reports/issue_details_conflicts.tsv
reports/issue_details_ambiguous_multi_article.tsv
reports/article_volume_number_differs.tsv
```

Backups created:

```text
data/backups/editor.before-normalize-article-issue-fields.20260525-235132.sqlite
data/backups/editor.before-target-conversion.20260525-235133.sqlite
```

Target model was refreshed and the working Google Sheets document was
re-exported after normalization.

## 2026-05-26.3

Added `merge_work_records` management command for reviewed duplicate Work
merges.

The command:

- runs as dry-run by default;
- creates a SQLite backup before applying changes;
- moves author links, tag links, article/container links, source group links,
  and legacy collection links from the duplicate record to the kept record;
- deletes the duplicate `Work` and its target-model `Source`;
- refreshes target-model tables after a successful merge.

Applied reviewed merge:

```text
source: work-049406
target: work-049397
```

Result:

- duplicate `work-049406` and `Source(work-049406)` removed;
- canonical `work-049397` preserved;
- `work-049397` remains linked to 5 contained article records and
  `group-000006`;
- legacy duplicate `Collection(collection-001927)` had no linked articles and
  was removed while keeping `collection-001918` for the canonical work.

Backups created during reviewed attempts:

```text
data/backups/editor.before-merge-work-records.20260526-000608.sqlite
data/backups/editor.before-merge-work-records.20260526-000633.sqlite
data/backups/editor.before-merge-work-records.20260526-000659.sqlite
data/backups/editor.before-target-conversion.20260526-000659.sqlite
```

`manage.py check` passes after the merge.

## 2026-05-26.4

Google Sheets export policy changed: do not export to the working Google Sheets
document unless the user explicitly asks for export.

Added bibliographic field:

```text
Work.circulation
Source.circulation
```

`circulation` is exported/imported through the `Works` sheet as an optional
column. Imports from an older sheet that does not yet contain `circulation` do
not fail.

Added `Work.is_container` for explicit collection/container works that may not
yet have linked articles. Target conversion now creates collection-type `Issue`
rows for works that either contain articles or have `is_container=True`.

Editor changes:

- custom editor searches now use Python `casefold()` matching so Cyrillic
  searches are independent of letter case;
- relation editor now has actions for selected works:
  `Сделать журналом` and `Сделать сборником`;
- `Сделать сборником` marks the work as a container and creates its target
  collection issue;
- `Сделать журналом` creates/updates a legacy `Journal` plus one
  `JournalIssue` from the selected work, for later article linking.

Legacy `Collection` policy: do not create new legacy `Collection` rows. Keep
compatibility only until remaining old links can be migrated and the table can
be retired.

Backups:

```text
data/backups/editor.before-schema-circulation-container.20260526-032153.sqlite
data/backups/editor.before-target-conversion.20260526-002156.sqlite
```

`manage.py check` passes after the schema and editor changes.

## 2026-05-26.5

Added explicit `Work.work_type=container`.

Migration `0007_work_type_container`:

- adds `container` as a valid work type;
- marks existing container records as `work_type=container` when they either
  have linked articles through `Article.container_work` or had the temporary
  `is_container=True` flag.

`is_container` remains only as a temporary compatibility field; new editor
logic uses `work_type=container` as the canonical signal.

Relation editor changes:

- merging collections now moves article links to the target collection and
  deletes the source collection work when it becomes empty;
- selected works can be deleted from the relation editor;
- deletion is blocked for containers that still contain linked records;
- selected containers can be converted back to ordinary books only when they
  contain no linked records;
- ordinary works can be converted to containers only if they are not already
  part of another container/journal.

Work list changes:

- each row shows whether the record is a container and how many linked articles
  it contains;
- if a record is part of a container, the container title is shown with a link;
- groups and categories were removed from the list view;
- a compact debug bibliographic line is shown under each title by joining
  non-empty record fields with `—`.

Legacy `Collection` status:

```text
active Article.collection links: 0
unused legacy Collection rows: 284
```

This means legacy `Collection` is no longer needed for active article
placement in the editor database, but the remaining unused rows should still be
removed through a reviewed backup cleanup rather than silently dropped.

Backups:

```text
data/backups/editor.before-container-type-and-delete-rules.20260526-094150.sqlite
data/backups/editor.before-target-conversion.20260526-064158.sqlite
```

`manage.py check` passes after the change. The `/works/` and `/relations/`
pages were checked against the reported search URL and load without
`OperationalError` or `IntegrityError`.

## 2026-05-26.6

Removed remaining legacy `Collection` rows in a reviewed cleanup pass.

Precondition:

```text
Article.collection links: 0
Collection rows: 284
```

Applied cleanup:

```text
deleted Collection rows: 284
Collection rows after cleanup: 0
```

Backups:

```text
data/backups/editor.before-delete-legacy-collections.20260526-095420.sqlite
data/backups/editor.before-target-conversion.20260526-065421.sqlite
```

Editor changes:

- `/works/` supports `?container=<work_id>` to show only records contained in
  a selected container;
- container titles and container relation links on `/works/` point to that
  filtered view in the same window;
- removed `№` and `Год` columns from the work list;
- added row-level delete action for records that are not containers with linked
  records;
- row-level deletion removes generated target rows and any remaining
  compatibility `Collection` row for that work before deleting the `Work`.

`manage.py check` passes. `/works/` and `/works/?container=work-049397` load
successfully after the cleanup.

## 2026-05-26.7

Added editable container index support for the `Works` Google Sheets tab.

New `Works` column:

```text
container_index
```

Export values:

- `container:<work_id>` for articles linked to a collection/container work;
- `issue:<journal_issue_id>` for articles linked to a journal issue;
- blank for standalone records.

Import behavior:

- changing `container_index` to `container:<work_id>` links the work as an
  article inside that container;
- changing it to `issue:<journal_issue_id>` links the work as an article inside
  that journal issue;
- `Works.container_index` is applied after the legacy `ArticlePlacements` sheet,
  so it is the final imported placement value.

Editor changes:

- `/works/` search now matches related journal IDs, journal issue IDs, journal
  titles, container work IDs, and container titles;
- `/works/?journal=<journal_id>` shows all article records linked to a journal;
- `/works/?issue=<journal_issue_id>` shows all article records linked to a
  specific journal issue;
- article rows show `В книгу` instead of `Удалить`;
- `В книгу` detaches the record from its journal/container placement, deletes
  the article placement row, and converts the work to an ordinary book.

Checks:

- `manage.py check` passes;
- fresh export values can be dry-run imported with the new `container_index`
  column;
- `/works/?q=journal-002633` and `/works/?journal=journal-002633` load and show
  the linked article record.

## 2026-05-26.8

Added `Works.linked_authors` as an export-only control column.

The field contains authors linked through `WorkAuthor`, ordered by
`sort_order`, using `name_as_printed` when present and falling back to
`Author.display_name`. Author roles are shown in square brackets.

Import treats `linked_authors` as optional and ignores changes to it. Editable
author relations remain in `WorkAuthors`.

Implementation note: export builds linked author text with a separate
`WorkAuthor` query instead of prefetching all work-author relations through the
full `Work` queryset, avoiding SQLite expression-depth failures on export.

Checks:

- `manage.py check` passes;
- fresh export values include `linked_authors`;
- fresh export values can be dry-run imported.

## 2026-05-26.9

Moved editable structured article page bounds into `Works`.

New `Work` fields:

```text
page_start
page_end
```

Migration `0008_add_work_page_bounds` copied existing `Article.page_start` and
`Article.page_end` values into the linked `Work` rows.

Google Sheets schema changes:

- `Works` now exports/imports `page_start` and `page_end`;
- `ArticlePlacements` no longer exports `page_start` or `page_end`;
- `ArticlePlacements` no longer exports `container_work_id` or
  `container_work_title`;
- `ContainerWorks` is no longer exported because container works are visible in
  `Works` via `work_type=container`, and article/container membership is edited
  via `Works.container_index`.

Compatibility:

- import still tolerates older `ArticlePlacements` sheets containing
  `container_work_id`, `container_work_title`, `page_start`, and `page_end`;
- target conversion uses `Work.page_start/page_end` first and falls back to
  legacy `Article.page_start/page_end`.

Applied redundant-field cleanup after the latest Google Sheets import:

```text
article_publication_place: 1
article_inferred_year: 3754
article_publication_date: 3754
article_publisher: 41
article_pages: 1130
```

Backups:

```text
data/backups/editor.before-work-page-bounds.20260526-115651.sqlite
data/backups/editor.before-cleanup-redundant-fields.20260526-085733.sqlite
data/backups/editor.before-target-conversion.20260526-085733.sqlite
```

Added editor duplicate workflow:

- work list now has `Дублировать`;
- duplicate copies bibliographic fields, authors, tags, and groups;
- duplicate gets a new stable `work-...` ID, synthetic source number, no
  container/journal article placement, and `description_status=needs_review`;
- duplicate opens in admin for editing after creation.

Checks:

- `manage.py check` passes;
- redundant-field cleanup dry-run is zero after apply;
- fresh export values can be dry-run imported with the new sheet schema.

## 2026-05-26.10

Google Sheets import now treats a completely blank sheet header row as an
absent sheet instead of raising a missing-columns error.

This prevents empty optional relation sheets such as `WorkAuthors` from
blocking import with errors like:

```text
Нет колонок: work_id, work_title, author_id, ...
```

When a relation sheet is absent or blank, import skips that sheet and preserves
existing database relations instead of replacing them.

Checks:

- `manage.py check` passes;
- dry-run import with a blank `WorkAuthors` sheet succeeds.

## 2026-05-26.11

Imported the working Google Sheets document after a transient Google API
connection failure.

Observed failure:

```text
[Errno 54] Connection reset by peer
```

The user also saw a similar transient error:

```text
[Errno 22] Invalid argument
```

A retry succeeded:

```text
updated=13975
created=0
relations_replaced=2
skipped=0
duplicate_issue_keys=51
```

Backups:

```text
data/backups/editor.before-google-import.20260526-110834.sqlite
data/backups/editor.before-target-conversion.20260526-110905.sqlite
```

Target model was refreshed after the import.

Google Sheets reading now uses the same retry wrapper as writes, and retries
temporary HTTP/API/network failures including 429/5xx responses and transient
`Errno 22`/`Errno 54` failures.

Checks:

- `manage.py check` passes;
- `import_google_sheet --dry-run` succeeds after the retry change.

## 2026-05-26.12

Changed the one-article journal conversion workflow.

When converting the only article of a journal into a standalone source, the
editor now appends journal/issue information to `Work.publication_details`
instead of replacing the existing value. The appended text includes the journal
title, year, issue number, volume/part when present, and existing issue raw
publication details.

Checks:

- `manage.py check` passes.

## 2026-05-26.13

Added first site export artifact command:

```bash
python3 editor/manage.py export_site_artifact
```

The command refreshes target tables by default and writes:

```text
data/bibliobon.sqlite
data/site_contract.json
data/build_manifest.json
```

The exported SQLite is a contract-facing database, not a copy of the editor
Django database. Exported tables:

```text
languages
sections
tags
authors
sources
source_authors
source_tags
periodicals
issues
article_placements
source_groups
source_group_items
```

Current artifact counts:

```text
sources: 5733
authors: 1556
source_authors: 5033
periodicals: 314
issues: 2057
article_placements: 4544
source_groups: 9
source_group_items: 94
```

Generated artifact:

```text
data/bibliobon.sqlite
sha256: 4e9b48272876cc8a11a3b277daa884a870705fdf003e93f6f1f92b90326ed33b
size: 4694016 bytes
```

Checks:

- `python3 editor/manage.py check`;
- `sqlite3 data/bibliobon.sqlite "PRAGMA integrity_check"` returns `ok`;
- `data/site_contract.json` and `data/build_manifest.json` parse as valid JSON.

## 2026-05-26.14

Imported the current working Google Sheets data into the editor database and
rebuilt target tables from the legacy/editor model.

Backups created:

```text
data/backups/editor.before-google-import.20260526-162930.sqlite
data/backups/editor.before-target-conversion.20260526-163054.sqlite
```

Import summary:

```text
updated: 13975
created: 0
relations_replaced: 2
skipped: 0
duplicate_issue_keys: 51
duplicate_issue_report: reports/google_sheet_duplicate_journal_issues.tsv
```

Target table counts after conversion:

```text
sources: 5733
source_authors: 5025
source_tags: 2999
periodicals: 314
issues: 2057
article_placements: 4558
source_groups: 9
source_group_items: 94
skipped_article_placements: 0
```

Diagnostics:

```text
articles_without_container: 0
articles_with_both_main_containers: 0
legacy_collection_articles: 0
standalone_records_that_look_like_articles: 23
```

Checks:

- `python3 editor/manage.py check` passes.

## 2026-05-26.15

Added editor service tools at `/service/` for repeatable maintenance actions:

- Google Sheets export, import dry-run, and import;
- dry-run/apply automatic merge of duplicate journal issues where
  `journal_id + year + issue_number + volume` match and important fields have
  no conflicts;
- dry-run/apply cleanup of empty journal issues, empty journals, and empty
  collection containers;
- dry-run/apply cleanup of authors without linked records;
- dry-run/apply cleanup of redundant article fields duplicated from the
  container issue or collection, including publication data, physical
  description fields, series, ISBN/ISSN, and circulation where the matching
  container field exists.

Duplicate issue merge writes a report to:

```text
reports/merge_duplicate_journal_issues.tsv
```

Replaced the `/periodicals/` page with a flat "Журналы и сборники" list similar
to the works list. It supports search, type filtering, links to related works,
admin edit links, and "В книгу" for journals/collections that contain exactly
one article.

Checks:

- `python3 editor/manage.py check` passes;
- Django test client returns `200` for `/service/` and `/periodicals/`.

## 2026-05-26.16

Changed the Google Sheets `ArticlePlacements` contract for article/container
links.

Future exports use a single editable container field:

```text
container_id
container_type
container_title
```

`container_id` may point either to a `JournalIssues.journal_issue_id` or to a
`Works.work_id` for a collection/container work. `container_type` is exported as
`journal_issue` or `container_work` for readability; import can also infer the
type from `container_id` if the type cell is blank.

Legacy imports with `journal_issue_id` and optional `container_work_id` are still
accepted.

Also isolated editor authentication cookies from other local Django sites:

```text
SESSION_COOKIE_NAME = bibliobon_data_editor_sessionid
CSRF_COOKIE_NAME = bibliobon_data_editor_csrftoken
SESSION_COOKIE_AGE = 14 days
```

This avoids `sessionid` collisions between the editor on `127.0.0.1:8001` and
other local sites on different ports.

Checks:

- `python3 editor/manage.py check` passes;
- `build_export_values()` produces the new `ArticlePlacements` headers.

## 2026-05-27.01

Added a Russian draft field guide for the future bibliographic parser:

```text
docs/PARSER_FIELD_GUIDE_RU.md
```

The guide is based on the relevant parts of:

- `source/GOST_R_7.0.100-2018.pdf`;
- `source/GOST_R_7.0.80_2023.pdf`.

It describes parser-facing use of the editor tables and fields for Bibliobon's
scope: books, articles, periodicals, newspapers, collections, multivolume works,
conference materials, legal/normative acts, and electronic resources. It also
documents proposed missing fields such as `multipart_statement`,
`publication_status_note`, `translation_note`, event fields, legal-document
fields, `access_mode`, and `source_of_title_note`.

## 2026-05-28.01

Added an editor-site entry point for the bibliography parser workflow:

- `/parser/` accepts pasted bibliography text or an uploaded `.txt`;
- the editor creates `source/incoming/<batch>/original/` archive files
  automatically;
- the editor maintains `source/incoming/<batch>/work/current.txt` as the working
  copy and appends new submissions to it;
- the previous working copy is saved in `work/history/` before each append;
- each submission launches `scripts/parse_bibliography.py run` and then
  `compare`, producing a staging run under `data/parser_runs/<run_id>/`;
- parser run pages are exposed through the editor as HTML review pages.

The parser remains staging-only. The comparison reads `data/editor.sqlite`, but
the editor database is not modified by this workflow. Stage 2 and stage 3 are
currently static prototype pages copied into new runs; persistence of review
decisions and the final backed-up apply step remain future work.

## 2026-05-28.02

Added the first AI-assisted preprocessing layer for parser staging:

- `docs/prompts/bibliography_ai_markup_ru.md` defines the prompt contract;
- `scripts/ai_markup_bibliography.py` creates AI markup JSONL from `.txt` or
  `.jsonl` inputs;
- output is stored under `source/incoming/<batch>/ai_markup/` by default;
- the script uses OpenAI Structured Outputs through the Responses API when
  `OPENAI_API_KEY` is set;
- `--mock` creates deterministic low-confidence markup for local technical
  tests without an API call;
- `scripts/parse_bibliography.py run` now consumes `ai_markup` objects from
  JSONL and builds staging parser candidates from those fields;
- the editor `/parser/` page has a checkbox that launches AI markup before the
  parser run when `OPENAI_API_KEY` is configured.

This remains staging-only. AI markup is saved as an auditable intermediate
artifact and does not write to `data/editor.sqlite`.

## 2026-05-28.03

Improved the parser review workflow:

- added an editor intake checkbox to clear `source/incoming/<batch>/work/current.txt`
  before adding new submitted data; the previous working file is backed up under
  `work/history/`;
- fixed the prototype stage 2 link to stage 3 for Django-served parser pages;
- stage 2 prototype edits are now restored from browser `localStorage`, so
  accepted modal edits survive page reloads in the current prototype;
- parser staging now splits `article // container` source rows into two
  independent candidates: one with `candidate_part=article` and one with
  `candidate_part=container`.

The durable target remains server-side review state under the parser run
directory before any apply step writes to `data/editor.sqlite`.

## 2026-05-28.04

Adjusted parser review diagnostics and title parsing:

- review HTML now shows candidate matches with `match_score >= 0.7`;
- pairs without differences in the short displayed citation still show
  diagnostic notes when the score or non-displayed fields differ;
- diagnostics distinguish display-field differences from differences in raw
  publication details and match reasons;
- generated citation rows prefix article candidates as `Статья:` and
  container/issue candidates as `Журнал/сборник:`;
- title parsing preserves short hyphenated titles such as `Гривне - год` and
  can split the following sentence into `subtitle`.

## 2026-05-28.05

Refined parser-review diagnostics and debugging workflow:

- if the only diagnostic differences are service fields such as raw
  publication details, the review note says `Отличия в служебных полях`;
- stage 2 review template now has a `Все поля` expander in the modal for
  non-service fields beyond the compact editor set;
- new-record staging rows add a note like `Дата добавления в базу: 2026-05-28`
  so later Google Sheets review can filter recently added records;
- static stage 2/3 HTML prototypes moved to `data/parser_review_templates/`;
- parser run history, normalized JSONL files, AI markup artifacts, batch events,
  manifests, and working-file history backups were cleared for debugging.

## 2026-05-28.06

Improved matching for container candidates:

- parser comparison now includes `sources_periodical` and `sources_issue` rows,
  not only `sources_source`;
- confident records with `confidence >= 0.7` are shown as paired weak matches
  when any probable database row exists, instead of going directly to `Новые`;
- current parser review for the debug run no longer sends the
  `Вестник Национального Банка Украины` container candidate to `Новые`;
- stage 3 review template now exposes two apply choices:
  `Внести только изменённые записи` and
  `Внести изменённые и новые записи`.

## 2026-05-28.07

Connected stage 3 apply buttons to a safe staging endpoint:

- `POST /parser/runs/<run_id>/apply/` accepts `changed_only` and
  `changed_and_new` modes;
- the endpoint creates a backup of `data/editor.sqlite`;
- the endpoint records `data/parser_runs/<run_id>/apply_request.json`;
- no parser changes are written to `data/editor.sqlite` yet.

The real parser apply step still needs persisted review state and explicit
field-level write logic.

## 2026-05-28.08

Added durable parser review state:

- `GET/POST /parser/runs/<run_id>/state/` reads and writes
  `data/parser_runs/<run_id>/review_state.json`;
- stage 1 review rows now post `new`, `keep_new`, `keep_old`, and `clear`
  decisions with candidate/editor identifiers;
- stage 2 modal accepts and rollback actions now post saved field values or
  clear events to the run state;
- the current debug run review pages were regenerated with the updated state
  wiring.

This still does not write parser results into `data/editor.sqlite`; the real
apply logic remains a separate pending step.

## 2026-05-29.01

Added parser container resolution for article records:

- `scripts/parse_bibliography.py compare` now writes
  `container_resolution.tsv` and `container_resolution.json`;
- periodical containers are resolved separately from bibliographic source
  matching: first periodical title/ISSN, then issue year/number/volume/part;
- collection-like containers are resolved against existing collection/issue
  source records;
- the parser review report now has a `Контейнеры статей` section showing
  whether the run should link an existing issue/container, confirm a similar
  one, or ask before creating a new journal/issue/container;
- issue year extraction now prefers the publication-date segment, preventing
  circulation values such as `1600 экз.` from being mistaken for the issue
  year.

For the current debug run, all 7 article containers resolve to existing journal
issues.

## 2026-05-29.02

Added stage 0 container review UI:

- parser runs now open `review_containers.html` before the record-matching
  page;
- `review_containers.html` separates containers that need editor action from
  containers found automatically;
- stage 0 buttons persist decisions such as `use_existing_issue`,
  `create_issue`, `choose_periodical`, and `create_collection` into
  `review_state.json`;
- the record-matching page links back to stage 0 and shows article container
  bindings inline, so article matches are reviewed with their journal/issue or
  collection context visible.

This is still staging-only. The stage 0 selector UI for choosing an alternative
existing journal/issue/container remains to be implemented.

## 2026-05-29.03

Refined parser review UI:

- stage 0 container review now uses the same compact citation styling as later
  review stages;
- stage 0 "choose" actions open a searchable list of candidate issues,
  periodicals, or collection containers and save the selected ID into
  `review_state.json`;
- duplicate `match_score` output on the record-matching page was removed from
  diagnostic notes, leaving the score in the pair metadata only;
- parser intake now has a full debug cleanup button for parser staging data.

The cleanup removes incoming batches, normalized parser text, and parser runs;
it does not modify `data/editor.sqlite`.

## 2026-05-29.04

Compacted parser review output:

- stage 0 now groups repeated article containers for display: a periodical is
  shown once, with unique issues listed underneath;
- duplicate source articles pointing to the same issue are counted in that
  issue row instead of producing repeated journal/issue confirmation blocks;
- record-match diagnostics now show `match_score` on the same line as match
  reasons;
- container-like rows use the `Журнал/сборник` prefix on both candidate and
  database rows.

## 2026-05-29.05

Separated container confirmation from record matching:

- stage 0 confirmation buttons are independent per issue/container row, so
  confirming one issue no longer clears confirmations for other issues under
  the same journal;
- generated `candidate_part=container` rows are excluded from stage 1 matching
  and new-record review;
- stage 1 now reviews only bibliographic records such as articles/books, with
  article container bindings shown inline.

## 2026-05-29.06

Updated parser cleanup and stage 3 summary:

- full parser cleanup now redirects with a browser-storage cleanup flag that
  removes `bibliobon-parser-*` `localStorage` entries;
- stage 2 no longer treats old browser `localStorage` as authoritative and
  restores edited markers only from server-side `review_state.json`;
- `review_stage3.html` is now generated from the current parser run and lists
  proposed apply items under `Новые книги`, `Данные дополнены`, `Журналы`, and
  `Сборники`.

The apply buttons still record a safe apply request only; real database writes
remain a separate implementation step.

## 2026-05-29.07

Clarified non-apply matches:

- stage 1 diagnostics now say `Отличия только в служебных полях — в базу не
  вносятся`;
- stage 3 now includes an `Уже есть в базе` group for matched records that do
  not require database writes.

## 2026-05-30.01

Added raw-source supplement review:

- parser comparison now proposes `raw_publication_details` only when the
  existing editor record has that field empty;
- stage 3 shows those matched existing records under `Данные дополнены` with
  the editor-facing label `raw-запись источника`;
- non-empty raw publication details are not replaced automatically and remain
  diagnostic-only.

## 2026-05-30.02

Made stage 3 apply visible and functional for safe supplements:

- the stage 3 apply endpoint now writes safe empty-field fills for
  `raw_publication_details` after creating a SQLite backup;
- `review_stage3.html` now shows the latest apply status, counts, note, and
  backup path after returning from the POST;
- automatic creation of new records is still not implemented in this apply
  step.

## 2026-05-30.03

Refined parser apply feedback and resolved-state handling:

- repeated stage 3 apply clicks now report that raw-source supplements were
  already внесены earlier instead of showing only skipped rows;
- applied and already-applied candidate IDs are stored in `apply_request.json`
  and excluded when parser review pages are regenerated;
- the `Source` Django admin now explicitly shows `raw_publication_details`
  under `Raw source text`.

## 2026-05-30.04

Fixed parser review state persistence:

- parser review pages now set a CSRF cookie, so stage 0 and stage 2 `fetch`
  saves can persist to `review_state.json`;
- stage 0 restores saved container confirmations and highlights the selected
  issue/container buttons after reload;
- stage 3 shows saved stage 2 modal edits as pending reviewed field changes.
- stage 2 is now generated from current run matches/new records instead of
  leaving the old static prototype page in place.

For the current debug run, stage 0 confirmations for the eight already matched
`Деньги и кредит` issues were restored from `container_resolution.tsv`.

## 2026-05-30.05

Fixed false CSRF failures on parser review saves:

- `/parser/runs/<run_id>/state/` remains staff-only but no longer requires a
  CSRF token, because the static review HTML can keep stale parser cookies
  during debug reloads;
- collection confirmations on stage 0 now save without the misleading
  `Не удалось сохранить решение` alert caused by HTTP 403;
- ready collection confirmations for the current debug run were restored from
  `container_resolution.tsv`.

## 2026-05-30.06

Improved stage 0 container review:

- container groups now show up to two original raw bibliography records under
  `Источник:` for editor control;
- technical source-count/id lines such as `записей источника` are no longer
  shown for collection groups;
- fixed the parser apply view call so the second write pass uses the internal
  apply function that accepts `write=True`.

## 2026-05-30.07

Refined parser routing and matching:

- parser intake now opens `review_report.html` directly when a run has no
  container candidates;
- when a candidate has a `match_score=1.0` match, weaker duplicate candidates
  for the same parsed record are suppressed from review and reports;
- text in trailing curly braces is parsed as `public_review` and proposed only
  as a safe fill when the existing field is empty.

## 2026-06-06.01

Added parser author staging and clarified raw publication naming:

- Google Sheets `publication_details_raw` is documented as the sheet-facing
  alias that maps to target `Source.raw_publication_details`;
- parser compare now writes `author_resolution.tsv`,
  `author_resolution.json`, and `review_authors.html`;
- author review decisions are saved under `stage_authors` in
  `review_state.json`;
- the parser intake route opens author review after containers when author
  candidates exist.

## 2026-06-06.02

Aligned parser/editor/sheet/site field vocabulary:

- added `docs/FIELD_CONTRACT.md` as the canonical field dictionary with columns
  `canonical_name`, `model`, `google_sheet_header`, `artifact_column`,
  `site_field`, `status`, and `notes`;
- added `scripts/audit_field_contract.py`, which writes
  `reports/field_contract_audit.md`,
  `reports/field_contract_audit.tsv`, and
  `reports/field_contract_inventory.json`;
- changed active Google Sheets headers from `publication_details_raw` to
  `raw_publication_details`; imports still accept `publication_details_raw` as
  a temporary compatibility alias;
- changed parser candidate JSON to write `source.raw_publication_details`, with
  fallback reads for older parser runs using `source.publication_details_raw`;
- added canonical target `Source` provenance fields: `data_source`,
  `first_seen_at`, and `updated_at`;
- exported `sources.data_source`, `sources.first_seen_at`, and
  `sources.updated_at` to `data/bibliobon.sqlite` and `site_contract.json`;
- renamed artifact/contract `issues.publication_details` to
  `issues.raw_publication_details`;
- bumped `site_contract.json` to contract version `0.2.0` and artifact schema
  version `2`;
- expanded active Google Sheets sync to include `Languages`, `Sections`,
  `Tags`, and `WorkTags`, matching the existing import code.

## 2026-06-06.03

Refined provenance semantics and relation timestamps:

- documented `data_source` as free-text source/verification provenance;
- documented `first_seen_at` as the first appearance timestamp in the editor
  database and `updated_at` as the last-change timestamp;
- added `created_at` and `updated_at` to target relation tables
  `SourceAuthor`, `SourceTag`, `ArticlePlacement`, and `SourceGroupItem`;
- updated target conversion to preserve existing source provenance and relation
  timestamps across target resets;
- exported relation timestamp columns to `data/bibliobon.sqlite` and
  `site_contract.json`;
- bumped `site_contract.json` to contract version `0.3.0` and artifact schema
  version `3`;
- added `biblio-admin.test` to the local Django editor `ALLOWED_HOSTS`.

## 2026-06-06.04

Fixed parser raw-field visibility in Google Sheets export:

- Google Sheets `Works` export now reads canonical target
  `Source.raw_publication_details`, `data_source`, `first_seen_at`, and
  `updated_at` when a target source exists;
- Google Sheets import accepts optional `data_source`, `first_seen_at`, and
  `updated_at` columns and writes them back to target `Source`;
- parser apply now updates `Source.updated_at`, sets `Source.data_source` to the
  parser run when safe raw details are filled, and mirrors safe
  `raw_publication_details` / `public_review` additions into the legacy `Work`
  fields used by the editor sheets;
- synchronized the two already applied records from parser run
  `2026-06-06-002` into `Work.publication_details`.

## 2026-06-06.05

Added author merge tooling.

New management command:

```bash
python3 editor/manage.py merge_authors \
  --target author-000001 \
  --source author-000002 \
  --source author-000003 \
  --apply
```

The command has a dry-run mode by default. On apply it creates a SQLite backup,
moves `WorkAuthor` and `SourceAuthor` links from duplicate authors to the target
author, preserves printed forms through existing `name_as_printed`/`source_text`
fields, adds duplicate display/heading/sort names to target `Author.aliases`,
deletes the duplicate author rows, and refreshes target tables.

The `/service/` page now includes the same author merge action with separate
check/apply buttons.

Google Sheets note: `WorkAuthors` import remains a full relation replacement.
Deleting a row from the `WorkAuthors` sheet and importing removes that author
link from the editor database.

Checks:

- `python3 editor/manage.py check` passes;
- `python3 editor/manage.py merge_authors --target author-013209 --source author-013210` dry-run succeeds;
- Django test client returns `200` for `/service/`.

## 2026-06-07.01

Added quick-create editor page at `/create/`.

The page creates minimal records for:

- `Author`: display name only;
- `Work` as book: title plus optional first author link;
- `Work` as collection/container: title plus optional first author/editor link;
- `Journal`: title only;
- `JournalIssue`: existing journal, year, and issue number.

After creation the editor redirects to the corresponding Django admin change
page for full editing. Quick creation updates editor tables only and does not
force an immediate target-table rebuild.

Removed the separate `/google-sheets/` route and top-menu link. Google Sheets
actions remain available from `/service/`.

Checks:

- `python3 editor/manage.py check` passes;
- Django test client returns `200` for `/create/`;
- Django test client returns `404` for `/google-sheets/`.

## 2026-06-15.01

Added a new editor import workflow MVP alongside the existing parser staging
workflow.

New editor routes under `/imports/` support:

- creating an import batch from pasted text or a `.txt` file;
- parsing raw bibliography lines into `ImportItem` rows;
- building deduplicated `ImportEntity` rows for authors, books, articles,
  journals, journal issues, and collections;
- building group-level review queues for journal issues, collections, and
  standalone books;
- recording editor decisions to create or link entities;
- showing an import plan before apply;
- applying reviewed creates in dependency order for authors, journals, journal
  issues, collections, books, articles, and author links.

The old `/parser/` workflow remains unchanged. The new workflow writes nothing
to existing bibliography tables until the explicit import apply action. Applying
the migration to `data/editor.sqlite` was preceded by backup:

```text
data/backups/editor.before-import-workflow.20260615-121247.sqlite
```

Checks:

- `python3 -m py_compile editor/sources/import_workflow.py editor/sources/models.py editor/sources/views.py` passes;
- `python3 editor/manage.py makemigrations --check --dry-run` reports no changes;
- `python3 editor/manage.py test sources` passes;
- `python3 editor/manage.py check` passes;
- Django test client returns `200` for `/imports/`, `/imports/new/`, import
  detail, review, and plan pages.

## 2026-06-15.02

Changed MVP import review logic so existing records remain visible and `//`
rows do not automatically create article/container duplicates.

Key behavior:

- every processed raw line remains an `ImportItem` and is shown in the import
  review page;
- `ImportItem` now stores `matched_existing_type`, `matched_existing_id`, and
  `comparison_json`;
- new item statuses distinguish existing records with no changes, existing
  records with possible supplements, and structural conflicts;
- parser searches an existing work by core `author + title + year` before
  creating article/container entities;
- if a `//` source row matches an existing work, it is marked as
  `structural_conflict` instead of creating a new article and collection;
- parent collection parsing stores title, place, year, and raw parent
  description separately, so `Сб. стат. сведений о России. — СПб., 1854.` is
  parsed as title `Сб. стат. сведений о России`, place `СПб.`, year `1854`;
- review UI shows all processed lines, status labels, existing work links, and
  a field comparison table.

Migration backup:

```text
data/backups/editor.before-import-item-review-state.20260615-164500.sqlite
```

Smoke check against the current editor database for the Lamansky three-line
fixture produced:

- `structural_conflict` -> `work-043743`;
- `found_existing_no_changes` -> `work-043744`;
- `found_existing_no_changes` -> `work-043745`;
- no new import entities proposed.

Checks:

- `python3 editor/manage.py test sources` passes;
- `python3 editor/manage.py check` passes;
- Django test client returns `200` for review, item detail, and plan pages for
  the Lamansky fixture.

## 2026-06-20.01

Normalized contributor-role vocabulary without adding new person tables.

Design decision:

- keep all people in `Author`;
- store responsibility role on `WorkAuthor.role` and `SourceAuthor.role`;
- preserve printed responsibility text in `responsibility_statement`,
  `source_text`, and `name_as_printed`;
- use canonical role values:
  `author`, `editor`, `responsible_editor`, `translator`, `compiler`,
  `commentator`, `illustrator`, `organization`, `other`.

The import parser now preserves `responsibility_statement` and can add
`responsibility_contributors` role candidates for patterns such as
`Под общ. ред. ...`; these candidates do not create or link people without
editor confirmation.

Also fixed import comparison and parser edge cases found in import batch 32:

- `Размер` comparison now reads `Work.dimensions`;
- edition statements before imprint and before `/ responsibility` are parsed as
  `edition_statement`;
- publication place, publisher, and year continue to parse after edition
  segments.

## 2026-06-21.01

Added site artifact export to the editor service page.

The `/service/` page now includes an "Экспорт для основного сайта" action that
runs:

```bash
python3 editor/manage.py export_site_artifact
```

It rebuilds target tables by default and writes:

```text
data/bibliobon.sqlite
data/site_contract.json
data/build_manifest.json
```

Checks:

- `python3 editor/manage.py check` passes;
- Django test client returns `200` for `/service/`;
- the rendered service page contains the `export_site_artifact` action.

## 2026-06-22.01

Clarified the future public-site mapping for editorial update timestamps.

The canonical/editor field remains:

```text
sources.updated_at
```

This timestamp means "when the bibliographic record was last changed in the
editorial data layer". The public Django site should not import it into the
technical model field `updated_at`, because that field is maintained by Django
and reflects when the site database row was last saved/imported.

Future site contract/importer work should expose it through a separate
public-site field, for example:

```text
catalog_work.bibliography_updated_at
```

Updated:

- `docs/FIELD_CONTRACT.md`;
- `docs/DATA_MODEL.md`.

## 2026-06-27.01

Architectural decision: `Source` is now the canonical active bibliography record
model. `Work` remains only as a legacy compatibility layer while older tools are
being migrated.

Rules from this point forward:

- add new bibliographic fields to `Source`, not `Work`;
- build new editor screens and workflows source-first;
- migrate import matching/apply behavior from `Work` to `Source`;
- use compatibility sync only where required by older pages;
- do not duplicate new long-term fields between `Work` and `Source`;
- remove/archive `Work` only after import, editor UI, Google Sheets sync,
  diagnostics, and export all operate on `Source`.

Planned citation fields for `Source`:

```text
citation_gost_2018_full
citation_gost_2003_short
citation_host_full
citation_host_short
citation_status
citation_note
```

These stored citation variants will become the preferred public display strings
once reviewed. Structured fields remain necessary for search, relations,
imports, matching, diagnostics, and future regeneration.

## 2026-06-25.01

Added `record_subtype` for standalone dissertation records.

Model changes:

- added `Work.record_subtype`;
- added `Source.record_subtype`;
- current supported value is `dissertation`, covering both dissertations and
  dissertation abstracts;
- structural type is unchanged: dissertations remain
  `Work.work_type=book` and `Source.source_type=monograph`;
- existing records default to blank subtype and were not mass-classified.

Import workflow:

- obvious standalone dissertation/abstract records containing markers such as
  `дис.`, `дисс.`, `диссертация`, `автореф.`, `автореферат`, or degree markers
  are classified as `record_subtype=dissertation`;
- article records with `//` are not reclassified as dissertations by this
  marker pass.

Export contract:

- bumped `site_contract.json` to contract version `0.5.0` and artifact schema
  version `5`;
- `sources` now exports `record_subtype`.

Backups created:

```text
data/backups/editor.before-record-subtype-dissertation.20260625-165125.sqlite
data/backups/editor.before-target-conversion.20260625-135243.sqlite
data/backups/bibliobon.before-site-artifact-export.20260625-135254.sqlite
data/backups/site_contract.before-site-artifact-export.20260625-135254.json
data/backups/build_manifest.before-site-artifact-export.20260625-135254.json
```

Checks:

- `python3 editor/manage.py test sources.tests` passes;
- `python3 editor/manage.py check` passes;
- `python3 editor/manage.py makemigrations --check --dry-run` reports no
  changes;
- `python3 editor/manage.py export_site_artifact` writes the updated artifact.

## 2026-06-24.01

Introduced a backward-compatible newspaper placement model in the editor data
layer and site artifact contract.

Model changes:

- added `Periodical.kind` with values:
  `journal`, `newspaper`, `bulletin`, `almanac`, `other`;
- made `ArticlePlacement.issue` nullable;
- added `ArticlePlacement.periodical` for direct placement under a serial title;
- added placement-level `year`, `publication_date`, and `issue_number`;
- added model validation that each placement has at least one of `issue` or
  `periodical`, and that `periodical` matches `issue.periodical` when both are
  present.

Data backfill:

- existing issue-based article placements keep their `issue_id`;
- placements whose issue has a periodical now also have
  `article_placements.periodical_id`;
- `Тамбовские губернские ведомости`
  (`source_django_id=2425`, `periodical_id=journal-002425`) is marked as
  `kind=newspaper`;
- existing six placements for that newspaper remain issue-based and also have
  the direct periodical link.

Export contract changes:

- bumped `site_contract.json` to contract version `0.4.0` and artifact schema
  version `4`;
- `periodicals` now exports `kind`;
- `article_placements` now exports `periodical_id`, nullable `issue_id`, `year`,
  `publication_date`, and `issue_number`.

Semantics:

- journal article placement: `periodical_id + issue_id`;
- newspaper article without stored issue:
  `periodical_id`, `issue_id = NULL`, placement-level
  `year/publication_date/issue_number/pages_raw`;
- current public-site importer can continue using `issue_id` until it is updated
  for the `0.4.0` contract.

Backups created:

```text
data/backups/editor.before-newspaper-placement-model.20260624-005300.sqlite
data/backups/editor.before-target-conversion.20260623-215540.sqlite
data/backups/bibliobon.before-site-artifact-export.20260623-215550.sqlite
data/backups/site_contract.before-site-artifact-export.20260623-215550.json
data/backups/build_manifest.before-site-artifact-export.20260623-215550.json
```

Checks:

- `python3 editor/manage.py test sources.tests` passes;
- `python3 editor/manage.py check` passes;
- `python3 editor/manage.py makemigrations --check --dry-run` reports no
  changes;
- `python3 editor/manage.py export_site_artifact` writes the updated artifact.

## 2026-06-23.01

Rebuilt the public-site export artifact after applying the editor-approved tag
tree.

Generated:

```text
data/bibliobon.sqlite
data/site_contract.json
data/build_manifest.json
```

Current exported tag counts:

```text
tags: 310
tags with parent_id: 246
source_tags: 3000
```

Contract decision:

- `tags.parent_id` is now an active navigation tree for tag browsing, not merely
  a possible future hierarchy field;
- top-level navigation roots currently include `География`, `Типы`, and `Темы`
  in the exported data; `Исторический период` remains an accepted root for the
  tag-tree workflow and will appear when approved/applied rows require it;
- parent/root tags may be navigational nodes without direct source assignments.

Backups created by export:

```text
data/backups/editor.before-target-conversion.20260623-140409.sqlite
data/backups/bibliobon.before-site-artifact-export.20260623-140419.sqlite
data/backups/site_contract.before-site-artifact-export.20260623-140419.json
data/backups/build_manifest.before-site-artifact-export.20260623-140419.json
```

Updated:

- `docs/FIELD_CONTRACT.md`;
- `docs/DATA_MODEL.md`.
