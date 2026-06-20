# Current Agent Task

## Status

Ready for implementation

## Task

Rework the bibliography import parser enough to make the current editor import usable on real bibliography strings.

The current UX problem is not only visual. Most confusing import screens are caused by incorrect parsing of the source bibliography line. The editor UI then compares wrong fields, fails to find existing records, or shows duplicated parser-preview fragments.

All records in the current test imports are known to already exist in the database. The parser should therefore produce much fewer false-new records and false differences.

## Context

Project:

```text
/Users/oleg/Projects/data/bibliobon-data
```

Local editor site:

```text
http://biblio-admin.test/
```

Current parser used by the import MVP:

```text
editor/sources/import_workflow.py
parse_record()
parse_book_record()
parse_article_record()
```

There is also a larger standalone parser script:

```text
scripts/parse_bibliography.py
```

But the current import pages appear to use `editor/sources/import_workflow.py`, not the standalone script.

You may either:

1. improve the parser functions in `editor/sources/import_workflow.py`, or
2. extract a focused parser module, for example `editor/sources/bibliography_parser.py`, and make import workflow use it.

Prefer option 2 if it makes tests and future parser work clearer.

## Test Fixtures

Use this fixture file as the source of regression cases:

```text
/Users/oleg/Projects/data/bibliobon-data/reports/import32_parser_expected_cases.json
```

It contains raw bibliography strings and expected parsed fields for current failing import examples.

The minimum cases to support:

- `import32_item_629`
- `import32_item_636`
- `import32_item_645`
- `import32_item_704`
- `import32_item_747`
- `import31_item_566`

## Current Failing Examples

### Case: import32 item 704

URL:

```text
http://biblio-admin.test/imports/32/items/704/
```

Raw:

```text
Боровиков С.В. Государственные Кредитные Билеты Российской Империи 1898–1912. Управляющие и кассиры. Альбом-каталог. — СПб., 2006. — 238 с.: ил. {...}
```

Current UI problem:

The `Как разобрана строка -> Разбор парсера` block duplicates many fragments. This happens because the UI tries to rediscover parsed fragments in the raw string using simple string search. When normalized values differ from raw values, unmatched badges are appended at the end and duplicate the text.

Required parser behavior:

```json
{
  "authors": ["Боровиков С.В."],
  "title": "Государственные Кредитные Билеты Российской Империи 1898–1912",
  "title_remainder": "Управляющие и кассиры. Альбом-каталог",
  "publication_place": "СПб.",
  "year": "2006",
  "extent": "238 с.",
  "illustrations": "ил."
}
```

Required UI/parser diagnostic behavior:

The parser should return source spans or enough diagnostics for the UI to highlight parsed fragments without duplicating them.

Do not build parser preview by appending unmatched parsed values to the end of the raw line.

### Case: import32 item 747

URL:

```text
http://biblio-admin.test/imports/32/items/747/
```

Raw:

```text
Иванкин Ф.Ф. Бумажный рубль России. Управляющие, директора, кассиры, наркомы и другие подписанты. 1843 — 1934 гг. — М.: Издательство Олега Пахмутова, 2010. — 236 с.: ил. — 100 экз.
```

Current parser result is wrong:

```json
{
  "title": "Бумажный рубль России",
  "title_remainder": "Управляющие, директора, кассиры, наркомы и другие подписанты. 1843",
  "year": "1934",
  "publication_place": "",
  "publisher": ""
}
```

Expected:

```json
{
  "authors": ["Иванкин Ф.Ф."],
  "title": "Бумажный рубль России. Управляющие, директора, кассиры, наркомы и другие подписанты. 1843–1934 гг.",
  "title_remainder": "",
  "publication_place": "М.",
  "publisher": "Издательство Олега Пахмутова",
  "year": "2010",
  "extent": "236 с.",
  "illustrations": "ил.",
  "circulation": "100 экз."
}
```

Important rule:

Do not treat date ranges inside the title as publication year. The publication year should come from the imprint segment after the dash:

```text
— М.: ..., 2010.
```

### Case: import32 item 636

URL:

```text
http://biblio-admin.test/imports/32/items/636/
```

Raw:

```text
Боровиков С.В. Государственные Кредитные Билеты Российской Империи 1898–1912. Управляющие и кассиры. Альбом-каталог. — 2-е изд. доп. и уточн. — СПб.: Издательство ДЕАН, 2017. — 240 с.: ил. — 450 экз. — 220х300 мм.
```

Current parser misses:

- `edition_statement`
- publication place
- publisher

Expected:

```json
{
  "authors": ["Боровиков С.В."],
  "title": "Государственные Кредитные Билеты Российской Империи 1898–1912",
  "title_remainder": "Управляющие и кассиры. Альбом-каталог",
  "edition_statement": "2-е изд. доп. и уточн.",
  "publication_place": "СПб.",
  "publisher": "Издательство ДЕАН",
  "year": "2017",
  "extent": "240 с.",
  "illustrations": "ил.",
  "circulation": "450 экз.",
  "dimensions": "220х300 мм"
}
```

Important rule:

An edition segment may appear before the imprint:

```text
— 2-е изд. доп. и уточн. — СПб.: ...
```

After extracting the edition segment, the parser must continue parsing the following imprint segment.

### Case: import32 item 645

URL:

```text
http://biblio-admin.test/imports/32/items/645/
```

Raw:

```text
Бумажные денежные знаки России. Государственные выпуски 1769–2014 г. Каталог. 6-е изд. / Под общ. ред. В.Б. Загорского. — СПб.: Стандарт-Коллекция, 2015. — 60 с. — 2000 экз. — 165х235 мм.
```

Expected:

```json
{
  "authors": [],
  "title": "Бумажные денежные знаки России. Государственные выпуски 1769–2014 г.",
  "title_remainder": "Каталог",
  "edition_statement": "6-е изд.",
  "responsibility_statement": "Под общ. ред. В.Б. Загорского",
  "responsibility_contributors": [
    {"name": "В.Б. Загорский", "role": "editor"}
  ],
  "publication_place": "СПб.",
  "publisher": "Стандарт-Коллекция",
  "year": "2015",
  "extent": "60 с.",
  "circulation": "2000 экз.",
  "dimensions": "165х235 мм"
}
```

Important rules:

- `6-е изд.` is an edition statement, not title remainder.
- `/ Под общ. ред. ...` is a responsibility statement.
- Responsibility names should be candidates with roles, not primary authors.
- The date range `1769–2014 г.` belongs to the title.

### Case: import32 item 629

URL:

```text
http://biblio-admin.test/imports/32/items/629/
```

Raw:

```text
Алямкин А.В. Государственные законные платёжные средства без ограничений. Каталог российских бумажных денежных знаков. Россия: 1769–2018 годы. — М.: Студия Вольфсона, 2019. — 384 с.: ил. — 1000 экз. — 250х310 мм. {...}
```

Expected:

```json
{
  "authors": ["Алямкин А.В."],
  "title": "Государственные законные платёжные средства без ограничений",
  "title_remainder": "Каталог российских бумажных денежных знаков. Россия: 1769–2018 годы",
  "publication_place": "М.",
  "publisher": "Студия Вольфсона",
  "year": "2019",
  "extent": "384 с.",
  "illustrations": "ил.",
  "circulation": "1000 экз.",
  "dimensions": "250х310 мм"
}
```

Also fix comparison:

If the matched `Work.dimensions` is `250х310 мм`, the comparison row must show `same`, not `source_extra`.

### Case: import31 item 566

URL:

```text
http://biblio-admin.test/imports/31/items/566/
```

Raw:

```text
Гиндин И.Ф. Банки и экономическая история России (XIX — начало ХХ в.): В 3 томах / Институт экономики. — М.: Наука, 1995.
```

Expected:

```json
{
  "authors": ["Гиндин И.Ф."],
  "title": "Банки и экономическая история России (XIX — начало ХХ в.)",
  "title_remainder": "",
  "responsibility_statement": "Институт экономики",
  "publication_place": "М.",
  "publisher": "Наука",
  "year": "1995",
  "extent": "В 3 томах"
}
```

Important rules:

- Parentheses containing a date range are part of the title.
- `: В 3 томах` is extent/volume statement here, not subtitle.
- `/ Институт экономики` is responsibility statement.

## Parser Architecture Requirements

The parser should return structured data and diagnostics.

Minimum result shape:

```python
{
    "detected_type": "book",
    "fields": {
        "authors": [...],
        "title": "...",
        "title_remainder": "...",
        "responsibility_statement": "...",
        "responsibility_contributors": [...],
        "edition_statement": "...",
        "publication_place": "...",
        "publisher": "...",
        "year": "...",
        "extent": "...",
        "illustrations": "...",
        "circulation": "...",
        "dimensions": "...",
        "notes": "..."
    },
    "spans": [
        {"field": "authors", "start": 0, "end": 13, "text": "Боровиков С.В."},
        {"field": "title", "start": 14, "end": 78, "text": "..."}
    ],
    "warnings": [...]
}
```

Exact shape can differ if it fits project patterns, but it must support:

- field comparison;
- parser preview without duplicated fragments;
- editor diagnostics for unparsed fragments.

## Parser Rules To Implement

Implement focused rules, not a broad rewrite:

1. Split the record into bibliographic zones by strong separators:
   - title/responsibility zone;
   - edition zone;
   - imprint zone;
   - physical description zone;
   - notes zone.
2. Treat dash-separated publication zones as stronger than periods inside title.
3. Do not use year-like values inside title ranges as publication year.
4. Recognize edition statements:
   - `2-е изд.`
   - `6-е изд.`
   - `2-е изд. доп. и уточн.`
   - `перераб. и доп.`
5. Recognize imprint:
   - `М.: Publisher, 2010`
   - `СПб.: Publisher, 2017`
   - `СПб., 2006`
6. Recognize physical details:
   - extent: `236 с.`, `384 с.`
   - illustrations: `ил.`, `4 л. ил.`
   - circulation: `100 экз.`
   - dimensions: `250х310 мм`
7. Preserve notes in braces.
8. Extract responsibility contributors:
   - `Под общ. ред. В.Б. Загорского` -> contributor `В.Б. Загорский`, role `editor`
   - preserve full responsibility statement.

## UI Requirements For Parser Preview

Fix:

```text
Как разобрана строка -> Разбор парсера
```

The preview must not duplicate labels/fragments.

Preferred display:

```text
Original line
<raw line>

Parser split
[Боровиков С.В.] [Государственные Кредитные Билеты ...] [Управляющие и кассиры. Альбом-каталог] — [СПб.] [2006] — [238 с.] [ил.]
```

Each highlighted fragment should be:

```html
<span class="status-badge" title="Title">...</span>
```

Visible badge text must not include field labels like `Title:` or `Authors:`.

If a parsed value cannot be mapped to a source span, do not append it blindly at the end. Show it in a small diagnostic section only when useful:

```text
Parsed but not located in source: ...
```

## Person / Contributor Roles

This project already has:

```text
Author
WorkAuthor.role
SourceAuthor.role
```

Do not create separate tables for editors/translators unless there is a strong reason.

Use the existing Author/Person table and store the role in the relation.

Add or document canonical roles:

```text
author
editor
responsible_editor
translator
compiler
commentator
illustrator
organization
other
```

For this task, only parser extraction of `responsibility_contributors` is required. Full UI/editor workflow for contributor roles can be a follow-up if too large.

## Acceptance Criteria

- The parser passes all cases in `reports/import32_parser_expected_cases.json`.
- Current import item `747` is no longer parsed with year `1934`; it should parse publication year `2010`.
- Current import item `636` extracts edition/place/publisher/year.
- Current import item `645` extracts `6-е изд.` as edition and `Под общ. ред. ...` as responsibility.
- Current import item `629` compares dimensions as `same` when matched DB value equals source value.
- Parser preview on item `704` no longer duplicates most fragments.
- Tests are added for all listed fixture cases.
- Existing import workflow still parses articles with `//`.
- Existing tests still pass.

## Tests To Run

```bash
python3 editor/manage.py test sources.tests.ImportWorkflowTests
python3 editor/manage.py check
python3 editor/manage.py makemigrations --check --dry-run
```

If a new parser test class is created, run it explicitly too.

Manual UX check:

```text
http://biblio-admin.test/imports/32/items/704/
http://biblio-admin.test/imports/32/items/747/
http://biblio-admin.test/imports/32/items/636/
http://biblio-admin.test/imports/32/items/645/
http://biblio-admin.test/imports/32/items/629/
```

## Output Expected

- Summary
- Files changed
- Parser architecture notes
- Test results
- Manual UX check results
- Risks / assumptions

