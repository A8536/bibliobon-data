# TODO

## Bootstrap

- [x] Create `scripts/bootstrap_from_site_db.py` to export current Django SQLite data into this project.
- [x] Generate initial JSONL files under `data/` for works, authors, links, journals, article containers, sections, tags, and work groups.
- [x] Preserve current Django IDs as `source_django_id` compatibility fields.
- [x] Introduce stable data-project IDs for works, authors, journals, issues, tags, sections, and groups.
- [x] Create an initial `data/build_manifest.json`.

## Target Model

- [x] Document target canonical model in `docs/DATA_MODEL.md`.
- [x] Document migration/cleanup strategy in `docs/MIGRATION_STRATEGY.md`.
- [x] Add ГОСТ-ready structured fields to editor compatibility models.
- [x] Add first-pass backfill command for obvious ГОСТ field mappings.
- [x] Add target `Source`/`Periodical`/`Issue`/`ArticlePlacement` tables alongside compatibility models.
- [x] Add first-pass legacy-to-target conversion command.
- [ ] Refactor editor models from compatibility names to target names.
- [x] Import current editor data into target `Source`/`Issue`/`Periodical`/`ArticlePlacement` models.
- [ ] Add model invariant validations.
- [ ] Implement citation renderer profiles: `gost_full`, `gost_short`, `site_public`, `editor_debug`, `raw_legacy`.
- [ ] Add report-driven parsers for structured ГОСТ fields from existing raw/legacy data.
- [ ] Add manual review queues for ambiguous parser results.
- [ ] Support one article/source with multiple placements in several journal issues: change target `ArticlePlacement.source` from one-to-one to many-to-one, add ordering, parse issue lists from legacy `publication_details`/`volume_number`, create missing issues, and review examples such as `work-047830` and `work-047820`.

## Bibliography Parser

- [x] Add staging directories for incoming source batches, normalized text, and parser runs.
- [x] Define first `raw_records.jsonl` format for parser runs.
- [x] Define first `parsed_candidates.jsonl` format for parser runs.
- [x] Add `scripts/parse_bibliography.py` CLI for `.txt` and `.jsonl` input.
- [x] Write `parser_warnings.tsv` for unrecognized or doubtful fragments.
- [x] Keep parser runs separate from `data/editor.sqlite`.
- [x] Add separate candidate-to-editor comparison command producing `match_candidates.tsv`, `proposed_changes.tsv`, `conflicts.tsv`, `new_records.tsv`, and `merge_plan.json`.
- [x] Add editor-site intake page that archives submitted source text, appends to a batch working copy, and launches parser/compare staging runs.
- [x] Add debug intake option to clear a batch working copy before adding new text, while preserving a history backup.
- [x] Add static review pages for stage 1 matches, stage 2 unresolved-record editing, and stage 3 run summary.
- [x] Add first AI-assisted markup CLI and prompt, writing staging JSONL under `source/incoming/<batch>/ai_markup/`.
- [x] Let parser runs consume `ai_markup` JSONL objects when present.
- [x] Add editor-site checkbox/action for launching AI markup before parser run.
- [ ] Run real AI markup on the 20-record fixture and compare quality against the algorithmic parser.
- [x] Split `article // container` source rows into independent article and container candidates in parser staging.
- [x] Lower review-page match display threshold to `0.7` and show diagnostic differences outside displayed citation fields.
- [x] Preserve short title hyphen patterns such as `Гривне - год` before splitting subtitle by sentence.
- [x] Move static stage 2/3 review pages to `data/parser_review_templates/` so parser history can be cleared safely.
- [x] Add "Все поля" expander to the stage 2 modal for non-service fields.
- [x] Include periodical and issue tables in parser comparison so container candidates can match existing journals/collections.
- [x] Add separate container-resolution staging for parsed article containers, producing `container_resolution.tsv`/`.json` and a review section for journals, issues, and collection containers.
- [x] Add parser review stage 0 page for confirming article containers before record matching.
- [x] Group repeated stage 0 periodicals/issues so the same journal and same issue are confirmed once per parser run.
- [x] Exclude parser-generated container candidates from stage 1 record matching; they are handled only by stage 0 container resolution.
- [x] Show confident weak matches as paired review rows instead of new records when a probable database row exists.
- [x] Add two stage 3 apply choices: changed-only and changed-plus-new.
- [x] Wire stage 3 apply buttons to a safe staging apply request that creates a backup and records `apply_request.json` without modifying editor data.
- [ ] Implement real parser apply for changed-only and changed-plus-new modes from persisted review state.
- [x] Persist stage 1 decisions and stage 2 edits server-side in `data/parser_runs/<run_id>/review_state.json` while keeping browser storage as a UI fallback.
- [ ] Make stage 2 and stage 3 pages fully data-driven for every parser run instead of copying the current prototype pages.
- [ ] Use persisted stage-review decisions to remove accepted records from `source/incoming/<batch>/work/current.txt`.
- [x] Expand stage 0 controls from simple confirmation buttons to searchable selectors for choosing existing journal/issue/container before creating new ones.
- [x] Add parser intake debug button for full staging cleanup without touching `data/editor.sqlite`.
- [x] Clear parser browser storage after full staging cleanup and make stage 2 restore edits from server state only.
- [x] Generate stage 3 apply-preview groups: new books, supplemented records, journals, and collections.
- [x] Show matched existing records with empty `raw_publication_details` as editor-confirmed additions on stage 3.
- [x] Make stage 3 apply button visibly apply safe raw-source supplements and show the apply status on return.
- [x] After parser apply, mark applied candidates as resolved and exclude them from regenerated review stages.
- [x] Show `raw_publication_details` explicitly in the Django `Source` admin form.
- [x] Persist and restore stage 0 container confirmations reliably from `review_state.json`.
- [x] Show saved stage 2 edits on stage 3 as pending reviewed field changes.
- [x] Generate `review_stage2.html` from current run data instead of keeping a static prototype page.
- [x] Add parser author staging page for confirming existing authors or marking new authors.
- [ ] Add reviewed apply step with mandatory `data/editor.sqlite` backup.
- [ ] Implement database apply for confirmed new authors and author links after explicit stage 3 confirmation.
- [ ] Implement database apply for saved stage 2 field edits after explicit stage 3 confirmation.
- [ ] Expand parser rules using `docs/PARSER_FIELD_GUIDE_RU.md` and focused fixtures.

## New Import Workflow

- [x] Add editor import tables for batches, items, entities, relations, groups, decisions, matches, and apply logs.
- [x] Add `/imports/` pages for import list, creation, overview, review queue, group detail, item detail, and plan.
- [x] Add MVP parser component for books, journal articles, and collection articles.
- [x] Deduplicate repeated entities inside one import, including one journal issue for multiple related articles.
- [x] Find candidate matches in the existing editor database and keep a `0.7` match floor.
- [x] Add group-level review for journal issues, collections, and standalone books.
- [x] Add transaction-based apply for reviewed creates: authors, journals, journal issues, collections, books, articles, and author links.
- [x] Add focused tests for normalization, parsing, deduplication, grouping, and duplicate matching.
- [x] Keep found existing records visible as import items instead of hiding them from review.
- [x] Add item-level statuses for found existing records, possible supplements, and structural conflicts.
- [x] Search `//` rows by work core before proposing article/container creation.
- [x] Parse parent collection title separately from parent place/year.
- [x] Show field comparison for matched existing records in the import item page.
- [x] Replace match labels that currently show only `type/id` with human-readable existing-record citations.
- [x] Add item-level decisions for found existing records and structural conflicts without applying database updates.
- [x] Apply item-level update-existing decisions as safe empty-field supplements with backup and apply log.
- [x] Add field-by-field checkbox review for update-existing safe supplements.
- [x] Add author review screen with cascade decisions for all linked imported records.
- [x] Add detailed read-only import plan preview before apply.
- [x] Polish import workflow status and decision labels for editor-facing Russian UI.
- [x] Add read-only import readiness validation checklist before apply.
- [x] Add editor-facing import apply result page.
- [x] Add read-only archive mode for applied import batches.
- [x] Add short editor trial guide for the new import workflow: `docs/EDITOR_IMPORT_TRIAL_RU.md`.
- [ ] Add field-by-field comparison page for update-existing decisions.
- [ ] Implement safe update-existing apply for empty-field supplements and approved field changes.
- [ ] Add author review screen with cascade messaging for names used in multiple imported records.
- [x] Add group split/move actions for incorrectly grouped articles.
- [ ] Improve parser coverage using `docs/PARSER_FIELD_GUIDE_RU.md` and real import fixtures.

## Contract

- [x] Define first `data/site_contract.json` for the future Django import.
- [x] Decide first exported tables and columns in `data/bibliobon.sqlite`.
- [x] Add `docs/FIELD_CONTRACT.md` canonical field dictionary.
- [x] Add repeatable audit for model, Google Sheets, site contract, and artifact field names.
- [ ] Add contract validation to the future Django importer.
- [ ] Add `DATA_CHANGELOG.md` entries whenever the site contract changes.

## Google Sheets

- [x] Port Google Sheets import/export into `bibliobon-data/editor`.
- [x] Decide active Google Sheets import/export lives in `bibliobon-data/editor`.
- [ ] Port the current sheet schema documentation into active data-project docs.
- [x] Add a dry-run import from Google Sheets into this project.
- [x] Ensure removed rows in relation tabs remove canonical relations.
- [ ] Preserve manual Google Sheets edits during migration.

## Diagnostics

- [x] Report counts for core entities and relations.
- [x] Report articles without normalized containers.
- [x] Report articles with both journal issue and container work.
- [x] Report articles with `host_title` but no normalized container.
- [x] Report empty journals and journal issues.
- [x] Report duplicate journal issues by journal/year/number/volume.
- [x] Report duplicate or suspiciously similar authors.
- [x] Report legacy `Collection` use.
- [x] Report publication details duplicated between article and container.

## Cleanup Workflows

- [x] Add dry-run/apply cleanup for unused authors, tags, empty issues, empty journals, empty groups, and unused legacy collections.
- [x] Add repeatable redundant-field cleanup for duplicated physical description, issue enumeration, article/container place, and article pages.
- [x] Add repeatable normalization for article-specific journal issue details and redundant article volume numbers.
- [ ] Add reviewed workflow for duplicate journal issues after splitting `issue_number` and `gross_number`: merge physical duplicate issues with the same journal/year/issue/volume/gross number, move articles to canonical issue, preserve article-specific publication details before clearing issue-level raw details.
- [ ] Define safe merge workflow for duplicate authors.
- [ ] Define safe conversion workflow for mistaken journals created from books/collections.
- [x] Remove unused legacy `Collection` rows after reviewed backup cleanup; active `Article.collection` links are zero.
- [ ] Define safe workflow for cleaning `publication_details` via review markers.

## Site Integration

- [ ] Keep `bibliobon-catalog` unchanged until the data model and export contract are stable.
- [x] Export editor data to `data/bibliobon.sqlite`.
- [x] Generate `data/site_contract.json` from the data project.
- [ ] Add Django management command in `bibliobon-catalog` to import from `data/bibliobon.sqlite`.
- [ ] Keep current site behavior stable during the transition.
- [ ] Run `manage.py check` after site importer changes.
- [ ] Run focused tests for parsing/import/filtering changes.

## Editor Site

- [x] Create separate local Django editor under `editor/`.
- [x] Import bootstrap JSONL into `data/editor.sqlite`.
- [x] Add editor pages for works, sections, journals, and collection-like container works.
- [x] Make journal/container selection show linked records directly on the periodicals page.
- [x] Add editor conversions for one-article journals and one-article containers.
- [x] Add MVP two-panel relation editor for linking records to issues, containers, authors, tags, sections, and languages.
- [ ] Add editor validation report pages.
- [x] Add editor Google Sheets sync commands.
- [x] Add editor Google Sheets sync page.
- [x] Add `biblio-admin.test` to local editor `ALLOWED_HOSTS`.
- [ ] Finish system-level `biblio-admin.test` setup in `/etc/hosts` and Caddyfile with administrator privileges.
- [x] Add relation editor commands to turn selected works into journal structures or collection containers.
- [x] Make editor search case-insensitive for Cyrillic text on custom editor pages.
- [x] Add explicit `work_type=container` and relation editor actions for deleting selected works and converting empty containers back to books.
- [ ] Add reviewed journal normalization workflow: merge an incorrectly named journal such as `Петербург– ский коллекционер` into the canonical `Петербургский коллекционер`, preview affected issues/articles, move non-conflicting issues, merge duplicate issues by year/number, move articles/placements to canonical issues, preserve conflicting issue fields for review, create backup, and leave the emptied wrong journal as a cleanup candidate instead of deleting it automatically.
- [ ] Speed up relation edits and deletes by deferring full target-model rebuilds: ordinary editor actions should update only editor tables, while target rebuild should move to an explicit service action and remain automatic only for batch imports/exports where needed.
