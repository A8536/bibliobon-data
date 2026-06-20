# Development Log

## 2026-05-01 23:19 MSK - Initial bibliography data analysis

Reviewed project root and `design/` source files:

- `design/BAR_Bibl.pdf` - source bibliography book, 362 PDF pages, titled "Отечественная бонистика. Библиографический указатель. 1808-2021 гг.", second revised and expanded edition, Moscow, 2022.
- `design/Библиография.xlsx` - working spreadsheet with an early attempt to convert the bibliography into structured data.
- `design/BAR_Web_01.pdf` - six-page early UI concept; not used for current data-model work.

Relevant workbook sheets:

- `Описание` contains a draft domain model: book, section, category, language, list views, and screens.
- `Каталог` contains the main bibliography data: 5462 numbered records. Columns currently map to source page/section marker, current number, author string, title, host/publication part, imprint/details, and review/comment.
- `Категории-1` contains the source book table of contents and section hierarchy. It includes 300 non-empty rows: chapter IDs like `c1`, section IDs like `p11`, nested subsection IDs like `p2514`, titles, notes, and descriptions.
- `Категории-2` contains a thematic/geographic index. It has 402 non-empty category rows; most rows map a category title to a comma/range list of bibliography record numbers.
- `BAR_Bibl` contains the alphabetical author index: 4916 author-to-record-number rows.

Unrelated or legacy workbook sheets appear to come from another project: `Сайт ЮНЕСКО`, `Биорезерваты`, `Вестник ...`, and `Ответы на форму (1)`.

Current structural interpretation:

- A bibliography record should keep the source book number as a stable external identifier, not only an internal database ID.
- A bibliography record belongs to at most one source section from the book structure (`Категории-1` / table of contents).
- A bibliography record can belong to many thematic/geographic categories from `Категории-2`.
- A bibliography record can have many authors. The raw author string from `Каталог` should be preserved, while normalized author entities can be derived and manually corrected later.
- Languages are part of the early model in `Описание`, but no reliable language source has been identified in the current workbook yet.
- The original split bibliography fields should be preserved even if the rendered citation is later assembled as one formatted string.

Draft entities:

- `BibliographyEntry`: source number, source page marker, section, raw author string, title, host/publication part, imprint/details, note/review, inferred year, source status.
- `Author`: normalized display name plus optional sortable name and aliases.
- `EntryAuthor`: entry-author join with ordering and role when known.
- `Section`: source book hierarchy from `Категории-1`, with source code, parent, order, title, description, and note.
- `SubjectCategory`: thematic/geographic hierarchy from `Категории-2`, with parent, order, title, and description when later available.
- `EntrySubjectCategory`: many-to-many join from entries to thematic/geographic categories.
- `Language`: planned dictionary, default Russian, but import source remains unresolved.

Open modeling questions:

- Should source sections and thematic/geographic categories be presented as two separate navigation systems, or should they be merged into one visible "lists" area?
- Should entries without an author string be modeled as anonymous/untitled-author records, or should the author field simply remain empty?
- Should `Book_Review` from the early model be a public annotation, an editorial note, or both separated into public/private fields?
- Should record numbers from the book remain immutable even after new records are added, with a separate site-local sequence for additions?

## 2026-05-01 23:24 MSK - Confirmed catalog semantics

Confirmed project semantics:

- `Категории-1` is the existing section hierarchy of the printed book.
- `Категории-2` is better modeled as tags/index terms attached to catalog records. These tags can later power thematic, geographic, issuer, name, and other indexes.
- Source record numbers from the book should be preserved for backward compatibility. They may become less visible later, but should remain stored.
- The final comment/review field from `Каталог` is a public review/annotation shown on the site.
- All imported records can default to Russian for now. Other languages are planned later.
- Avoid creating a synthetic "Без автора" author entity, because it would incorrectly group many unrelated records. Empty/unknown authors should remain empty metadata and be handled in display/search logic.

Refined entity interpretation:

- `Work` / catalog record is the common parent concept for both books and articles. It keeps source number, title, source section links, tags, language, authors, and public review.
- `Book` is a work that is a standalone monograph/catalog/book-like publication.
- `Article` is a work that belongs to a containing publication: either a one-off collection or a periodical issue.
- `Author` can be linked to many books and articles through an ordered join model.
- `Collection` is a one-off multi-author publication containing articles.
- `Journal` is a continuing periodical title.
- `JournalIssue` represents a specific issue of a journal and stores issue-level attributes such as year, number, volume, place, and publisher when available.
- `Tag` / index term is a many-to-many classification attached to works; typed or grouped indexes can be derived from tags later.

Open modeling issue:

- The printed section hierarchy assigns each bibliographic record to one source location in the book, but the future site may need many-to-many section assignment. Keep the imported source section as a stable primary classification, and consider a separate curated many-to-many section relationship if editorial needs require it.

## 2026-05-01 23:27 MSK - Project folder rename

Decision: build the site on Django.

Renamed the project folder from `shares-catalog` to `bibliobon-catalog` to match the bibliography catalog domain before Django scaffolding begins.

## 2026-05-01 23:44 MSK - Django scaffold and first import

Created a Django 5.2 project with a `catalog` app.

Environment and dependencies:

- Added local `.venv`.
- Added `requirements.txt` with `Django==5.2.12` and `openpyxl==3.1.5`.
- Project uses SQLite for local development.
- Locale settings changed to `ru-ru` and `Europe/Moscow`.

Initial catalog models:

- `Work` is the shared catalog record for books and articles.
- `Book` and `Article` are subtype/detail models linked one-to-one to `Work`.
- `Author` and `WorkAuthor` store ordered authors without creating a fake "Без автора" entity.
- `Collection`, `Journal`, and `JournalIssue` are prepared for later article containment normalization.
- `Section` stores the printed book section hierarchy from `Категории-1`.
- `Tag` and `WorkTag` store index terms from `Категории-2`.
- `Language` defaults imported records to Russian.

Import implementation:

- Added `catalog/importers.py` for testable spreadsheet parsing helpers.
- Added management command `import_bibliography`.
- Command imports sections, works, authors, book/article subtype rows, tags, and work-tag links from `design/Библиография.xlsx`.
- Raw bibliography fields are preserved: author string, title, host/publication part, publication details, and public review.
- `source_number` is not unique because the spreadsheet currently contains duplicate record numbers. `source_sequence` is the unique import key for preserving every catalog row.

Import check on current workbook:

- 5462 works.
- 1703 books.
- 3759 articles.
- 1651 normalized author seed records.
- 291 coded source sections.
- 347 unique tags from 402 tag/index rows.
- 4889 work-tag links.
- 680 works have no author links because the source author string is empty.
- 4 source numbers are duplicated in the workbook: 2278, 3634, 4055, 4828.

Validation run:

- `manage.py makemigrations catalog`
- `manage.py migrate`
- `manage.py import_bibliography --clear`
- `manage.py test catalog`
- `manage.py check`

## 2026-05-02 00:23 MSK - Article container normalization

Added first-pass normalization for article containers during spreadsheet import.

Rules:

- Only records whose host field starts with `//` are classified as articles.
- Records whose host field starts with a single `/` are treated as standalone books/editions for now, because those rows usually contain editor/compiler statements rather than article containers.
- Article host strings are normalized by stripping the leading `//`, removing soft hyphens, replacing non-breaking spaces, and collapsing whitespace.
- Article containers are classified as `Collection` when the host title contains collection/conference markers such as `сб.`, `сборник`, `материал`, `тезис`, `конференц`, `чтения`, `альманах`, or `труды`.
- Other article containers are treated as `Journal` + `JournalIssue`.
- Issue year, issue number, volume, and pages are parsed from publication details when simple source patterns are present.

Current import statistics after rerun:

- 5462 works.
- 1807 books.
- 3655 articles.
- 279 collections.
- 397 journals.
- 1669 journal issues.
- 667 articles linked to collections.
- 2988 articles linked to journal issues.
- 0 articles without a normalized container.
- 2852 articles with parsed pages.

Validation run:

- `manage.py import_bibliography --clear`
- `manage.py test catalog`
- `manage.py check`

## 2026-05-02 00:29 MSK - Public catalog landing page

Added the first public catalog pages:

- `/` shows catalog statistics, a search form, latest imported records, and navigation links.
- `/sections/` lists source book sections with direct work counts.
- `/authors/` lists normalized author seed records with work counts.
- `/tags/` lists imported index tags with work counts.

Search currently matches `Work.title`, `raw_author_string`, `host_title`, `publication_details`, and `public_review`.

Implementation notes:

- Added `catalog/urls.py`.
- Connected `catalog.urls` at the site root in `config/urls.py`.
- Added templates under `catalog/templates/catalog/`.
- Added smoke tests for public views.

Validation run:

- `manage.py test catalog`
- `manage.py check`
- `curl http://127.0.0.1:8000/`
- `curl "http://127.0.0.1:8000/?q=Гознак"`

## 2026-05-02 00:48 MSK - Section tree landing page

Reworked the public landing page around the printed book section hierarchy.

Design reference:

- Reviewed neighboring `newspapers` project, especially `publication_list.html` and `catalog/static/catalog/site.css`.
- Reused its restrained catalog layout direction: serif page title, muted kicker, soft background, bordered panels, compact filter-like left navigation, and dense link/list presentation.

Behavior:

- The left panel now shows an expandable section tree.
- Initially only the top-level tree items are visible.
- Expanding a section reveals the next nested level.
- Selecting a section filters the central list to works directly in that section and all descendant sections, because a work belongs to its source section and all parent sections.
- The center pane is now a simple paginated title list.

Data fix:

- Corrected section hierarchy import. Section nesting is now derived from the title column position in `Категории-1`, which preserves relationships such as `c1 -> p11/p12` and `p12 -> p121`.
- Re-ran `import_bibliography --clear` after the hierarchy fix.

Validation run:

- `manage.py import_bibliography --clear`
- `manage.py test catalog`
- `manage.py check`
- `curl http://127.0.0.1:8000/`
- `curl "http://127.0.0.1:8000/?section=<c1 id>"`

## 2026-05-02 15:57 MSK - Catalog section assignment fix

Fixed source section assignment during Excel import.

Problem:

- The first import used the explicit marker in column A of `Каталог` as the section marker.
- Column A is populated only for early rows; after that most records have an empty column A.
- Because of that, the importer kept the previous marker for too long, and most records were incorrectly assigned to `p21` / "2.1. Работа Гознака и его фабрик".

Source structure:

- `Каталог` uses separator rows in column B to mark section boundaries.
- Examples: `21 Работа Гознака и его фабрик`, `22 Каталоги бумажных денежных знаков`, `251 Центральный район`, `«Петербургский коллекционер»`, etc.

Implementation:

- Added section resolution in `catalog/importers.py`.
- The resolver builds a section map from `Категории-1`.
- Numeric separators in `Каталог` are mapped to section codes (`21` -> `p21`, `2510` -> `p2510`).
- Text separators are matched to section titles from `Категории-1`, using current section context to resolve repeated titles.
- Re-ran `manage.py import_bibliography --clear`.

Verification:

- Works are now distributed across 258 source sections.
- `p21` now has 35 direct works instead of 5318.
- Example counts after fix:
  - `p21` / "2.1. Работа Гознака и его фабрик": 35.
  - `p22` / "2.2. Каталоги бумажных денежных знаков": 128.
  - `p23` / "2.3. Работы общего характера: монографии, исследования": 181.
  - `p522` / "5.22. «Петербургский коллекционер»": 650.
  - `p11` / "1.1. Книги": 96.
  - `p12` / "1.2. Статьи в журналах": 48.

Validation run:

- `manage.py import_bibliography --clear`
- `manage.py test catalog`
- `manage.py check`
- `curl http://127.0.0.1:8000/`

## 2026-05-03 15:52 MSK - Work detail cards

Added public detail cards for catalog records.

Behavior:

- Main title list now links to `/works/<id>/`.
- Detail cards distinguish book-like works and articles with "Карточка издания" / "Карточка статьи".
- Cards include source number, import sequence, inferred year, type, authors, source section, language, raw source/publication fields, article container details, pages, public review, tags, and nearby works in the same source section.
- Added previous/next navigation by import sequence.
- Added edit link to the Django admin record.

Design reference:

- Mirrored the `newspapers` issue card structure: back/action bar, large serif hero title, metadata badges, fact rows, side panel, and sibling list.

Validation run:

- `manage.py test catalog`
- `manage.py check`
- `curl http://127.0.0.1:8000/`
- `curl http://127.0.0.1:8000/works/<book id>/`
- `curl http://127.0.0.1:8000/works/<article id>/`

## 2026-05-03 15:59 MSK - Project directory layout

Reorganized the project to match the maintainable layout used by the neighboring `newspapers` project.

New layout:

- `app/` contains Django runtime code and database:
  - `manage.py`
  - `config/`
  - `catalog/`
  - `db.sqlite3`
- root-level project materials remain outside Django runtime:
  - `design/`
  - `data/raw/`
  - `data/imports/`
  - `data/exports/`
  - `assets/`
  - `archive/`
  - `secrets/`
  - project docs and backlog files.

Path changes:

- Added `PROJECT_ROOT = BASE_DIR.parent` in Django settings.
- Updated `import_bibliography` default workbook path to `PROJECT_ROOT / "design" / "Библиография.xlsx"` so imports work from `app/`.
- Kept `.venv` in the project root; Django commands now run as `../.venv/bin/python manage.py ...` from `app/`.
- Updated `.gitignore` for `app/db.sqlite3`.

Validation run from `app/`:

- `../.venv/bin/python manage.py check`
- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py import_bibliography --clear`
- `curl http://127.0.0.1:8000/`
- `curl http://127.0.0.1:8000/works/<book id>/`

## 2026-05-03 18:07 MSK - Main bibliography list and author pages

Changed the public browsing model after deciding that individual book/article cards are not needed yet.

Changes:

- Removed links from the main bibliography list to work detail cards.
- Removed the public `/works/<id>/` route and deleted the work detail template.
- The main section view now renders full bibliography rows: source number, linked normalized authors when available, title, host/publication part, and publication details.
- Public reviews are no longer shown inline. They are hidden under a native expandable `Рецензия` summary.
- Added author detail pages at `/authors/<id>/`.
- Author names in bibliography rows link to the author page, which uses the same bibliography row design to list that author's books and articles.

Data model note:

- Authors already have a separate `Author` table.
- Works link to authors through `WorkAuthor`, so each author can be connected to multiple books and articles without duplicating author records.

Validation run:

- `manage.py test catalog`
- `manage.py check`
- `curl http://127.0.0.1:8000/`
- `curl http://127.0.0.1:8000/authors/<author id>/`
- `curl http://127.0.0.1:8000/works/1/` returns `404`.

## 2026-05-03 18:36 MSK - Main page navigation and collapsed metadata

Updated the main catalog page to keep browsing focused on the current section list.

Changes:

- The central list header now shows linked section breadcrumbs for the selected section hierarchy.
- Breadcrumb labels are cleaned for public display, for example `Глава 1. Издания до 1918 года - Книги`.
- The left column now contains three panels: the expandable section tree, top 10 authors by linked work count, and top 10 tags by linked work count.
- Added public tag detail pages at `/tags/<id>/`, using the same bibliography row template as the main and author pages.
- Bibliography rows now show only authors and title by default.
- Extra fields such as host title, publication details, source section, and review are hidden in an expandable block opened by a red triangle after the title.
- Public review text remains additionally hidden under the nested `Рецензия` summary.

Validation run:

- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`

## 2026-05-11 - Local font assets

Design integration:

- Copied Roboto light/regular and Scada regular font files from neighboring `bonistika_info` legacy theme into `assets/fonts`.
- Added `STATICFILES_DIRS` so top-level `assets/` is collected and served as Django static assets.
- Added `@font-face` declarations to `catalog/site.css`.
- Set the base font stack to Roboto and headings to Scada.

Validation run:

- `../.venv/bin/python manage.py check`
- `../.venv/bin/python manage.py findstatic assets/fonts/roboto-regular.woff assets/fonts/scada-regular.woff catalog/site.css`
- `../.venv/bin/python manage.py collectstatic --noinput --clear`

## 2026-05-11 - Work group deletion and search follow-up

Local workflow:

- Recorded a future task in `TODO.md` to discuss whether Elasticsearch is needed for full-text search, fuzzy search, morphology, and faceted filtering.
- Added a staff-only `Удалить группу` button to the work groups tool.
- Deleting a group removes `WorkGroup` and its `WorkGroupItem` links only; linked `Works` records are left intact.

Validation run:

- `../.venv/bin/python manage.py test catalog.tests.ContainerConversionTests`
- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`

## 2026-05-11 - Public beta layout pass

Design/UI changes:

- Replaced the local BiblioBon-only header with a Bonistika.info-style site header: top strip, brand area, and horizontal navigation.
- Added a public footer with Bonistika.info and catalog links.
- Moved search from the left sidebar into the central catalog panel above all result lists.
- Made the central search input full-width within the content panel.
- Hid home-page service buttons (`Google Sheets`, `Админка`, `Детали`, conversion tools, group tools) from non-staff users.
- Wrapped direct admin edit links in detail pages so they are visible only to staff/admin users.

Validation run:

- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`
- `../.venv/bin/python manage.py collectstatic --noinput`
- Anonymous `curl http://127.0.0.1:8000/` check confirms service/admin labels are no longer present in public HTML.

## 2026-05-10 - Future cleanup note

Recorded follow-up in `TODO.md`:

- Add a cleanup workflow for orphaned/empty records after Google Sheets edits: authors with no linked works, journal issues with no articles, journals with no issues/articles, and legacy collection/container records with no articles.

## 2026-05-10 - Convert single-article collection container to book

Local staff workflow:

- Added a staff-only `Сделать книгу` action for `?container_work=<id>` pages when the selected collection/container work has exactly one linked article.
- The merge keeps the `Work` record that has `source_sequence`, so historical source identity is preserved.
- For historical article records, container bibliographic fields are not copied into the article work itself; the full container citation is preserved only in `host_title`.
- The kept work is converted to `book`, article/container links are removed, and the synthetic container work is deleted when it has no `source_sequence`.

Validation run:

- `../.venv/bin/python manage.py test catalog.tests.ContainerConversionTests`
- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`

Follow-up fix:

- Adjusted merged citation formatting so a converted single-article collection keeps both the article description and the container/book description.
- Converted records now preserve the container title in `host_title`, keep article pages in `article_pages`, and include remaining `publication_details` even when structured publication fields are present.
- Repaired already converted local record `Work.django_id=48425` from diagnostic reports: restored `host_title`, `article_pages`, and citation output.

Second follow-up fix:

- Changed single-article collection-to-book conversion so container bibliographic fields are not copied into the article work itself.
- The kept historical article work now keeps only its own title/subtitle/responsibility/publication fields; the full container citation is preserved in `host_title` after `//`.
- This prevents duplicate collection title, editors, place, publisher, year, and physical description in merged analytical records.
- Repaired already converted local record `Work.django_id=48752` to match its previous analytical citation form.

- `curl http://127.0.0.1:8000/`
- `curl "http://127.0.0.1:8000/?section=<section id>"`
- `curl http://127.0.0.1:8000/tags/<tag id>/`

## 2026-05-03 19:02 MSK - Journal subsection assignment in section 1.2

Fixed article section assignment for `1.2. Статьи в журналах`.

Problem:

- The workbook lists journal names as separator rows under `1.2. Статьи в журналах`.
- Those journal rows are also present as child sections in `Категории-1`, for example `p121 «Вестник Азии»`.
- Article rows below a journal heading repeat the parent marker `p12` in column A.
- The importer previously treated that repeated marker as authoritative and reset each article back to the parent `p12`, leaving the journal child sections empty.

Implementation:

- Added source-section ancestry detection in `catalog/importers.py`.
- During `Каталог` import, a row marker does not reset the current section when it points to an ancestor of the current section.
- This preserves context such as `p121 «Вестник Азии»` for article rows that repeat the parent `p12` marker.
- Added a regression test for source record `98`, `Внутренний заем`.
- Re-ran the full import with `../.venv/bin/python manage.py import_bibliography --clear`.

Verification:

- Source record `98`, `Внутренний заем`, now imports as `source_page_marker=p121`, section `p121 «Вестник Азии»`.
- Its article container remains journal `Вестник Азии`.
- Direct works in `p12` are now `0`; works are distributed across `p121` through `p1217`.
- `p121 «Вестник Азии»` has `10` direct works.

Validation run:

- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py import_bibliography --clear`
- `../.venv/bin/python manage.py check`

## 2026-05-03 19:45 MSK - Google Sheets editing workflow

Reviewed the neighboring `newspapers` project Google Sheets workflow.

Reference implementation:

- `catalog/google_sheets.py` defines sheet names, headers, export rows, Google API helpers, and shared parsing helpers.
- `export_google_sheet` writes all managed sheets and freezes header rows.
- `import_google_sheet` supports `--dry-run` by rolling back the transaction.
- `/google-sheets/` is staff-only and exposes export, dry-run import, and real import buttons.

Bibliography implementation:

- Added Google API dependencies to `requirements.txt`.
- Added `GOOGLE_SHEETS_SPREADSHEET_ID` and `GOOGLE_SHEETS_CREDENTIALS` settings.
- Added root `.env` loading in Django settings. Environment variables still take precedence over `.env`.
- Configured the current spreadsheet ID in `.env`: `1zYFOqM-wBT6mFYLmblaGRBlV0rJeWAaLrEsCMJYKHTw`.
- Relative `GOOGLE_SHEETS_CREDENTIALS` values are resolved against the project root.
- Added editable `Work` description fields:
  - `subtitle`;
  - `responsibility_note`;
  - `publication_place`;
  - `publisher`;
  - `physical_description`.
- Added migration `catalog.0003`.
- Added `catalog/google_sheets.py`.
- Added management commands:
  - `export_google_sheet`;
  - `import_google_sheet`.
- Added staff-only page at `/google-sheets/`.
- Added Google Sheets documentation in `GOOGLE_SHEETS.md`.

Sheet structure:

- `Works` for bibliographic records and editable description fields.
- `Authors` for the author dictionary.
- `Sections` for the printed book hierarchy.
- `Tags` for index terms used later by thematic/geographic/name indexes.
- `WorkAuthors` for ordered many-to-many author links.
- `WorkTags` for many-to-many tag links.

Modeling decision:

- Publication places, publishers, and physical description details are editable text fields on `Work`, not separate entities.
- The original `publication_details` field remains as raw source text for checking and backward compatibility.
- Added conservative parsing from `publication_details` into the new editable fields during the Excel import.
- Re-ran `import_bibliography --clear`; current parsed field coverage is:
  - `publication_place`: 1483 works;
  - `publisher`: 781 works;
  - `physical_description`: 1005 works.

Validation run:

- `../.venv/bin/python manage.py migrate`
- `../.venv/bin/python -m pip install -r ../requirements.txt`
- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py import_bibliography --clear`
- `../.venv/bin/python manage.py check`

## 2026-05-04 12:56 MSK - Google Sheets dry-run message fix

Fixed a Google Sheets dry-run failure shown in the web interface as:

- `Ошибка Google Sheets: name 'works' is not defined`

Cause:

- The tail of `tag_detail` had accidentally been left inside `add_import_messages` while adding the Google Sheets page.
- After a successful dry-run, the message helper tried to paginate a local `works` variable that only exists in `tag_detail`.

Implementation:

- Restored `tag_detail` pagination/rendering inside `tag_detail`.
- Removed the stray tag-detail code from `add_import_messages`.
- Added regression tests for:
  - tag detail page rendering;
  - Google Sheets import message helper not referencing unrelated view locals.

Validation run:

- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`
- `../.venv/bin/python manage.py import_google_sheet --spreadsheet-id "1zYFOqM-wBT6mFYLmblaGRBlV0rJeWAaLrEsCMJYKHTw" --credentials "../secrets/google-service-account.json" --dry-run`

Dry-run result:

- Created: works `0`, authors `0`, sections `0`, tags `0`, work-author links `5117`, work-tag links `4889`.
- Updated: works `5462`, authors `1651`, sections `291`, tags `347`.
- Skipped rows: `0`.

## 2026-05-04 13:03 MSK - SQLite lock timeout for Google Sheets import

Investigated `Ошибка Google Sheets: database is locked`.

Findings:

- No stale process was holding `app/db.sqlite3`.
- Command-line Google Sheets dry-run completed successfully.
- Django SQLite connections had no wait time configured, so concurrent writes could fail immediately.

Implementation:

- Added SQLite database option `timeout: 30` in `config/settings.py`.
- Verified Django connection `PRAGMA busy_timeout` is now `30000`.
- Restarted the local server.

Validation run:

- `../.venv/bin/python manage.py import_google_sheet --spreadsheet-id "1zYFOqM-wBT6mFYLmblaGRBlV0rJeWAaLrEsCMJYKHTw" --credentials "../secrets/google-service-account.json" --dry-run`
- `../.venv/bin/python manage.py check`

## 2026-05-04 13:18 MSK - Home search and inline author/tag filters

Updated the home page browsing model.

Changes:

- Added a search form above the section tree.
- Search matches titles, subtitles, responsibility notes, raw author text, normalized author names, author aliases, journal/container title, publication place, publisher, physical description, raw publication details, public review, tags, and section titles.
- Author links in the side panel and bibliography rows now point to `/?author=<id>`.
- Tag links in the side panel now point to `/?tag=<id>`.
- Author and tag selections render in the same central results pane used by section browsing.
- Author result headers show the author display name and aliases.
- Tag result headers show the tag title.
- Existing `/authors/<id>/` and `/tags/<id>/` pages remain for compatibility, but the main browsing flow no longer uses them.

Validation run:

- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`
- `curl http://127.0.0.1:8000/`
- `curl "http://127.0.0.1:8000/?q=Вестник%20Азии"`
- `curl "http://127.0.0.1:8000/?author=<author id>"`
- `curl "http://127.0.0.1:8000/?tag=<tag id>"`

## 2026-05-04 16:57 MSK - Article container inheritance diagnostics

Added a repeatable diagnostic command for article journal/collection containers:

- `../.venv/bin/python manage.py diagnose_article_containers --limit 15`

The command reads the source workbook and compares detected container headers with current imported `Article`, `JournalIssue`, `Collection`, and `Work` data. It does not change the database.

Current diagnostic result:

- Source container headers with articles: `120`.
- Headers with place/raw column C: `58`.
- Headers with extra cells or descriptive rows: `60`.
- Imported `Article` rows: `3655`.
- Linked to journal issues: `2988`.
- Linked to collections: `667`.
- Without normalized container: `0`.
- Articles under detected source headers: `954`.
- Articles that can inherit `publication_place` from source headers: `453`.
- Collection-like article rows by host title: `667`.
- Rows where `//` container marker appears misplaced in `publication_details`: `0`.

Important examples of inheritable data:

- `«Русский вестник»` -> `Москва`.
- `«Тамбовские губернские ведомости»` -> `Тамбов`.
- `«Вестник Манчжурии»` -> `Харбин`.
- `«Голос филателиста» ...` -> `Ленинград`.
- `«Известия Народного Комиссариата финансов»` -> `Москва`.

Observations:

- All article rows already have a normalized container link.
- The next safe automatic improvement is to inherit missing `publication_place` from detected source headers where the article itself has no place.
- Host-title mismatches still need editorial review: many are abbreviations or expanded forms such as `Известия Наркомфина` vs `Известия Народного Комиссариата финансов`, not necessarily import errors.

Validation run:

- `../.venv/bin/python manage.py diagnose_article_containers --limit 15`
- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`

## 2026-05-04 19:59 MSK - Publication containers removed from section tree

Normalized the current database structure without re-importing from Excel.

Problem:

- The source section tree contained publication containers such as journals, issues, and collection titles as child sections.
- This mixed two separate concepts:
  - catalog/source sections;
  - publication containers (`Journal`, `JournalIssue`, `Collection`).

Implementation:

- Added `normalize_publication_sections`.
- The command has dry-run mode by default and `--apply` for writing changes.
- Created a SQLite backup before applying:
  - `data/backups/db-before-publication-section-normalize-20260504-193750.sqlite3`
- Moved works out of publication-title sections into the nearest real catalog section.
- Converted `920` book-like rows inside publication sections into article rows and created/updated their article container links.
- Deleted `251` publication-container sections from the section tree.

Applied result:

- Moved works: `3949`.
- Deleted publication sections: `251`.
- Remaining sections: `40`.
- Remaining quoted-title sections: `0`.
- `p12 Статьи в журналах`: `48` works.
- `c3 Статьи в журналах, опубликованные в 20–30-х гг. ХХ в.`: `596` works.
- Article works: `4575`.
- Book works: `887`.
- Article rows without normalized container: `0`.

Spot checks:

- Source `98` now remains an article in `p12` and links to journal `Вестник Азии`.
- Source `947` is now an article in `c3` and links to the `Ежемесячный бюллетень...` journal.
- Source `1755` is now an article in `c4` and links to journal `Московский бонист`.

Validation run:

- `../.venv/bin/python manage.py normalize_publication_sections`
- `../.venv/bin/python manage.py normalize_publication_sections --apply`
- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`

## 2026-05-04 20:51 MSK - Public journal and collection navigation

Added public pages for publication containers so journal and collection articles can be browsed independently from the catalog section tree.

Implementation:

- Added `/journals/` with journal titles and article counts.
- Added `/journals/<id>/` with journal issues and all linked articles.
- Added `/journal-issues/<id>/` with articles from a specific issue.
- Added `/collections/` with one-off сборники and article counts.
- Added `/collections/<id>/` with all linked articles from a collection.
- Added top navigation links for `Журналы` and `Сборники`.
- Reused the existing bibliography-row rendering so article rows stay visually consistent with the main catalog.

Current data counts:

- Journals: `413`.
- Journal issues: `1879`.
- Collections: `280`.
- Journal articles: `3907`.
- Collection articles: `668`.

Validation run:

- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`
- `curl http://127.0.0.1:8000/journals/`
- `curl http://127.0.0.1:8000/journals/2405/`
- `curl http://127.0.0.1:8000/collections/`
- `curl http://127.0.0.1:8000/collections/1676/`

## 2026-05-04 21:05 MSK - Publication containers in Google Sheets

Extended the Google Sheets workflow with editable publication containers.

New sheets:

- `Journals`: journal dictionary.
- `JournalIssues`: journal issue dictionary linked to `Journals`.
- `Collections`: one-off article collections.
- `ArticleContainers`: article-to-issue and article-to-collection links.

This keeps publication containers separate from the catalog section tree while still making them editable outside Django admin.

Current export row counts:

- `Journals`: `413`.
- `JournalIssues`: `1879`.
- `Collections`: `280`.
- `ArticleContainers`: `4575`.

Validation run:

- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`
- `../.venv/bin/python manage.py shell -c "from catalog.google_sheets import build_export_values; ..."`

## 2026-05-04 21:20 MSK - Google Sheets deletion of final author links

Fixed deletion semantics for `WorkAuthors`.

Problem:

- The importer previously replaced author links only for works still present in `WorkAuthors`.
- If the last author row for an article was deleted from Google Sheets, the work disappeared from `WorkAuthors` entirely.
- The importer therefore had no signal to clear existing `WorkAuthor` rows, so the author reappeared after import/export.

Implementation:

- During import, collect the current work scope from `Works`.
- Treat `WorkAuthors` and `WorkTags` as replacement sets for that work scope.
- If a work is present in `Works` but no longer has rows in `WorkAuthors`, delete all author links for that work.
- Applied the same rule to `WorkTags` for consistency.

Validation run:

- Added `test_import_values_removes_last_author_when_link_row_is_deleted`.
- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`
- `../.venv/bin/python manage.py import_google_sheet --dry-run`

Current Google Sheets dry-run notes:

- `WorkAuthors` now imports `5135` rows from the current sheet, so the deleted author row is visible to the importer.
- Two journal title conflicts remain in the sheet and are reported as warnings instead of aborting import:
  - `Нумбон`;
  - `Таганский бонист`.

## 2026-05-05 00:28 MSK - Public rows no longer show raw author fallback

Adjusted public bibliography rendering after checking work `46740` / source number `3050`.

Problem:

- Deleting the `WorkAuthors` row correctly removed the real author link.
- The public bibliography row still displayed `raw_author_string`, so the mistaken author text remained visible without a link.
- For this workflow, `raw_author_string` is source/import metadata and must not be used as a public author fallback.

Implementation:

- `_bibliography_item.html` now displays authors only from real `WorkAuthor` links.
- Added a regression test for a work with `raw_author_string` but no linked authors.
- Restarted the local server so the browser uses the updated template.

Validation run:

- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`
- `curl http://127.0.0.1:8000/?q=Денежное%20обращение%20СССР`

## 2026-05-05 00:50 MSK - Journal and collection links in main catalog pane

Updated the main catalog pane so publication containers can be opened without leaving the home view.

Implementation:

- Article rows now show the linked container after the title:
  - journal article: `Название статьи (Название журнала)`;
  - collection article: `Название статьи (Название сборника)`.
- The container name is a link back to the home page with a central-pane filter:
  - `?issue=<id>` for a specific journal issue;
  - `?collection=<id>` for a collection.
- Added `?journal=<id>` support for breadcrumb navigation from an issue to all articles in the journal.
- The central pane now renders journal issue headers with breadcrumbs:
  - source section;
  - journal title;
  - year;
  - issue number.
- Collection filters render an analogous header with source section, collection title, and year when available.

Validation run:

- Added regression tests for issue and collection links in the home pane.
- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`
- `curl http://127.0.0.1:8000/`
- `curl http://127.0.0.1:8000/?issue=10072`
- `curl http://127.0.0.1:8000/?collection=1676`

## 2026-05-05 01:05 MSK - Google Sheets journal issue conflict handling

Fixed Google Sheets import crash on duplicate journal issue identity.

Problem:

- `JournalIssue` has a unique identity: `journal`, `year`, `issue_number`, `volume`.
- If a row in `JournalIssues` was edited to match another existing issue, SQLite raised an integrity error and aborted the whole import.

Implementation:

- Added a pre-save conflict check in `import_journal_issues`.
- Conflicting rows are now skipped with a warning that includes the existing `django_id`.
- Added a regression test for this import case.

Current Google Sheets dry-run notes:

- Import now completes as dry-run.
- Remaining warnings:
  - `Journals` row `14`: duplicate title `Нумбон`;
  - `Journals` row `22`: duplicate title `Уральский следопыт`;
  - `JournalIssues` row `203`: duplicates existing issue `django_id=10972`, journal `Таганский бонист`, year `1997`, issue `4 (26)`.

Validation run:

- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`
- `../.venv/bin/python manage.py import_google_sheet --dry-run`

## 2026-05-05 22:35 MSK - GOST publication line in expanded bibliography rows

Updated the expanded bibliography row rendering.

Implementation:

- Combined `publication_place`, `publisher`, `inferred_year`, and `physical_description` into one formatted line.
- Current format:
  - `— Москва: Издательство, 2008. — 240 с.`
- Kept the raw `publication_details` line below the formatted line for comparison and cleanup.

Validation run:

- Added a regression test for the formatted publication line.
- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`
- `curl http://127.0.0.1:8000/?q=Недостатки%20нашей%20денежной`

## 2026-05-05 23:08 MSK - Copyable full citation line

Added a complete bibliographic description as the first line in each expanded bibliography row.

Implementation:

- Added `Work.bibliographic_citation`.
- The citation combines:
  - linked authors;
  - title;
  - subtitle;
  - responsibility note;
  - article journal/collection container;
  - formatted publication statement or raw `publication_details`;
  - article pages when available.
- Added `.citation-copy-line` styling and `user-select: all` so the line can be selected in one gesture.
- Kept the separate structured and raw fields below the copy line.

Validation run:

- Added regression tests for book and article citation output.
- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`
- `curl http://127.0.0.1:8000/?q=Недостатки%20нашей%20денежной`

## 2026-05-06 01:36 MSK - Journal issue citation and catalog-style sidebar

Updated citation rendering and the home-page sidebar.

Implementation:

- Journal article citations now include structured issue data from `JournalIssue` when structured publication fields are used:
  - year;
  - issue number;
  - volume.
- Example pattern:
  - `Название статьи. // Название журнала. — 1904. — № 43. — Место: издатель, год.`
- Kept raw `publication_details` below the copyable citation line.
- Reworked the left sidebar toward a denser catalog-navigation pattern:
  - dark section headers;
  - full-width list rows;
  - visible hierarchy;
  - active row highlight;
  - compact counters.

Notes:

- The referenced `knigant.ru` page was not reachable from the tool during implementation, so this follows the catalog-menu principle rather than copying exact styling.

Validation run:

- Added a regression test for structured journal issue data in citations.
- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`
- `curl http://127.0.0.1:8000/?q=Фальшивые%20кредитки`

## 2026-05-06 02:05 MSK - Publication container diagnostics CSV

Added diagnostic export tables for reviewing mistaken journals, collections, and standalone-work candidates.

Implementation:

- Added `export_container_diagnostics`.
- The command creates two CSV files in `data/reports/`:
  - `publication_containers_summary.csv`;
  - `publication_container_articles.csv`.
- Summary rows contain one row per journal issue or collection with:
  - container IDs;
  - title/year/issue/volume data;
  - article counts;
  - source number range;
  - section titles;
  - sample article titles;
  - editorial columns for `review_decision`, target collection/work IDs, and notes.
- Article rows contain one row per article with current container links and blank editorial columns.
- Added lightweight `review_hint` values for suspicious journal issues:
  - `check_as_collection`;
  - `check_as_standalone_work`.

Generated current diagnostics:

- `data/reports/publication_containers_summary.csv`: `2139` rows.
- `data/reports/publication_container_articles.csv`: `4575` rows.

Validation run:

- `../.venv/bin/python manage.py export_container_diagnostics`
- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`

## 2026-05-06 02:06 MSK - Staff publication container conversion actions

Added staff-only service actions for correcting mistaken publication containers from the public detail pages.

Implementation:

- Journal detail page:
  - `Сделать сборник`;
  - `Сделать книгу` when the journal has exactly one linked article.
- Collection detail page:
  - `Сделать журнал`.
- All actions are POST-only and require staff login.
- `Сделать сборник`:
  - creates or reuses a `Collection` with the journal title;
  - moves all articles from journal issues to that collection;
  - clears `journal_issue` links.
- `Сделать журнал`:
  - creates or reuses a `Journal`;
  - creates or reuses a `JournalIssue`;
  - moves all collection articles to that issue;
  - clears `collection` links.
- `Сделать книгу`:
  - allowed only when the journal has one linked article;
  - changes the linked `Work` to `book`;
  - copies simple issue metadata where useful;
  - deletes the `Article` row.

Validation run:

- Added conversion tests for all three actions and the multi-article guard.
- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`

## 2026-05-06 10:20 MSK - Staff cleanup button for publication_details

Added a temporary staff-only cleanup action for individual `Work.publication_details` fields.

Implementation:

- Expanded bibliography rows always show the copyable full citation line when it can be assembled from structured fields.
- The temporary shortened structured/GOST comparison line is shown only while `publication_details` is still filled.
- Staff users see an inline `Очистить publication_details` POST button next to the raw `publication_details` line.
- Added `clear_work_publication_details`.
- The action clears only the selected work's `publication_details` field and redirects back to the current page.

Validation run:

- Added a regression test for the cleanup action: after cleanup the full copyable citation remains, while the raw and shortened comparison lines disappear.
- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`
- `curl http://127.0.0.1:8000/?q=Недостатки%20нашей`

## 2026-05-06 10:36 MSK - Deployment proposal for bonistika.info integration

Reviewed the neighboring `bonistika_info/` project.

Findings:

- `bonistika_info/` is a WordPress project with Docker-based local development.
- The bibliography catalog is a separate Django project and should stay separate at the code/deployment level.
- Recommended public alpha URL: `https://biblio.bonistika.info`.
- `https://bonistika.info/biblio/` remains possible later through reverse proxy, but is more complex for Django path-prefix handling.

Implementation:

- Added `docs/DEPLOYMENT.md` with GitHub, hosting, DNS, nginx, gunicorn/systemd, update, and backup instructions.
- Added `.env.example`.
- Updated Django settings to read `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, and `STATIC_ROOT` from deployment-friendly settings.

## 2026-05-06 10:47 MSK - Docker production deployment files

Added VPS deployment files for running the Django catalog through Docker Compose.

Implementation:

- Added root `Dockerfile`.
- Added root `docker-compose.prod.yml`.
- Added `.dockerignore` to keep `.env`, `.venv`, SQLite database, generated static files, and secrets out of the Docker build context.
- Added `gunicorn==23.0.0` to `requirements.txt`.
- Added `DJANGO_SQLITE_PATH` support so the production SQLite database can live in a mounted `/data` directory instead of inside the container image.
- Updated `.env.example`.
- Expanded `docs/DEPLOYMENT.md` with Docker Compose setup and update workflow.
- Added Docker Compose defaults for `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS`, so the container can start in alpha mode even when an existing local `.env` only contains Google Sheets settings.

## 2026-05-07 20:02 MSK - VPS backup workflow

Added and deployed SQLite backup workflow for the VPS alpha site.

Implementation:

- Added `scripts/backup_db.sh`.
- The script uses Python's SQLite backup API, writes `data/backups/db-YYYYMMDD-HHMMSS.sqlite3`, and keeps the newest 30 generated backups.
- Updated `.gitignore` so `data/db.sqlite3` and generated SQLite backups are not committed.
- Deployed the script to `biblio.bonistika.info`.
- Created the first VPS backup: `data/backups/db-20260507-200147.sqlite3` (`6926336` bytes).
- Added a user crontab entry for `deploy`: daily backup at `03:17` server time.
- Updated `docs/DEPLOYMENT.md` with backup paths and commands.

## 2026-05-07 21:31 MSK - Google Sheets web import timeout

Fixed production timeout for Google Sheets web import checks.

Problem:

- The staff Google Sheets "Проверить импорт" action can run longer than gunicorn's default 30 second worker timeout.
- On production this caused `WORKER TIMEOUT` in the container logs and an uninformative `Internal Server Error` in the browser.
- CLI dry-run import worked because it is not constrained by gunicorn's request timeout.

Change:

- Increased production gunicorn timeout in `docker-compose.prod.yml` to `180` seconds.

## 2026-05-07 21:47 MSK - Google Sheets import timings and first diff layer

Added import timing diagnostics and a first safe diff layer for Google Sheets imports.

Implementation:

- Added per-stage timings to `import_google_sheet` CLI output and to the staff Google Sheets page messages.
- Timed Google Sheets reading separately from database import stages.
- Added `unchanged` and `deleted` import counters.
- Main entity imports now skip `.save()` when fields are unchanged.
- `WorkAuthor` and `WorkTag` imports now diff existing links per work instead of deleting/recreating links that did not change.
- `Article` container links now skip unchanged saves.

Local dry-run result after change:

- Google Sheets read: about `3.26s` for `24647` rows.
- Django import stages: about `13.69s`.
- Unchanged rows are now reported explicitly, e.g. `5450` unchanged works and `5097` unchanged author links.

## 2026-05-07 22:10 MSK - Deferred empty container cleanup workflow

Decision: do not automatically delete empty journals, journal issues, or collections during Google Sheets import.

Rationale:

- Empty publication containers can be temporary editorial work in progress.
- Automatic deletion during import would be surprising and could remove intentionally prepared records.
- Cleanup should be a separate staff action with preview/dry-run semantics.

Planned workflow:

- Diagnose journals without issues.
- Diagnose journal issues without articles.
- Diagnose collections without articles.
- Add a staff-only cleanup action that first shows counts and sample titles, then deletes only confirmed empty containers.

Recorded in `TODO.md` under Google Sheets.

## 2026-05-08 22:45 MSK - Local journal merge interface

Implemented a local-only staff interface for merging duplicate journals. Not pushed or deployed.

Implementation:

- Added `journals/<id>/merge/`.
- Added a service link on journal detail pages: `Объединить с журналом`.
- Merge form asks for the target journal ID and final title.
- Preview shows the journal to keep, the journal to delete, issue conflicts, non-conflicting issues, and article counts.
- Apply moves non-conflicting issues to the target journal.
- Apply merges conflicting issues by moving articles to the target issue, deleting the duplicate issue, deleting the duplicate journal, and optionally renaming the kept journal.
- Added regression tests for preview and applying a merge with a conflicting issue.

Validation run:

- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`

## 2026-05-09 - Main catalog navigation refinements

Planned local UI changes:

- Keep "Все авторы" and "Все темы" inside the home page central panel instead of opening separate list pages.
- Render full author/tag indexes as compact multi-column lists.
- Make the section label in expanded bibliography rows link back to the section filter.
- For journals, show a year/issue index above the article list so existing years and issue numbers are discoverable.
- In journal issue breadcrumbs, make the year link to all articles/issues for that journal year and make the issue number link to the issue itself.

## 2026-05-09 - Bibliography detail display fixes

Local fixes:

- Avoid duplicating subtitle as a separate diagnostic line when it is already included in the full bibliographic citation and there is no physical_description context requiring extra diagnostics.
- Ensure public_review text is visible in expanded bibliography rows.
- Display public_review as an inline "Пояснение." note before the section link, matching the section metadata size and weight.

Validation run:

- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`

## 2026-05-09 - Google Sheets import header safety

Diagnostic:

- Google Sheets `Works!O1` contained `— С. 103–104.` instead of the required `physical_description` header.
- Restored only the `Works!O1` header to `physical_description`; no data rows were changed.

Local safety fix:

- Added an import preflight check for malformed sheet headers.
- If a provided sheet is missing required columns, import now raises an error before any database updates can be applied.
- Added a regression test proving that malformed `Works` headers do not allow partial updates to other tables.

Validation run:

- `../.venv/bin/python manage.py import_google_sheet --spreadsheet-id 1zYFOqM-wBT6mFYLmblaGRBlV0rJeWAaLrEsCMJYKHTw --credentials ../secrets/google-service-account.json --dry-run`

## 2026-05-10 - Journal and collection index refinements

Local UI changes:

- Changed the left menu "Все журналы" and "Все сборники" links to open central home-page indexes, matching the author and topic index behavior.
- Render journal and collection indexes in compact columns with gray dotted catalog IDs, e.g. `123. Название`.
- Kept standalone `/journals/` and `/collections/` list pages, but aligned their ID formatting with bibliography row numbers.
- When filtering a journal by year, hide the full year/issue index and show only articles from that year; the year/issue index is shown only on the journal-level view.

Validation run:

- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`
- `curl http://127.0.0.1:8000/?list=journals`
- `curl http://127.0.0.1:8000/?list=collections`
- `curl http://127.0.0.1:8000/?journal=2425`
- `curl http://127.0.0.1:8000/?journal=2425&year=1885`

## 2026-05-10 - Collection container links and historical numbers

Local data/UI changes:

- Added `container_work_django_id` and `container_work_title` to the `ArticleContainers` Google Sheets schema so an article can link directly to a collection stored as a full `Works` record.
- Updated Google Sheets `ArticleContainers`: inserted the two new columns after `collection_title` and rewrote the header row; existing data columns were shifted, not overwritten.
- Import now reads `Article.container_work` from the new columns while keeping the old `collection_django_id` / `collection_title` path compatible.
- Export now writes both the legacy `Collection` link and the newer `container_work` link.
- Added a `?container_work=<work id>` home-page filter so clicking a collection title stored as a `Works` record shows all articles from that collection in the central pane.
- Changed public bibliography rows to show `source_number` only for historical source records with `source_sequence`; synthetic/new records no longer show technical numbers.
- Removed technical IDs from public journal and collection indexes/lists.

Validation run:

- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`
- `../.venv/bin/python manage.py import_google_sheet --spreadsheet-id 1zYFOqM-wBT6mFYLmblaGRBlV0rJeWAaLrEsCMJYKHTw --credentials ../secrets/google-service-account.json --dry-run`

Follow-up local UI tweak:

- Removed "Страница журнала" and "Страница сборника" buttons from the central home-page filter actions; conversion buttons remain available for staff/admin users.

## 2026-05-10 - Work groups for multivolume editions

Implemented local support for grouping separate `Works` records into edition groups.

Data model:

- Added `Work.volume_number`.
- Added `WorkGroup` with `title`, `group_type`, and `note`.
- Added `WorkGroupItem` with `group`, `work`, and `sort_order`.

Google Sheets:

- Added `volume_number` to `Works` after `subtitle`.
- Added `WorkGroups` sheet: `django_id`, `title`, `group_type`, `note`.
- Added `WorkGroupItems` sheet: `group_django_id`, `group_title`, `work_django_id`, `work_title`, `sort_order`.
- Updated the live Google Sheet structure by inserting the new `Works` column and creating the two new sheets; existing data was shifted, not overwritten.

Interface:

- Added staff tool `/tools/work-groups/`.
- The tool can create or update a group by title/type/note and a comma-separated list of `work_django_id` values.
- Saving a group replaces its item list, so removing an ID from the form removes that work from the group.
- Added public filter `/?work_group=<id>` to show all works in a group in the central catalog pane.
- Expanded bibliography rows now show group links and the work `volume_number` when available.

Validation run:

- `../.venv/bin/python manage.py makemigrations catalog`
- `../.venv/bin/python manage.py migrate`
- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`
- `../.venv/bin/python manage.py import_google_sheet --spreadsheet-id 1zYFOqM-wBT6mFYLmblaGRBlV0rJeWAaLrEsCMJYKHTw --credentials ../secrets/google-service-account.json --dry-run`

Follow-up local UI tweak:

- Added a compact group link directly in the collapsed bibliography row next to the title, so group membership is visible without opening the full description.
- Made that collapsed group link explicit as `Группа: ...` and visually stronger because the first version was too easy to miss.
- Reverted the collapsed group marker to the quiet gray bracket style and removed the duplicate expanded-detail group line; group membership is now loaded for ordinary work lists as well as group-filter pages.

## 2026-05-10 - Google Sheets author unlink behavior

Local import/export fix:

- Clarified that `WorkAuthors` is the authoritative sheet for linking or unlinking a specific work and author.
- If an imported `WorkAuthors` row references an author that is no longer present in the imported `Authors` sheet, the relation is skipped and removed for the imported work scope instead of being silently restored from the existing database.
- Export now omits authors with no linked works, so an author removed from all links is not regenerated in `Authors` on the next export.
- Updated `GOOGLE_SHEETS.md` with the safe editing workflow for author links.

Follow-up export reliability fix:

- Reworked Google Sheets export writes from many per-sheet/per-chunk update calls to `batchClear` plus batched `values.batchUpdate` requests.
- Added retry handling for Google Sheets `429 RATE_LIMIT_EXCEEDED` responses.
- Documented that repeated manual exports can still require waiting for Google's per-minute quota window to reset.

## 2026-05-10 - Exclude Collection from collection handling

Local model/UI/export shift:

- Stopped using `Collection` as the public source for collection lists and article links.
- Collection indexes and sidebar "Сборники" now use `Work` records referenced by `Article.container_work`.
- Bibliography rows link collection articles to `?container_work=<work id>` instead of `?collection=<collection id>` when `container_work` is present.
- Legacy `/collections/` and `/collections/<id>/` routes now redirect to the home page or the corresponding `container_work` filter.
- Google Sheets export now leaves the `Collections` sheet as a header-only legacy sheet.
- Google Sheets `ArticleContainers` export now writes `container_work_django_id` / `container_work_title` and no longer writes `collection_django_id` / `collection_title`.
- Google Sheets import still accepts old `collection_*` columns only as a compatibility fallback and stores imported collection articles with `Article.collection = None`.
- Added migration `0007_detach_article_collection_links` to clear legacy `Article.collection` links where `Article.container_work` already preserves the collection relationship.

Follow-up collection group UI/citation work:

- Container-work pages now show `Work.volume_number` in the page heading, e.g. `... Вып. 3`.
- Article bibliographic citations now include the full `container_work.bibliographic_citation` for collection articles, so the article line carries the collection's subtitle, place/year, and physical description before article pages.
- Expanded article rows now show a `Сборник:` link to the root `WorkGroup` when the containing collection work belongs to a group.
- Sidebar and "Все сборники" index now collapse grouped collection works into a single `?collection_group=<id>` entry, similar to journal-level navigation over issues.
- Added a `collection_group` home filter that lists all articles across all collection works in that group.
- Collection-group pages now show an "Издания" index above the article list; choosing one edition keeps that index visible and filters articles to that edition.
- In expanded article rows, the volume label in `Сборник: <group> · <volume>` now links to the concrete collection work.
- Reduced sidebar top lists for authors, topics, journals, and collections from 10 to 5 entries.

## 2026-05-10 - Google Sheets import report completeness

Diagnostic:

- The command-line dry-run import reported the full set of changed categories, but the web UI message only showed works/authors/sections/tags and omitted journals, issues, article containers, work groups, group items, and link tables.
- Current Google Sheets `Works` has 4874 data rows while the local database has 5763 `Work` rows. Import updates/creates rows present in the sheet but does not delete `Work` records absent from the sheet.

Local UI fix:

- Expanded the web import summary to include all tracked import categories from `ImportStats`.

## 2026-05-10 - Mark duplicate article publication details

One-time cleanup support:

- Added `mark_duplicate_publication_details` management command.
- The command finds article `Work.publication_details` values that exactly duplicate the article container's `publication_details`.
- It checks both journal issue containers and collection work containers.
- Applied marker `@@` to 1211 currently unmarked duplicate article `publication_details` values.
- Generated review report at `data/reports/duplicate_publication_details.csv`; the report includes 1213 marked duplicate rows because 2 rows had already been marked before this operation.

Validation run:

- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`

## 2026-05-10 - Google Sheets Works review order

Google Sheets export usability:

- Changed only the export order of the `Works` sheet.
- Collection/container works now appear immediately before their linked articles in `Works`, so duplicate bibliographic fields can be compared and cleaned in adjacent rows.
- The database fields, `source_sequence`, and historical catalog numbers are unchanged.
- No temporary model field or import dependency was added; this is only a review-friendly sheet ordering.

Validation run:

- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`

## 2026-05-11 - Google Sheets work groups import stats

Import bugfix:

- Fixed `KeyError: 'work_groups'` during Google Sheets import.
- Added missing import statistics counters for work groups and work group items in the updated counters.
- Expanded the command-line import summary to report work groups and group items alongside works, journals, issues, and links.
- Added a regression check for updating work groups from Google Sheets.

Validation run:

- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`
- `../.venv/bin/python manage.py import_google_sheet --dry-run --spreadsheet-id 1zYFOqM-wBT6mFYLmblaGRBlV0rJeWAaLrEsCMJYKHTw --credentials ../secrets/google-service-account.json`

## 2026-05-11 - Public beta deployment preparation

Public beta checklist:

- Added a future TODO item for a public feedback page or contact email for reporting mistakes in bibliographic descriptions.
- Added a reusable `<meta name="description">` block to the base template.
- Added normal page titles and meta descriptions for the catalog home state, central index states, author/tag/journal/detail pages, and the main list pages.
- Kept technical 404/500 pages unchanged for now.
- Created a fresh SQLite backup from the current local `app/db.sqlite3`: `data/backups/db-20260511-164016.sqlite3` (`7663616` bytes).
- Confirmed service links remain hidden from anonymous visitors.
- Restarted the local development server after template changes.

Validation run:

- `DB_PATH=app/db.sqlite3 scripts/backup_db.sh`
- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`
- `../.venv/bin/python manage.py migrate --check`
- `../.venv/bin/python manage.py collectstatic --noinput --dry-run`
- `DJANGO_DEBUG=false ... ../.venv/bin/python manage.py check --deploy`

## 2026-05-11 - Production static files permission fix

Production issue:

- After public beta deployment, the HTML was served correctly but the page appeared unstyled.
- `https://biblio.bonistika.info/static/catalog/site.css` returned `403 Forbidden`.
- The CSS file existed in `~/projects/bibliobon-catalog/staticfiles/catalog/site.css`, and `collectstatic` had run inside the Docker container.
- Nginx could not traverse `/home/deploy` because the directory mode was `750`.

Server fix:

- Changed `/home/deploy` permissions to allow traversal without directory listing: `chmod o+x /home/deploy`.
- Verified `https://biblio.bonistika.info/static/catalog/site.css` returns `200 OK`.
- Verified `https://biblio.bonistika.info/static/assets/fonts/roboto-regular.woff` returns `200 OK`.

Follow-up:

- For a cleaner long-term setup, consider serving static files from `/var/www/bibliobon/staticfiles` or another nginx-owned path instead of a directory under `/home/deploy`.

## 2026-05-11 - Bonistika shell preview page

Design experiment:

- Added a temporary preview URL: `/preview/bonistika/`.
- The preview renders the existing bibliography home page data inside a copied header/footer shell from `design/Союз Бонистов.html`.
- The normal home page `/` still uses the current catalog layout and is not replaced.
- Added local preview static assets under `app/catalog/static/catalog/bonistika_preview/`.
- Kept the original header/footer links from the saved design file intentionally unchanged for visual comparison.

Validation run:

- `../.venv/bin/python manage.py test catalog`
- `../.venv/bin/python manage.py check`
- `../.venv/bin/python manage.py collectstatic --noinput --dry-run`
- `curl -I http://127.0.0.1:8000/`
- `curl -I http://127.0.0.1:8000/preview/bonistika/`

## 2026-05-11 19:52 MSK - Main page moved into Bonistika shell

Main page design update:

- Switched the primary catalog home page to the copied Bonistika header/footer shell.
- Kept the site title/logo as the only public header link, pointing back to the catalog home page.
- Kept header service navigation and home-page service buttons visible only to staff/admin users.
- Removed the hardcoded home-page kicker above the title.
- Removed Scada usage from the catalog and copied Bonistika CSS; all catalog typography now uses Roboto.
- Split the central search and results list into separate bordered containers with a 30px gap between them.
- Standardized the central results heading into `filter-label` and `filter-name` parts for authors, themes, journals, sections, years, groups, and service filters.
- Moved breadcrumbs to the bottom of the results header block.
- Added a left sidebar filter by publication year periods: before 1918, 1918-1945, 1946-1991, 1992-2000, and after 2001.
- Limited public expanded bibliography rows to the copyable bibliographic citation plus the section link; raw/service fields remain staff-only.

Validation run:

- `../.venv/bin/python manage.py check`
- `../.venv/bin/python manage.py test catalog`
- `curl -I http://127.0.0.1:8000/`
- `curl -s http://127.0.0.1:8000/`
- `curl -s "http://127.0.0.1:8000/?period=before-1918"`
- `curl -s "http://127.0.0.1:8000/?list=authors"`

## 2026-05-11 20:04 MSK - Bonistika header restored

Design correction:

- Restored the original Bonistika header markup on the main catalog page: logo, desktop menu, mobile menu, and top search are kept as in the copied source shell.
- Removed the catalog-specific header title/menu from the main base template.
- Kept catalog service buttons below the page title and staff-only.
- Added a catalog override so the central search input does not inherit the theme's `margin-bottom: 30px`.
- Set bibliography item titles to `font-weight: 300` in the main catalog CSS.
- Decided that future catalog-specific styling should be placed in `app/catalog/static/catalog/site.css`; copied Bonistika CSS files should remain source theme files and be overridden rather than edited for routine catalog tweaks.

Validation run:

- `../.venv/bin/python manage.py check`
- `../.venv/bin/python manage.py test catalog`
- `curl -s http://127.0.0.1:8000/`

## 2026-05-14 17:59 MSK - Yandex Metrika integration

Analytics update:

- Added optional Yandex Metrika support controlled by `YANDEX_METRICA_ID`.
- Added `catalog.context_processors.analytics` so base templates can access the configured counter ID.
- Added `catalog/includes/analytics.html` with the Metrika script and noscript fallback.
- Included analytics in both the main Bonistika shell and the temporary Bonistika preview shell.
- Added `YANDEX_METRICA_ID=109209498` to `.env.example`.
- Added `YANDEX_METRICA_ID` passthrough to `docker-compose.prod.yml`.
- Added tests that verify the counter is rendered only when the ID is configured.

Validation run:

- `../.venv/bin/python manage.py check`
- `../.venv/bin/python manage.py test catalog`
- `YANDEX_METRICA_ID=109209498 ../.venv/bin/python manage.py shell -c "..."`
- `../.venv/bin/python manage.py shell -c "..."`
