# Bibliobon Data Context

## Purpose

This project is the future canonical data layer for the Bibliobon bibliography catalog. It exists so that bibliography data can be corrected, normalized, validated, and extended outside the Django site codebase.

The Django site should eventually consume a generated export from this project, similar to the relationship between:

- `/Users/oleg/Projects/texts/veterans/` - data/build project;
- `/Users/oleg/Projects/websites/veterans-bio/` - Django publishing site.

## Current State At Creation

Created on 2026-05-25 from the current state and decisions of:

```text
/Users/oleg/Projects/websites/bibliobon-catalog
```

The current working source of truth is the existing Django database plus manual edits in Google Sheets.

Important current files copied for reference:

- `docs/reference/current-site/GOOGLE_SHEETS.md`
- `docs/reference/current-site/TODO.site.md`
- `docs/reference/current-site/DEVELOPMENT.site.md`
- `docs/reference/current-site/AGENTS.md`

These reference files are snapshots. Update active project decisions in this project's own `CONTEXT.md`, `TODO.md`, and `DATA_CHANGELOG.md`.

## Core Principle

The site publishes. This project builds and validates data.

The target workflow is:

```text
editorial inputs
  -> normalized canonical data
  -> validation reports
  -> site export contract
  -> Django import
```

## Editor Site

`bibliobon-data` now contains a separate local Django editor in `editor/`.

Purpose:

- edit and inspect canonical bibliography data outside the public site;
- keep source work, diagnostics, normalization, and future Google Sheets workflows away from production publishing code;
- prepare exports that the public site can consume.

The editor database is:

```text
data/editor.sqlite
```

The initial editor data was imported from the bootstrap JSONL files generated from the current Django site database. The public site database is not used by the editor at runtime.

Current editor scope:

- works list/search;
- sections list;
- journals and container works list;
- journal/detail and collection/detail pages;
- single-article journal/container conversion to one source;
- Django admin for direct edits.

The editor is still missing Google Sheets import/export and final site-export generation.

## Bibliography Parser Staging

The bibliography parser is a separate staging workflow. It reads raw
bibliographic strings from `.txt` or `.jsonl`, creates parse candidates, and
does not write to the editor database.

Current parser entry point:

```bash
python3 scripts/parse_bibliography.py run --input <file.txt|file.jsonl> --batch <batch>
```

Staging layout:

- `source/incoming/<batch>/original/` - received source files;
- `source/incoming/<batch>/extracted/` - extracted plain text;
- `source/normalized_text/<batch>.jsonl` - one normalized raw record per line;
- `data/parser_runs/<run_id>/` - parser outputs.

Each run currently writes `run_manifest.json`, `raw_records.jsonl`,
`parsed_candidates.jsonl`, `parser_warnings.tsv`, and `parser.sqlite`.

The next workflow steps are intentionally separate:

- compare parser candidates with `data/editor.sqlite`;
- create match/proposed-change/conflict/new-record reports and `merge_plan.json`;
- apply only reviewed safe changes after backing up `data/editor.sqlite`.

## Target Model Decision

The target canonical model is now documented in:

```text
docs/DATA_MODEL.md
```

Accepted active vocabulary:

- `Source`
- `Author` / `SourceAuthor`
- `Section`
- `Tag` / `SourceTag`
- `Periodical`
- `Issue`
- `ArticlePlacement`
- `SourceGroup` / `SourceGroupItem`

Key decisions:

- `Source` is the citeable bibliographic unit.
- `Periodical` is a continuing serial identity and is not itself a `Source`.
- `Issue` is the common container for journal issues, annual issues, collections, and concrete volumes.
- `Issue.source_id` is optional and points to a `Source` only when the container has its own bibliographic record.
- `ArticlePlacement` links an article `Source` to an `Issue`.
- `Collection` is not part of the target active model.
- One-article containers should remain structured if the record is truly an article in a container.
- Accidental one-article journal/collection structures should be convertible back to a standalone `Source`.

Migration and cleanup strategy is documented in:

```text
docs/MIGRATION_STRATEGY.md
```

Current boundary: do not change the public `bibliobon-catalog` site again until the data model, cleanup tools, and export contract are stable.

## Active Data Sources

1. Current Django SQLite database:

```text
/Users/oleg/Projects/websites/bibliobon-catalog/app/db.sqlite3
```

2. Current working Google Sheets document:

```text
1zYFOqM-wBT6mFYLmblaGRBlV0rJeWAaLrEsCMJYKHTw
```

3. Historical Excel import is legacy:

```text
/Users/oleg/Projects/websites/bibliobon-catalog/design/Библиография.xlsx
```

The Excel file may be useful for historical diagnostics, but it should not become the active editing source again.

## Current Django Entities

- `Work` - book, article, issue/volume, collection-like container, or unknown bibliographic item.
- `Author` - normalized author/person/corporate author display entity.
- `WorkAuthor` - ordered author-to-work relation.
- `Section` - printed source-book hierarchy.
- `Tag` and `WorkTag` - index terms and work-tag links.
- `Journal` - periodical title.
- `JournalIssue` - specific journal issue.
- `Article` - article-to-container relation:
  - `journal_issue` for journal articles;
  - `container_work` for articles in collections/books.
- `WorkGroup` and `WorkGroupItem` - related editions, multivolume sets, annuals, series-like groups.
- `Collection` - legacy table to phase out. New container work should use `Work` plus `Article.container_work`.

## Preferred Bibliographic Structure

### Books

A book is a `Work`-like record with structured bibliographic fields, authors through a relation table, source section, and tags.

### Articles

An article is also a `Work`-like record. It should link to exactly one normalized container:

- a `JournalIssue`; or
- a container `Work`.

Article citation output must combine article data and container data.

### Journals

- `Journal` is the continuing periodical title.
- `JournalIssue` is a concrete issue, with year, number, volume, date text, and publication details.
- Articles should link to issues, not directly to journals.

### Collections

Preferred current approach:

- store a collection as a normal `Work`;
- link collection articles through `Article.container_work`;
- do not use legacy `Collection` for new data;
- use `WorkGroup` for multivolume or annual collection structures.

### Multivolume Works

- Use `WorkGroup` for the group.
- Use `WorkGroupItem` for member works.
- Use `Work.volume_number` for volume/issue number on the concrete work.
- Articles should link to the concrete volume/work, not only to the group.

## Important Work Fields

- `source_sequence` - historical row order from the original import.
- `source_number` - printed bibliography number. Show publicly only for historical records.
- `title`, `subtitle`, `responsibility_note`.
- `host_title` - legacy/raw container text; prefer normalized `Article` container links.
- `publication_place`, `publisher`, `inferred_year`, `physical_description`.
- `publication_details` - raw/legacy statement for verification; gradually move data into structured fields.
- `public_review` - public comment/annotation.
- `article_pages` - page range for the article itself.

## Stable IDs

The new data project should introduce stable IDs independent of Django primary keys:

- `work_id`
- `author_id`
- `section_id`
- `tag_id`
- `journal_id`
- `journal_issue_id`
- `group_id`

During migration, keep old Django IDs as compatibility fields, for example `source_django_id`, but do not make them the long-term source of truth.

### Initial Bootstrap ID Scheme

`scripts/bootstrap_from_site_db.py` creates deterministic compatibility IDs from the current Django primary keys:

- `work-000001`
- `author-000001`
- `section-000001`
- `tag-000001`
- `journal-000001`
- `journal-issue-000001`
- `group-000001`

These IDs are the first bootstrap layer, not a promise that Django PK-derived IDs will remain the final curated canonical IDs forever. Any future replacement with human-curated stable IDs should be done as an explicit data migration and recorded in `DATA_CHANGELOG.md`.

The bootstrap also exports compatibility-only IDs for tables that are needed to preserve the full current site state but are not primary target entities yet: `language_id`, `book_id`, `article_id`, and `collection_id`.

## Google Sheets

Google Sheets remains useful as an editorial interface. The target direction is:

- Sheets can be imported into this data project.
- This project validates and normalizes the data.
- The Django site imports only generated, contract-checked data.
- Do not export to Google Sheets unless the user explicitly asks for export.

Important current sheets:

- `Works`
- `Authors`
- `WorkAuthors`
- `Sections`
- `Tags`
- `WorkTags`
- `Journals`
- `JournalIssues`
- `ArticleContainers`
- `WorkGroups`
- `WorkGroupItems`

If a user removes an author, tag, or container relation in a relation sheet, import must remove that relation in the canonical data.

`Works.container_index` is an editable placement helper:

- `container:<work_id>` means the work is an article/part inside that container
  work;
- `issue:<journal_issue_id>` means the work is an article inside that journal
  issue;
- blank means no container placement.

On import, `Works.container_index` is applied after `ArticlePlacements`, so it
is the final placement value when the two disagree.

`Works.page_start` and `Works.page_end` are the editable structured page bounds
for article records. `ArticlePlacements` no longer exports `page_start`,
`page_end`, `container_work_id`, or `container_work_title`; container placement
is edited through `Works.container_index`.

## Legacy Collection Policy

Legacy `Collection` rows are migration compatibility data only. New editor
workflows should not create new `Collection` rows.

Current target direction:

- articles in books/collections use `Article.container_work`;
- collection-like containers are regular `Work` records with
  `work_type=container`;
- `is_container=True` is kept only as temporary compatibility with earlier
  editor migrations and should not be used as the long-term canonical flag;
- target `Issue(issue_type=collection)` is generated from those container
  works;
- current active `Article.collection` links are zero;
- legacy `Collection` rows were removed from `data/editor.sqlite` on
  2026-05-26 with backup; new workflows should not depend on that table.

## Safety Rules

- Do not deploy.
- Do not overwrite the site database.
- Make backups before mass import or mass rewrite.
- Preserve manual Google Sheets edits.
- Keep diagnostics before destructive cleanup.

## Bibliography Parser Workflow

The editor site owns parser intake so editors do not manually create incoming
folders or filenames.

Current intake flow:

- `/parser/` accepts pasted bibliography text or an uploaded `.txt` file;
- the site creates or reuses `source/incoming/<batch>/`;
- submitted originals are archived under `source/incoming/<batch>/original/`;
- `source/incoming/<batch>/work/current.txt` is the mutable working copy;
- new submitted text is appended to the working copy and the previous copy is
  saved under `source/incoming/<batch>/work/history/`;
- the editor intake form can clear the current working copy before adding new
  text; the previous copy is still backed up under `work/history/`;
- each parse creates a staging run under `data/parser_runs/<run_id>/`;
- parser comparison reads `data/editor.sqlite` but does not write to it.

The review UI is staged:

- stage 1 checks full and partial matches against existing records;
- stage 2 edits unresolved or manually selected records;
- stage 3 summarizes the current file before a later explicit apply step.
- review decisions are persisted per parser run in
  `data/parser_runs/<run_id>/review_state.json`;
- stage 1 writes manual split/keep decisions to that state file;
- stage 2 writes accepted modal edits and rollback actions to that state file
  while still using browser storage as a UI fallback.

For records with `//`, parser staging creates independent candidates for the
article and the container. They share the same raw source record, but have
different `candidate_id` values and `candidate_part=article` or
`candidate_part=container`.

Parser comparison now has a separate container-resolution layer for article
containers:

- `container_resolution.tsv` and `container_resolution.json` are written under
  each parser run;
- periodical containers are matched in two steps: first `Periodical` by title
  or ISSN, then `Issue` by year, issue number, volume, part number, and date;
- collection-like containers are matched against existing `Source` rows with
  `source_type=collection` or `source_type=issue`;
- ready actions such as `link_existing_issue` mean the journal and issue
  already exist; creation actions such as `confirm_create_issue` or
  `confirm_create_periodical` still require explicit editor confirmation.

Container review is now stage 0:

- new parser runs open `review_containers.html` first;
- stage 0 decisions are persisted in `review_state.json` under `stage0`;
- the following record-matching page links back to stage 0 and shows article
  container bindings inline, for example `Контейнер: Вопросы истории, 2015,
  № 6 -> journal-issue-011386`;
- current buttons confirm the suggested action, and "choose" actions open a
  searchable local list of candidate journals, issues, or collection containers;
- stage 0 groups repeated containers for display: one periodical is shown once,
  and each unique issue under that periodical is shown once even when several
  source articles point to it;
- generated container candidates (`candidate_part=container`) are not shown as
  ordinary records on stage 1; stage 1 reviews books/articles and shows article
  container bindings inline;
- the parser intake page has a debug cleanup action that deletes parser staging
  data under `source/incoming/`, `source/normalized_text/`, and
  `data/parser_runs/` without touching `data/editor.sqlite`.
- after debug cleanup, the parser intake page also clears browser-side
  `bibliobon-parser-*` storage, and stage 2 now restores edits only from
  server-side `review_state.json`.

Optional AI-assisted preprocessing is a separate staging layer:

- prompt version lives in `docs/prompts/bibliography_ai_markup_ru.md`;
- `scripts/ai_markup_bibliography.py` reads `.txt` or `.jsonl` records and
  writes `source/incoming/<batch>/ai_markup/<timestamp>.jsonl`;
- real AI calls require `OPENAI_API_KEY`; `--mock` is only for technical tests;
- `scripts/parse_bibliography.py run` can read this JSONL and use the
  `ai_markup` object to create parser candidates;
- the editor `/parser/` page exposes this as "Использовать ИИ для
  предварительной разметки";
- AI markup never writes to `data/editor.sqlite`.

Fully resolved records should later be removed from the working copy, while
unresolved records stay in `work/current.txt` so they can be reparsed in a later
session. The apply step must remain separate and must create a backup before
writing to `data/editor.sqlite`.

Parser comparison treats missing source raw text as a safe supplement only when
the existing editor row has an empty `raw_publication_details` field. In that
case stage 3 shows the matched record under `Данные дополнены` with the label
`raw-запись источника`, so the editor can confirm adding the archival raw
bibliographic line. If the editor row already has non-empty raw publication
details, parser raw-text differences remain diagnostic-only and are not proposed
for automatic replacement.

The stage 3 apply endpoint currently applies only those safe
`raw_publication_details` supplements. It creates a SQLite backup only when
there are new writes, writes the accepted empty-field fills into
`data/editor.sqlite`, records `apply_request.json`, and injects a visible
apply-status block when `review_stage3.html` is reopened. Repeated clicks are
reported as already applied instead of generic skips. Applied or already-applied
candidate IDs are then treated as resolved and excluded when parser review
reports are regenerated. New record creation remains a later reviewed apply
step.

Parser review pages rely on `data/parser_runs/<run_id>/review_state.json` for
editor decisions. The Django parser pages now force a CSRF cookie for static
review HTML so stage 0 container confirmations and stage 2 modal edits can be
saved through `/parser/runs/<run_id>/state/`. The state endpoint is still
staff-only but is CSRF-exempt to avoid stale-cookie failures on static staging
pages during debug sessions. Stage 0 reloads saved decisions and highlights
confirmed issues/containers. Stage 3 injects saved stage 2 edits as a visible
review block; applying arbitrary stage 2 field edits to the database is still a
later reviewed apply step.

Stage 0 container groups display up to two source bibliography records from
`raw_records.jsonl` under `Источник:` so the editor can verify a journal issue
or collection against the original line without opening the input file.

If a parser run has no rows in `container_resolution.tsv`, the editor intake
opens the first record-matching stage directly and skips the empty container
review page.

Trailing text in `{...}` in raw bibliography is treated as an annotation and
stored in candidate `source.public_review`. It is excluded from parsed title,
publication, and extent fields, while the full raw line is still available for
`raw_publication_details`.

`review_stage2.html` is generated from the current parser run during compare.
It must not remain a copied prototype page: each row carries stable
`candidate_id`, `editor_source_id`, and `stage2:<candidate>:<editor>` review IDs
so saved modal edits can be traced into stage 3.

Google Sheets uses the canonical header `raw_publication_details` in `Works`,
`JournalIssues`, and `ContainerWorks`. Imports still accept the legacy
`publication_details_raw` header as a temporary compatibility alias, but new
exports must use `raw_publication_details`. In the compatibility editor models,
some legacy storage fields are still named `publication_details`; these map to
target/export `raw_publication_details`.

`docs/FIELD_CONTRACT.md` is the active field dictionary across parser, editor
models, Google Sheets, `site_contract.json`, and `data/bibliobon.sqlite`. Run
`python3 scripts/audit_field_contract.py` after any field/schema change. A clean
audit writes `reports/field_contract_audit.md`,
`reports/field_contract_audit.tsv`, and
`reports/field_contract_inventory.json` with zero findings.

Canonical provenance semantics:

- `data_source` is free text describing where a source record came from or how
  it was verified, for example `Баранов 2021`, `ручное добавление`,
  `Google Sheets`, `сообщение пользователя`, `проверено по РГБ`;
- `first_seen_at` is when the entity first appeared in the editor database;
- `updated_at` is when the record was last changed.

Target relation tables `SourceAuthor`, `SourceTag`, `ArticlePlacement`, and
`SourceGroupItem` have relation-level `created_at` and `updated_at`, because the
existence/order of a relation can be a meaningful bibliographic edit. Do not add
relation-level `data_source` unless a workflow really needs provenance for the
link itself.

Parser compare now creates an author staging step:
`author_resolution.tsv`, `author_resolution.json`, and `review_authors.html`.
The page groups repeated parsed authors, shows up to two source bibliography
examples, and lets the editor confirm an existing author or mark a new author to
create. Author decisions are stored in `review_state.json` under
`stage_authors`. This is staging-only; applying new author rows and links to
`data/editor.sqlite` is still a separate reviewed apply step.

## New Import Workflow MVP

The editor now has a second, cleaner import workflow under `/imports/`. It is
separate from the older `/parser/` staging pages, which are preserved for
debugging and comparison.

The new workflow stores import state in editor tables:

- `ImportBatch` is the uploaded or pasted source package;
- `ImportItem` is one raw bibliographic line and its parsed fields;
- `ImportEntity` is a proposed author, work, article, journal, issue, or
  collection inside the import;
- `ImportEntityRelation` stores the draft graph, for example journal -> issue
  -> article and author -> article;
- `ImportGroup` groups repeated parent entities so one journal issue or
  collection is reviewed once for all related articles;
- `ImportMatch` stores candidate matches to existing editor entities;
- `ImportDecision` stores editor choices;
- `ImportApplyLog` stores what happened when the import was applied.

Current MVP behavior:

- raw pasted text and `.txt` files can create an import;
- simple regex/heuristic parsing recognizes books, journal articles, and
  collection articles;
- every processed raw line remains visible as an `ImportItem`, including records
  that are already found in the editor database;
- existing records are not silently ignored: `ImportItem` stores the matched
  existing work id and a comparison table;
- rows with `//` first search the work core by author, title, and year before
  creating parent container entities;
- if a `//` row matches an existing work but the source describes it as part of
  a collection or issue, the item is marked as `structural_conflict` and no
  article/container duplicate is proposed automatically;
- parent title/place/year are parsed separately from the right side of `//`, so
  output data such as `СПб., 1854` does not become part of the collection title;
- repeated journal/issue/container/author/book/article entities inside one
  import are deduplicated by normalized keys;
- match lookup checks existing authors, journals, journal issues, collections,
  and works with a `0.7` similarity floor;
- group review pages let the editor mark an entity as new or link it to an
  existing match;
- the plan page blocks apply while required decisions remain unresolved;
- apply creates reviewed new authors, journals, journal issues, collections,
  books, articles, and author links in one transaction.

Known MVP limits:

- parser quality is intentionally basic and should be replaced or augmented by
  the AI-markup parser layer later;
- update-existing and merge decisions are recorded conceptually but not yet
  applied to existing bibliography rows;
- group split/move UI is not implemented yet;
- comparison UI is still compact and does not yet show field-by-field conflict
  choices.

## Useful Diagnostics

Before mass changes, produce reports for:

- counts of works, authors, journals, issues, article links;
- articles without containers;
- articles with both `journal_issue` and `container_work`;
- articles with `host_title` but no normalized container;
- empty journals and empty journal issues;
- duplicate journal issues by `(journal_id, year, issue_number, volume)`;
- duplicate/similar authors;
- publication details duplicated between article and container;
- legacy `Collection` still used by article links;
- source records without stable generated IDs;
- new records without historical `source_sequence`.

## Target Site Contract

This project should eventually write:

- `data/bibliobon.sqlite`
- `data/site_contract.json`
- `data/build_manifest.json`

The Django site should check `site_contract.json` before import.

## Contributor Roles

Bibliographic responsibility roles are normalized on the relation between a
record and a person, not by creating separate editor/translator tables.

Active model fields:

- legacy layer: `WorkAuthor.role`;
- target layer: `SourceAuthor.role`;
- printed forms remain in `source_text` and `name_as_printed`;
- `include_in_responsibility` and `is_primary_heading` keep citation/display
  semantics separate from the role itself.

Canonical role vocabulary:

- `author` - автор;
- `editor` - редактор;
- `responsible_editor` - ответственный редактор;
- `translator` - переводчик;
- `compiler` - составитель;
- `commentator` - комментатор;
- `illustrator` - художник / иллюстратор;
- `organization` - организация;
- `other` - другая роль.

The import parser preserves the full `responsibility_statement` and may also
create non-applying role candidates in parsed JSON, for example
`responsibility_contributors`, so the editor can later confirm people and roles
without losing the original responsibility text.
