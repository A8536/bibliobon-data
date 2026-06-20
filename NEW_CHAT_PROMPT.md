# Текст для нового чата

Проект: `bibliobon-data`

Локальный путь:

```text
/Users/oleg/Projects/data/bibliobon-data
```

Связанный Django-сайт:

```text
/Users/oleg/Projects/websites/bibliobon-catalog
```

Продакшен сайта:

```text
https://biblio.bonistika.info/
```

Рабочая Google Sheets таблица:

```text
1zYFOqM-wBT6mFYLmblaGRBlV0rJeWAaLrEsCMJYKHTw
```

## Главная цель

Создать правильную отдельную базу данных источников для библиографического каталога Bibliobon, которую можно пополнять, проверять и изменять вне работы над самим Django-сайтом.

Новая архитектура:

```text
Google Sheets / manual TSV / scripts
        ↓
bibliobon-data validation + normalization
        ↓
data/bibliobon.sqlite + reports + site_contract.json
        ↓
bibliobon-catalog Django import
```

Сайт должен постепенно стать потребителем готового экспортного артефакта данных, а не местом, где ведётся основная редакторская база.

## Важные правила

1. Работать прежде всего со структурой и качеством данных.
2. Не менять дизайн и публичные шаблоны сайта без явной просьбы.
3. Не деплоить без явного разрешения.
4. Не перезаписывать базы без бэкапа.
5. Перед импортом или массовой правкой SQLite делать backup.
6. Основной источник текущей правды на старте: текущая Django-база и Google Sheets, не исходный Excel.
7. Импорт из Excel больше не использовать как активный workflow.
8. Если работаешь с Google Sheets, не терять ручные правки пользователя.
9. Все durable-решения фиксировать в `CONTEXT.md` или `DATA_CHANGELOG.md`.
10. Будущие задачи вести в `TODO.md`.

## Что уже создано

В папке `bibliobon-data` создан стартовый каркас:

- `AGENTS.md` — правила работы для агента.
- `README.md` — назначение проекта и целевой workflow.
- `CONTEXT.md` — текущий контекст модели данных и решений.
- `TODO.md` — стартовый backlog.
- `DATA_CHANGELOG.md` — журнал изменений контракта данных.
- `docs/ARCHITECTURE.md` — архитектура разделения data-проекта и сайта.
- `source/` — будущие исходные снимки и raw exports.
- `data/` — будущие машинные данные.
- `data/manual/` — ручные overrides и merge rules.
- `reports/` — диагностики.
- `scripts/` — повторяемые команды.
- `docs/reference/current-site/` — reference-снимки документов из текущего Django-проекта.

## Текущая модель сайта, которую нужно перенести в data-проект

Главные сущности:

- `Work` — книга, статья, выпуск/том сборника, иногда контейнер для статей.
- `Author` — авторы.
- `WorkAuthor` — связь авторов с работами.
- `Section` — иерархические разделы из исходной книги.
- `Tag`, `WorkTag` — темы/географические/именные указатели.
- `Journal` — журнал как периодическое издание.
- `JournalIssue` — конкретный выпуск журнала.
- `Article` — связь статьи с контейнером:
  - `journal_issue` для статьи в журнале;
  - `container_work` для статьи в сборнике/книге-контейнере.
- `WorkGroup` / `WorkGroupItem` — группы связанных изданий, например многотомники или ежегодники.
- `Collection` — legacy-таблица, её надо постепенно исключать из активной логики.

Предпочтительный подход:

- сборник хранить как обычный `Work`;
- статьи сборника связывать через `Article.container_work`;
- `Collection` не использовать для новых связей;
- многотомники объединять через `WorkGroup`;
- статьи должны ссылаться на конкретный том/выпуск через `Article.container_work`.

## Важные поля Work

- `django_id` в Google Sheets сейчас соответствует `id` в Django, но в новом data-проекте это должно стать compatibility/reference полем, а не главным ID.
- `source_sequence` — порядок/позиция исходной записи из первоначального импорта.
- `source_number` — номер записи из печатной книги. Для новых записей исторический номер не имитировать.
- `title`
- `subtitle`
- `responsibility_note`
- `host_title` — legacy/raw контейнер, лучше нормализовать через `Article.journal_issue` или `Article.container_work`.
- `publication_place`
- `publisher`
- `inferred_year`
- `physical_description`
- `publication_details` — сырая/legacy строка для проверки, постепенно чистить или переносить в структурированные поля.
- `public_review`
- `article_pages`

## Стабильные ID

Нужно ввести стабильные ID data-проекта, независимые от Django primary key:

- `work_id`
- `author_id`
- `section_id`
- `tag_id`
- `journal_id`
- `journal_issue_id`
- `group_id`

Текущие Django IDs сохранять как `source_django_id` или подобное поле для сопоставления во время миграции.

## Первая практическая задача

Начать с bootstrap из текущей Django SQLite базы:

```text
/Users/oleg/Projects/websites/bibliobon-catalog/app/db.sqlite3
```

Нужно создать повторяемый скрипт, например:

```bash
python3 scripts/bootstrap_from_site_db.py \
  --source /Users/oleg/Projects/websites/bibliobon-catalog/app/db.sqlite3
```

Первый результат:

- JSONL-экспорты таблиц в `data/`;
- сохранение `source_django_id`;
- первичные стабильные ID;
- `data/build_manifest.json`;
- диагностические отчёты в `reports/`.

После этого определить `data/site_contract.json` и будущий `data/bibliobon.sqlite` для импорта в Django-сайт.

## Полезные первые диагностики

Перед массовыми правками сделать отчёты:

- количество `Work`, `Author`, `Journal`, `JournalIssue`, `Article`;
- статьи без контейнеров;
- статьи с одновременно `journal_issue` и `container_work`;
- статьи с `host_title`, но без нормализованного контейнера;
- пустые журналы;
- пустые выпуски;
- дубли `JournalIssue` по `(journal_id, year, issue_number, volume)`;
- дубли авторов по похожему `display_name`;
- `publication_details`, дублирующий данные контейнера;
- legacy `Collection`, ещё используемые в `Article.collection`.

## Команды сайта для проверки, когда позже меняется Django importer

```bash
cd /Users/oleg/Projects/websites/bibliobon-catalog/app
../.venv/bin/python manage.py check
../.venv/bin/python manage.py test catalog
```

Пока работа идёт только в `bibliobon-data`, эти команды не обязательны.
