# TODO

## Data Model

- [x] Confirm whether source sections (`Категории-1`) and thematic/geographic categories (`Категории-2`) are separate public taxonomies.
- [x] Decide how to handle bibliography records with empty author strings.
- [x] Decide whether annotations/reviews are public, private editorial notes, or two separate fields.
- [x] Decide how new site records should be numbered after imported source records 1-5462.
- [x] Identify whether language data exists in the source materials or must be entered manually.
- [x] Decide whether catalog records should use a base `Work` model with subtypes (`Book`, `Article`) or separate book/article models sharing common mixins/fields.
- [x] Define containment models for articles: `Collection` for one-off сборники and `Journal` + `JournalIssue` for periodicals.
- [x] Decide whether source book sections stay one-to-many only, or whether the site also needs curated many-to-many section assignment.
- [x] Define tag/index term types for future indexes: thematic, geographic, issuer, name, and other controlled vocabularies.
- [ ] Review duplicate source numbers in `Каталог`: 2278, 3634, 4055, 4828.
- [x] Normalize article containment into `Collection`, `Journal`, and `JournalIssue` records from `host_title` and `publication_details`.
- [ ] Review first-pass article container classification, especially long conference titles and almanacs.
- [ ] Add manual override fields or workflow for correcting article container type after import.

## Import Preparation

- [x] Build a repeatable parser for `design/Библиография.xlsx`.
- [ ] Import only relevant workbook sheets: `Каталог`, `Категории-1`, `Категории-2`, `BAR_Bibl`, and optionally `Описание` as reference documentation.
- [x] Ignore unrelated legacy workbook sheets from other projects unless explicitly needed later.
- [x] Parse number lists and ranges in `Категории-2` into entry-category links.
- [ ] Normalize author strings from `Каталог` and cross-check them against `BAR_Bibl`.
- [x] Preserve raw source fields during import so citation formatting can be revised without data loss.
- [ ] Use `BAR_Bibl` to validate and improve author normalization.
- [ ] Add import diagnostics for duplicate numbers, missing target works in tag rows, and suspicious author splits.
- [ ] Add diagnostics for article containers that are likely misclassified as journals or collections.
- [x] Use section separator rows in `Каталог` column B to assign imported works to the correct source section.
- [x] Keep journal subsection context for section `1.2. Статьи в журналах` when article rows repeat the parent `p12` marker.
- [x] Add diagnostics for inheritable journal/collection container data on article records.
- [x] Normalize current DB so journals and issue-like containers are no longer source-section tree nodes.

## Google Sheets

- [x] Review Google Sheets import/export workflow in the neighboring `newspapers` project.
- [x] Add Google Sheets export for works, authors, sections, tags, work-author links, and work-tag links.
- [x] Add Google Sheets export/import for journals, journal issues, collections, and article-container links.
- [x] Add Google Sheets import with dry-run support.
- [x] Add a staff-only Google Sheets web page.
- [x] Add editable work description fields for subtitle, responsibility note, place, publisher, and physical description.
- [x] Add root `.env` support for Google Sheets settings.
- [ ] Add diagnostics and cleanup workflow for empty publication containers left after Google Sheets edits:
  journals without issues, journal issues without articles, and collections without articles.
- [ ] Add staff-only cleanup action for empty containers with dry-run preview before deletion.
- [ ] Add cleanup workflow for orphaned/empty link and container records after manual Google Sheets edits:
  authors with no linked works, journal issues with no articles, journals with no issues/articles,
  and legacy collection/container records with no articles.
- [ ] Add automatic SQLite backup before real Google Sheets import.
- [ ] Add persistent operation log for Google Sheets actions.

## Public Site

- [ ] Discuss whether Elasticsearch is needed for full-text search, fuzzy search, morphology, and faceted filtering when the catalog grows.
- [ ] Add a public feedback page or contact email for reporting mistakes in bibliographic descriptions.
- [x] Add simple catalog landing page with statistics, latest records, search, and navigation links.
- [x] Add basic section, author, and tag list pages.
- [x] Add expandable section tree to the landing page.
- [x] Filter central title list by selected section and descendant sections.
- [x] Replace work detail links with full bibliography rows on the main screen.
- [x] Hide public review text under an expandable `Рецензия` control.
- [x] Add author detail pages with the author's books and articles.
- [x] Add tag detail pages with bibliography rows for works linked to the tag.
- [x] Add public list/detail pages for journals, journal issues, and collections.
- [ ] Add detail pages for sections.
- [ ] Add pagination to public lists and search results.
- [ ] Improve public citation formatting for books and articles.
- [x] Add linked section breadcrumbs above the central bibliography list.
- [x] Add top author and top tag panels under the section tree.
- [x] Collapse extended bibliography metadata under a red-triangle spoiler in list rows.
- [x] Add home-page search across authors, titles, journals, tags, sections, and description fields.
- [x] Show author and tag selections in the central home-page results pane instead of navigating to separate pages from the side panels.

## Project Structure

- [x] Move Django runtime into `app/`.
- [x] Keep design, data, assets, archive, secrets, and docs at project root.
- [x] Update import paths after moving Django runtime.
