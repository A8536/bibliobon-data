# Google Sheets import/export

## Статус с 2026-05-25

Этот документ оставлен как справка по прежнему workflow.

Активная работа с источниками перенесена в отдельный data-проект:

```text
/Users/oleg/Projects/data/bibliobon-data
```

Сайт `bibliobon-catalog` больше не импортирует и не экспортирует источники через Google Sheets. Он только показывает готовую базу данных и обновляется из подготовленного экспорта data-проекта:

```bash
cd /Users/oleg/Projects/websites/bibliobon-catalog/app
../.venv/bin/python manage.py import_bibliobon_data --clear
```

Старые команды `import_google_sheet`, `export_google_sheet` и `import_bibliography` сохранены в коде как legacy, но при запуске останавливаются с сообщением о переносе workflow в `bibliobon-data`.

Ниже описан старый процесс для исторического контекста.

Рабочий цикл:

```text
Django -> экспорт -> Google Sheets -> ручное редактирование -> проверка импорта -> импорт -> Django
```

Используется Google service account.

## Настройка

1. Создать service account в Google Cloud.
2. Включить Google Sheets API.
3. Скачать JSON-ключ.
4. Положить ключ в `secrets/google-service-account.json`.
5. Открыть Google-таблицу и дать email service account права редактора.
6. Задать ID таблицы в `.env` или через переменную окружения `GOOGLE_SHEETS_SPREADSHEET_ID`.

В корне проекта используется файл `.env`:

```bash
GOOGLE_SHEETS_SPREADSHEET_ID=1zYFOqM-wBT6mFYLmblaGRBlV0rJeWAaLrEsCMJYKHTw
GOOGLE_SHEETS_CREDENTIALS=secrets/google-service-account.json
```

Относительный путь в `GOOGLE_SHEETS_CREDENTIALS` считается от корня проекта.

По умолчанию Django ищет ключ здесь:

```text
secrets/google-service-account.json
```

Можно переопределить путь:

```bash
export GOOGLE_SHEETS_CREDENTIALS="/path/to/google-service-account.json"
```

## Web-интерфейс

Служебная страница доступна только staff-пользователям:

```text
/google-sheets/
```

На странице есть три действия:

- экспорт в Google Sheets;
- проверка импорта без записи в базу;
- реальный импорт в Django.

## Команды

Экспорт:

```bash
cd app
../.venv/bin/python manage.py export_google_sheet \
  --spreadsheet-id "GOOGLE_SHEET_ID" \
  --credentials "../secrets/google-service-account.json"
```

Экспорт пишет данные пакетами через Google Sheets `batchClear` и `values.batchUpdate`. Это снижает число write-запросов и помогает не упираться в лимит Google `60 write requests/minute/user`. Если Google всё равно вернул `429 RATE_LIMIT_EXCEEDED`, подождите минуту и повторите экспорт: команда делает несколько повторных попыток с паузами, но внешний лимит может сохраняться, если перед этим было много ручных запусков.

Проверка импорта:

```bash
cd app
../.venv/bin/python manage.py import_google_sheet \
  --spreadsheet-id "GOOGLE_SHEET_ID" \
  --credentials "../secrets/google-service-account.json" \
  --dry-run
```

Импорт:

```bash
cd app
../.venv/bin/python manage.py import_google_sheet \
  --spreadsheet-id "GOOGLE_SHEET_ID" \
  --credentials "../secrets/google-service-account.json"
```

## Листы

### `Works`

Основной лист библиографических записей.

Ключевые поля:

- `django_id` - основной идентификатор Django; не менять вручную;
- `source_sequence` - порядковый номер строки исходного импорта;
- `source_number` - номер записи из печатной библиографии;
- `work_type` - `book`, `article` или `unknown`;
- `section_django_id`, `section_code` - связь с разделом;
- `language_code` - сейчас обычно `ru`;
- `authors_display` - справочный текст, редактирование связей авторов делается в `WorkAuthors`;
- `title` - основное название;
- `subtitle` - подзаголовок или пояснение, например `Каталог. Ценник`;
- `responsibility_note` - сведения об ответственности: под редакцией, составитель, руководитель авторского коллектива;
- `host_title` - контейнер статьи или исходный текст `// ...`;
- `publication_place` - место издания;
- `publisher` - издательство;
- `physical_description` - страницы, иллюстрации, таблицы, формат, том и прочие физические сведения;
- `publication_details` - исходная строка библиографического описания;
- `public_review` - публичная рецензия/аннотация;
- `inferred_year` - год;
- `tags_display` - справочный текст, редактирование связей тэгов делается в `WorkTags`.

### `Authors`

Справочник авторов:

- `django_id`;
- `display_name`;
- `sort_name`;
- `aliases`;
- `note`.

Не нужно создавать автора `Без автора`. Записи без автора остаются без строк в `WorkAuthors`.

Для отвязки автора от одной конкретной книги или статьи удаляйте строку связи во вкладке `WorkAuthors`, а не строку автора во вкладке `Authors`.

Удаление строки из `Authors` считается удалением автора из справочника. При импорте связи из `WorkAuthors`, которые ссылаются на отсутствующего в `Authors` автора, пропускаются и удаляются из базы для импортируемых работ. При следующем экспорте авторы без связанных работ больше не выгружаются.

### `Sections`

Иерархия разделов печатной книги:

- `django_id`;
- `source_code`;
- `parent_django_id`;
- `parent_source_code`;
- `title`;
- `note`;
- `description`;
- `sort_order`.

### `Tags`

Справочник тэгов/указателей. Этот лист нужен, потому что тэги будут основой тематического, географического, именного и других указателей.

- `django_id`;
- `title`;
- `tag_type`;
- `parent_django_id`;
- `parent_title`;
- `sort_order`.

### `Journals`

Справочник журналов:

- `django_id`;
- `title`;
- `place`;
- `description`.

Название журнала редактируется здесь. Статьи привязываются не напрямую к журналу, а к конкретному выпуску в `JournalIssues`.

### `JournalIssues`

Выпуски журналов:

- `django_id`;
- `journal_django_id`;
- `journal_title`;
- `year`;
- `issue_number`;
- `volume`;
- `date_text`;
- `publication_details`.

Если нужно перенести выпуск в другой журнал, менять нужно `journal_django_id` или `journal_title`.

### `Collections`

Legacy-лист. Больше не используется как источник сборников.

Сборник теперь должен быть обычной записью во вкладке `Works`, а статья из сборника должна ссылаться на него через `ArticleContainers.container_work_django_id`.

При экспорте этот лист очищается до строки заголовков, чтобы старые `Collection`-записи не создавали дублей и путаницы.

Старые поля оставлены только для понимания прежней структуры:

- `django_id`;
- `title`;
- `year`;
- `place`;
- `publisher`;
- `publication_details`;
- `source_text`.

### `ArticleContainers`

Связи статей с журналами, выпусками и сборниками:

- `work_django_id`;
- `work_source_number`;
- `work_title`;
- `container_work_django_id`;
- `container_work_title`;
- `journal_issue_django_id`;
- `journal_title`;
- `journal_year`;
- `journal_issue_number`;
- `pages`.

Для одной статьи нужно указывать либо `container_work_django_id`, либо `journal_issue_django_id`.

`container_work_django_id` должен ссылаться на запись сборника во вкладке `Works`.

Старые колонки `collection_django_id` и `collection_title` при импорте ещё читаются как совместимость: если указан старый `Collection`, импорт попытается взять связанный с ним `parent_work`. Но новые экспортируемые данные эти колонки больше не содержат.

Если указан выпуск журнала, поля `journal_title`, `journal_year` и `journal_issue_number` остаются справочными и помогают ориентироваться при ручном редактировании.

### `WorkAuthors`

Связи записей с авторами. Используется отдельный лист, потому что у книги или статьи может быть несколько авторов.

- `work_django_id`;
- `work_source_number`;
- `author_django_id`;
- `author_display_name`;
- `sort_order`;
- `role`;
- `source_text`.

Чтобы убрать автора только у одной записи, удалите соответствующую строку здесь. Чтобы заменить автора, измените `author_django_id` или `author_display_name` на правильного автора из `Authors`.

### `WorkTags`

Связи записей с тэгами.

- `work_django_id`;
- `work_source_number`;
- `tag_django_id`;
- `tag_title`;
- `sort_order`;
- `source_text`.

## Правила импорта

- Импорт обновляет существующие строки по `django_id`.
- Если `django_id` пустой, импорт пытается найти запись по устойчивому ключу: `source_sequence`, `source_number`, `source_code`, имени автора или названию тэга.
- Удаление строки из Google Sheets не удаляет запись из Django.
- Для работ, авторов, разделов и тэгов можно создавать новые строки, если заполнены обязательные поля.
- Для журналов, выпусков и сборников можно создавать новые строки, если заполнены обязательные поля.
- Для `ArticleContainers` импорт создаёт или обновляет связь статьи с конкретным выпуском журнала или сборником.
- Для `WorkAuthors` и `WorkTags` импорт заменяет связи у работ, которые присутствуют во вкладке `Works`.
- Если работа есть в `Works`, но строк для неё больше нет в `WorkAuthors`, все авторы этой работы будут удалены.
- То же правило действует для `WorkTags`: если работа есть в `Works`, но строк для неё больше нет в `WorkTags`, все тэги этой работы будут удалены.
- Перед реальным импортом нужно запускать `--dry-run` или кнопку проверки в web-интерфейсе.

## Текущее решение по описанию издания

Города выпуска, издательства и физические сведения не вынесены в отдельные сущности. Они хранятся как редактируемые поля самой библиографической записи:

- `publication_place`;
- `publisher`;
- `physical_description`.

Это оставляет описание удобным для редактирования и не усложняет модель раньше времени.

Первичный импорт из `publication_details` осторожно заполняет эти поля там, где структура строки читается простыми правилами. Сырой текст остаётся в `publication_details`, чтобы можно было сверять автоматическое разбиение.
