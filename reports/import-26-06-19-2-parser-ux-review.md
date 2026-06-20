# Import review: 26-06-19-2

Date: 2026-06-19
Batch: `26-06-19-2`
URL: `http://biblio-admin.test/imports/29/review/`

Scope: editor-style review of a large import batch. Decisions were not applied.

## Parser run

The batch was parsed manually with `parse_import_batch()` because the draft had 0 items.

Result:

- 96 source rows
- 23 import entities
- 18.39 seconds parse time
- status: `review_required`

Item status distribution:

- found existing, no changes: 67
- found existing with differences: 14
- parsed/new: 7
- structural conflict: 5
- needs review: 3

Type distribution:

- books: 87
- collection articles: 5
- journal articles: 4

## Main finding

The parser/matcher already finds most records, but the remaining failures are systematic. Since this import is expected to consist of records that already exist in the base, every `Новая`, `Требует решения`, and most `Отличается` states should be treated as diagnostics, not as routine editor work.

## Parser patterns causing false new records or weak matches

### Author parsing

- `В.Б. Бумажные деньги...` is parsed as title `В.Б` instead of an abbreviated author/signature.
- `Г.Б.К. К вопросу о Денежной Реформе...` has the same problem.
- `Гурьев А. (Н.) Реформа денежного обращения...` parses `(Н.)` as part of the title instead of the author initials.
- `Туган-Барановский М.И. Бумажные деньги и металл...` is not recognized as an author, likely because of the hyphenated surname.

Impact: records that are in the database appear as `Новая` or weak candidates.

### Title and publication year parsing

- `Обзор деятельности Министерства финансов в царствование императора Александра III (1881–1894). — СПб..., 1902` uses the date range from the title instead of the publication year.
- `Министерство финансов 1802–1902...` is split incorrectly around the date range and multi-part details.
- `Русские банки. Справочные...` is treated as different when the existing record stores title/subtitle in another shape.

Impact: exact existing works become false differences or false new records.

### Extent and physical details

Several records lose important extent fragments:

- prefatory pages: `6, Х, 928 с.`
- plates/tables: `3 л. табл.`
- illustrations: `: ил.`
- multi-volume extents: `Т. 1. — 226 с.; Т. 2. — 125 с.`

Impact: comparison says fields differ, but the editor cannot easily see whether this is a real correction, a richer source value, or a parser loss.

### Responsibility, notes, and semicolon syntax

- `Джевонс В. Бумажные деньги; пер. с англ. — Одесса...` splits `пер. с англ.` incorrectly and loses place/publisher.
- `{{...}}` notes in journal article rows should be separated into notes, not left as part of matching noise.
- Edition markers such as `2-е изд.` must not be lost.

Impact: source rows become artificially different from existing records.

### Journal and collection containers

- `Наука и Жизнь. — Рига. — 1902? — № 44` becomes journal title `Наука и Жизнь. - Рига`, so the journal match is weaker than it should be.
- `Вопросы мировой войны. — Пг.: Право, 1915` is split as collection title plus wrong place/publisher values.
- Existing parent titles often include place/year, while source parent title contains only the title. This creates structural conflicts where the real parent may be the same.

Impact: articles are shown as if they are in another issue/collection even when the human sees the same container.

## UI/UX issues observed

### Review page

The review page is useful after recent changes, but for a diagnostic import it still overloads the editor:

- The plan lists too many blocking messages one by one.
- `Новая` rows do not clearly say: "parser did not find a match, but this may be a parser error".
- Some rows with exact or likely matches are still presented as decisions the editor must make manually.
- Container warnings are mixed with row-level article/book warnings.

Recommendation: add a diagnostic grouping for `Вероятная ошибка разбора / слабое совпадение` and separate it from true new records.

### Item page

The item page is now closer to editor work, but several screens still cause uncertainty:

- For false new records the page says "Будет создана новая запись", even when the likely reason is a parse error.
- The editor needs to see used vs unused source fragments. Otherwise it is hard to understand where `2-е изд.`, plates, tables, translation notes, or parent container details went.
- For matched records, the comparison should show normalized bibliographic meaning, not only raw field equality.
- For article container conflicts, wording should say whether the current source issue and existing linked issue are actually equivalent after normalization.

Recommendation: show a source parsing map: original row fragments -> parsed fields -> unused fragments.

### Plan page

The plan is too strict for imports where all records already exist:

- It blocks on many "possible additions" that are probably parser normalization problems.
- It asks for repeated decisions where the best default is likely "skip existing and add only safe missing technical fields".
- It does not give a compact summary like "67 found, 14 need comparison review, 15 likely parser/matcher diagnostics".

Recommendation: introduce a "diagnostic mode" or at least a compact blockers summary by reason.

## Suggested next work blocks

### 1. Parser diagnostics and manual correction

Add per-item parsing diagnostics:

- show parsed fields inline in the comparison table;
- show unused source fragments;
- allow the editor to correct parsed fields and re-run matching for that row;
- allow postponing a broken row without blocking the rest of the batch.

This is the highest value block because it helps both editor work and parser debugging.

### 2. Matching fallback by normalized full bibliographic string

Add a fallback matcher that compares normalized full bibliographic strings, not only split fields:

- normalize punctuation, dashes, spaces, case, `ё/е`;
- ignore obvious punctuation-only differences;
- compare title + author + year + container where available;
- use it to surface candidates for false `Новая` rows.

This should catch records like `Гурьев А. (Н.)...`, `Туган-Барановский...`, and rows where title/subtitle split differs.

### 3. Parser rule improvements for recurring bibliographic patterns

Add focused parsing rules for:

- initials-only or signature-like authors: `В.Б.`, `Г.Б.К.`;
- parenthetical initials after author: `Гурьев А. (Н.)`;
- hyphenated surnames: `Туган-Барановский М.И.`;
- title date ranges vs publication year;
- edition markers: `2-е изд.`;
- translation/responsibility after semicolon;
- prefatory pages, plates, tables, illustrations;
- multi-volume extents;
- journal title/place separation.

### 4. Performance diagnostics

96 records took 18.39 seconds. This is usable but slow for editor feedback.

Add timing instrumentation around:

- parse row;
- work match search;
- entity match search;
- group creation;
- comparison generation.

Likely optimization target: repeated scans of existing works during match search.

## Examples to re-test

- `item 384`: `В.Б. Бумажные деньги...`
- `item 391`: `Г.Б.К. К вопросу...`
- `item 398`: `Гурьев А. (Н.) Реформа...`
- `item 437`: `Обзор деятельности Министерства финансов... (1881–1894)... 1902`
- `item 450`: `Туган-Барановский М.И. Бумажные деньги и металл...`
- `item 463`: `Эльяшев А. Вексельное право... // Наука и Жизнь. — Рига...`
- `item 376`: `Боголепов М.И. Война и деньги // Вопросы мировой войны...`

