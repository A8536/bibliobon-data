# Migration Strategy

This document separates two independent work tracks:

1. maintain the correct target data structure;
2. edit and migrate the current legacy data toward that structure.

The public Django site should not be changed again until the data model, migration
tools, and export contract are stable enough.

## Track 1: Target Structure

This track defines and protects the canonical data model.

Tasks:

- keep `docs/DATA_MODEL.md` as the target model specification;
- implement editor models with target names and target relationships;
- add validations for model invariants;
- import new data directly into the target model;
- generate `data/bibliobon.sqlite`;
- generate `data/site_contract.json`;
- generate `data/build_manifest.json`;
- later update the public site importer to consume only the generated export.

This track should not bend the model to legacy errors.

## Track 2: Legacy Cleanup

This track handles the current data inherited from the public Django site and
Google Sheets.

Tasks:

- bootstrap legacy data into compatibility snapshots;
- map legacy rows into target entities;
- report ambiguous or suspicious structures;
- provide editor workflows for manual decisions;
- apply controlled transformations with preview/dry-run;
- record durable data-contract decisions in `DATA_CHANGELOG.md`.

Legacy cleanup can be iterative and imperfect. Target model rules should remain
stable while cleanup tools improve.

## Migration Phases

### Phase 1: Freeze Target Vocabulary

Use these target names:

```text
Source
Author / SourceAuthor
Section
Tag / SourceTag
Periodical
Issue
ArticlePlacement
SourceGroup / SourceGroupItem
```

Do not introduce new active `Collection` logic. Collection-like data maps to
`Issue`, with optional `Issue.source_id`.

### Phase 2: Build Target Editor Schema

The current editor MVP still uses compatibility names in places. Refactor it to
the target model before adding more workflows.

Deliverables:

- target Django models;
- structured ГОСТ-ready fields on source, periodical, issue, author, and article placement entities;
- migrations;
- admin configuration;
- import from bootstrap JSONL into target models;
- basic list/detail pages.

### Phase 3: Validation Reports

Add repeatable reports for:

- article sources without `ArticlePlacement`;
- article placements without an `Issue`;
- issues with both missing `periodical_id` and missing `source_id`;
- periodicals with no issues;
- issues with no article placements and no `source_id`;
- duplicate periodicals by normalized title;
- duplicate issues by periodical/year/number/volume;
- standalone sources that look like articles;
- one-article periodical issues;
- one-article collection issues;
- source records that appear to duplicate an issue source;
- candidate edition groups.
- records with only raw `publication_details` and empty structured publication fields;
- article placements where `pages_raw` exists but `page_start`/`page_end` are missing;
- sources where `physical_description` likely contains parseable `extent`, illustrations, or dimensions;
- authors without `heading_name`;
- responsibility statements missing when name-as-printed data exists.

Reports should be generated under `reports/` and visible in the editor.

### Phase 4: Cleanup Workflows

Add editor tools for:

- standalone source -> journal article placement;
- standalone source -> collection article placement;
- issue source creation/attachment;
- merge duplicate issues;
- merge duplicate periodicals;
- one-article issue -> standalone source;
- group editions as `SourceGroup`;
- group multivolume works as `SourceGroup`;
- resolve raw `publication_details` into structured fields.

Any workflow that merges or deletes relationships must have preview/dry-run.

### Phase 5: New Imports

All new imports should target the canonical model:

- standalone book/source -> `Source`;
- journal article -> `Source + Periodical + Issue + ArticlePlacement`;
- collection article -> `Source + Issue + ArticlePlacement`;
- cited collection/issue -> `Source` attached to `Issue.source_id`;
- multivolume item -> `Source + SourceGroupItem`;
- edition/reprint -> `Source + SourceGroupItem` with `group_type=editions`.

Do not create legacy `Collection`.

Google Sheets editing for the editor is now active through canonical-ID sheets:

```bash
python3 editor/manage.py export_google_sheet
python3 editor/manage.py import_google_sheet --dry-run
python3 editor/manage.py import_google_sheet
```

The import layer uses stable editor IDs, not public-site Django primary keys.
Relation sheets are replace-all, so manual removal of a relation row is preserved
as a deletion on import.

### Phase 5A: ГОСТ Field Normalization

Before reliable renderer implementation, existing data needs normalization into
the structured fields:

- parse title/subtitle/remainder/part data where possible;
- move printed responsibility data into `responsibility_statement`;
- split publication statements into place, publisher, publication date, and normalized year;
- split physical description into extent, illustrations, dimensions, and accompanying material;
- move article page ranges to placement-level `pages_raw`, `page_start`, and `page_end`;
- add author heading forms and name-as-printed relation data;
- mark records with `description_status`.

Current first-pass backfill command:

```bash
python3 editor/manage.py backfill_gost_fields
```

This command performs only obvious non-destructive moves:

- `Author.heading_name` from `sort_name` or `display_name`;
- `WorkAuthor.name_as_printed` from existing source text;
- first author link per work marked as `is_primary_heading`;
- `Work.responsibility_statement` from `responsibility_note`;
- `Work.publication_date` from normalized year;
- `Work.extent` from legacy physical description;
- `JournalIssue.publication_date`, `chronology`, and `enumeration` from existing issue fields;
- `Article.pages_raw`, `page_start`, and `page_end` from placement/work page strings.

Further parsing must be report-driven and reviewed before writes.

## What Is Needed To Convert Existing Data

Existing data cannot be transformed reliably by one blind migration. Required
building blocks:

1. Legacy-to-target mapping.

   Define deterministic mapping from current bootstrap/editor compatibility data
   into target entities:

   ```text
   Work -> Source
   Journal -> Periodical
   JournalIssue -> Issue
   Article -> ArticlePlacement
   container Work / legacy Collection -> Issue with optional Source
   WorkGroup -> SourceGroup
   ```

   Current first-pass command:

   ```bash
   python3 editor/manage.py convert_legacy_to_target
   python3 editor/manage.py convert_legacy_to_target --apply --reset
   ```

   Apply mode backs up `data/editor.sqlite` before writing. The report is stored
   in `reports/target_conversion_report.json`.

2. Structured parsers with confidence levels.

   Parsers should extract:

   - title/subtitle/part statements;
   - responsibility statements;
   - publication place, publisher, date/year;
   - physical extent, illustrations, dimensions;
   - article page ranges;
   - issue enumeration and chronology.

   Each parsed result should carry a confidence/status value and preserve raw
   input.

3. Diagnostics before writes.

   Reports must identify:

   - records that look like articles but have no placement;
   - standalone records that duplicate container sources;
   - one-article containers that may be accidental;
   - duplicate periodicals/issues;
   - issue/container records with missing source data;
   - raw `publication_details` values that cannot be parsed confidently.

4. Manual review queues.

   Ambiguous cases should be queued in the editor, not auto-mutated.

5. Idempotent commands.

   Every migration/normalization command should be safe to rerun and should avoid
   overwriting manually reviewed structured fields unless explicitly requested.

6. Provenance.

   Keep source fields and compatibility IDs until the site export has been
   validated.

7. Site-independent export.

   Only after cleanup and validation should the editor generate
   `data/bibliobon.sqlite` and `data/site_contract.json` for the public site.

## Splitting A Standalone Record Into Article And Container

When a legacy standalone bibliographic record appears to describe an article in
an uncreated journal issue or collection, do not rewrite it in place without a
preview. The safe transformation is:

1. Preserve the original source row.

   Keep `source_django_id`, `source_sequence`, `source_number`, and raw legacy
   fields on the article `Source`. Add a review/status marker instead of deleting
   evidence.

2. Create or match the container first.

   Use normalized title, year, issue/volume/part, place, publisher, and raw
   `publication_details` as matching evidence. If confidence is low, create a
   review candidate rather than a real merge.

3. Move only container-level fields to the container.

   Container candidates include journal/collection title, issue number, volume,
   year/date, place, publisher, ISBN/ISSN, series statement, physical extent of
   the full issue/collection, and notes about the host publication.

4. Keep article-level fields on the article source.

   Article candidates include article title, article authors, responsibility
   statement for the article, article pages, DOI/URL, article notes, section,
   tags, and language.

5. Link through `ArticlePlacement`.

   The placement stores the relationship and location data:

   ```text
   Source(article) -> ArticlePlacement -> Issue(container)
   ```

   Page strings are preserved as `pages_raw` and normalized into
   `page_start`/`page_end` only when parsing is confident.

6. Preserve ambiguity.

   If a field could belong to either the article or the container, keep the raw
   value on the article source and add a review note. The editor can later move
   it manually.

The conversion command should therefore have three modes:

```text
report     identify split candidates without writing
preview    show the proposed article/container/placement rows
apply      write only reviewed or high-confidence transformations, with backup
```

## Cleanup

Unused-data cleanup is available as:

```bash
python3 editor/manage.py cleanup_unused
python3 editor/manage.py cleanup_unused --apply
```

Dry-run mode reports candidates only. Apply mode creates a SQLite backup before
deleting rows.

### Phase 6: Site Export

Only after the editor model and cleanup workflow are stable:

- define `data/site_contract.json`;
- export `data/bibliobon.sqlite`;
- add importer checks in the public site;
- run public site checks/tests.

## Current Boundary

Until this strategy changes, do not modify:

```text
/Users/oleg/Projects/websites/bibliobon-catalog
```

All data-model work should happen in:

```text
/Users/oleg/Projects/data/bibliobon-data
```
