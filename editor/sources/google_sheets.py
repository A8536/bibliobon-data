from time import sleep

from django.db.models import Count

from .models import (
    Article,
    Author,
    Journal,
    JournalIssue,
    Language,
    Section,
    Tag,
    Work,
    WorkAuthor,
    WorkGroup,
    WorkGroupItem,
    WorkTag,
)


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
WRITE_CHUNK_SIZE = 500
BATCH_UPDATE_RANGE_COUNT = 25
WRITE_RETRIES = 5

WORKS_SHEET = "Works"
AUTHORS_SHEET = "Authors"
WORK_AUTHORS_SHEET = "WorkAuthors"
SECTIONS_SHEET = "Sections"
TAGS_SHEET = "Tags"
WORK_TAGS_SHEET = "WorkTags"
JOURNALS_SHEET = "Journals"
JOURNAL_ISSUES_SHEET = "JournalIssues"
ARTICLE_PLACEMENTS_SHEET = "ArticlePlacements"
CONTAINER_WORKS_SHEET = "ContainerWorks"
WORK_GROUPS_SHEET = "WorkGroups"
WORK_GROUP_ITEMS_SHEET = "WorkGroupItems"
LANGUAGES_SHEET = "Languages"

WORK_HEADERS = [
    "work_id",
    "source_sequence",
    "source_number",
    "work_type",
    "container_index",
    "section_id",
    "section_code",
    "language_id",
    "language_code",
    "raw_author_string",
    "linked_authors",
    "title",
    "parallel_title",
    "subtitle",
    "title_remainder",
    "volume_number",
    "part_number",
    "part_title",
    "responsibility_statement",
    "edition_statement",
    "additional_edition_statement",
    "publication_place",
    "publisher",
    "publication_date",
    "year",
    "extent",
    "illustrations",
    "dimensions",
    "accompanying_material",
    "circulation",
    "physical_description",
    "article_pages",
    "page_start",
    "page_end",
    "series_statement",
    "notes",
    "raw_publication_details",
    "data_source",
    "first_seen_at",
    "updated_at",
    "description_status",
    "public_review",
    "isbn",
    "issn",
    "doi",
    "url",
]

AUTHOR_HEADERS = [
    "author_id",
    "display_name",
    "heading_name",
    "sort_name",
    "aliases",
    "person_dates",
    "authority_note",
    "note",
]

WORK_AUTHOR_HEADERS = [
    "work_id",
    "work_title",
    "author_id",
    "author_display_name",
    "sort_order",
    "role",
    "source_text",
    "name_as_printed",
    "include_in_responsibility",
    "is_primary_heading",
]

SECTION_HEADERS = [
    "section_id",
    "source_code",
    "parent_section_id",
    "parent_source_code",
    "title",
    "note",
    "description",
    "sort_order",
]

TAG_HEADERS = [
    "tag_id",
    "title",
    "tag_type",
    "parent_tag_id",
    "parent_title",
    "description",
    "sort_order",
]

WORK_TAG_HEADERS = [
    "work_id",
    "work_title",
    "tag_id",
    "tag_title",
    "sort_order",
    "source_text",
]

JOURNAL_HEADERS = [
    "journal_id",
    "title",
    "parallel_title",
    "title_remainder",
    "responsibility_statement",
    "place",
    "publisher",
    "issn",
    "periodicity",
    "numbering_start",
    "numbering_end",
    "start_year",
    "end_year",
    "title_history_note",
    "description",
]

JOURNAL_ISSUE_HEADERS = [
    "journal_issue_id",
    "journal_id",
    "journal_title",
    "title",
    "year",
    "publication_date",
    "issue_number",
    "volume",
    "part_number",
    "gross_number",
    "date_text",
    "chronology",
    "enumeration",
    "publication_place",
    "publisher",
    "raw_publication_details",
    "issn",
    "isbn",
    "notes",
]

ARTICLE_PLACEMENT_HEADERS = [
    "article_id",
    "work_id",
    "work_title",
    "container_id",
    "container_type",
    "container_title",
    "pages_raw",
    "location_note",
    "placement_note",
]

CONTAINER_WORK_HEADERS = [
    "work_id",
    "title",
    "year",
    "publication_place",
    "publisher",
    "publication_date",
    "raw_publication_details",
    "article_count",
]

WORK_GROUP_HEADERS = ["group_id", "title", "group_type", "note"]

WORK_GROUP_ITEM_HEADERS = ["group_id", "group_title", "work_id", "work_title", "sort_order"]

LANGUAGE_HEADERS = ["language_id", "code", "title", "description", "sort_order"]

SHEETS = {
    LANGUAGES_SHEET: LANGUAGE_HEADERS,
    SECTIONS_SHEET: SECTION_HEADERS,
    TAGS_SHEET: TAG_HEADERS,
    WORKS_SHEET: WORK_HEADERS,
    AUTHORS_SHEET: AUTHOR_HEADERS,
    WORK_AUTHORS_SHEET: WORK_AUTHOR_HEADERS,
    WORK_TAGS_SHEET: WORK_TAG_HEADERS,
    JOURNALS_SHEET: JOURNAL_HEADERS,
    JOURNAL_ISSUES_SHEET: JOURNAL_ISSUE_HEADERS,
    ARTICLE_PLACEMENTS_SHEET: ARTICLE_PLACEMENT_HEADERS,
    WORK_GROUPS_SHEET: WORK_GROUP_HEADERS,
    WORK_GROUP_ITEMS_SHEET: WORK_GROUP_ITEM_HEADERS,
}


def clean_cell(value):
    if value is None:
        return ""
    return str(value).strip()


def int_or_none(value):
    value = clean_cell(value)
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Ожидалось целое число: {value!r}") from exc


def bool_cell(value):
    return clean_cell(value).lower() in {"1", "true", "yes", "y", "да", "истина"}


def row_dict(headers, row):
    return {header: clean_cell(row[index]) if index < len(row) else "" for index, header in enumerate(headers)}


def get_sheets_service(credentials_file):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    credentials = service_account.Credentials.from_service_account_file(credentials_file, scopes=SCOPES)
    return build("sheets", "v4", credentials=credentials)


def sheet_range(sheet_name, end_column="ZZ"):
    return f"'{sheet_name}'!A1:{end_column}"


def values_range(sheet_name, start_row, column_count, row_count):
    end_column = column_letter(column_count)
    end_row = start_row + row_count - 1
    return f"'{sheet_name}'!A{start_row}:{end_column}{end_row}"


def column_letter(column_number):
    letters = ""
    while column_number:
        column_number, remainder = divmod(column_number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters or "A"


def execute_google_request(request_factory):
    for attempt in range(WRITE_RETRIES):
        try:
            return request_factory().execute()
        except Exception as exc:
            if not is_rate_limit_error(exc) or attempt == WRITE_RETRIES - 1:
                raise
            sleep(2**attempt)
    return None


def is_rate_limit_error(exc):
    status = getattr(getattr(exc, "resp", None), "status", None)
    text = str(exc)
    return (
        status in {429, 500, 502, 503, 504}
        or "RATE_LIMIT_EXCEEDED" in text
        or "Quota exceeded" in text
        or "Connection reset by peer" in text
        or "[Errno 22]" in text
        or "[Errno 54]" in text
        or "temporarily unavailable" in text.casefold()
    )


def get_spreadsheet_metadata(service, spreadsheet_id):
    return service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()


def ensure_sheets(service, spreadsheet_id):
    metadata = get_spreadsheet_metadata(service, spreadsheet_id)
    existing_titles = {sheet["properties"]["title"] for sheet in metadata.get("sheets", [])}
    requests = [{"addSheet": {"properties": {"title": title}}} for title in SHEETS if title not in existing_titles]
    if requests:
        execute_google_request(lambda: service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}))


def read_sheet_values(service, spreadsheet_id):
    response = execute_google_request(
        lambda: service.spreadsheets().values().batchGet(
            spreadsheetId=spreadsheet_id,
            ranges=[sheet_range(sheet_name) for sheet_name in SHEETS],
        )
    )
    return {
        sheet_name: value_range.get("values", [])
        for sheet_name, value_range in zip(SHEETS, response.get("valueRanges", []))
    }


def write_sheet_values(service, spreadsheet_id, values_by_sheet):
    ensure_sheets(service, spreadsheet_id)
    resize_sheets(service, spreadsheet_id, values_by_sheet)
    execute_google_request(
        lambda: service.spreadsheets().values().batchClear(
            spreadsheetId=spreadsheet_id,
            body={"ranges": [sheet_range(sheet_name) for sheet_name in SHEETS]},
        )
    )
    for batch in build_value_update_batches(values_by_sheet):
        execute_google_request(
            lambda batch=batch: service.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"valueInputOption": "RAW", "data": batch},
            )
    )


def resize_sheets(service, spreadsheet_id, values_by_sheet):
    metadata = get_spreadsheet_metadata(service, spreadsheet_id)
    requests = []
    for sheet in metadata.get("sheets", []):
        properties = sheet["properties"]
        title = properties["title"]
        if title not in SHEETS:
            continue
        values = values_by_sheet.get(title, [SHEETS[title]]) or [SHEETS[title]]
        row_count = max(len(values), 1)
        column_count = max(max(len(row) for row in values), len(SHEETS[title]))
        grid = properties.get("gridProperties", {})
        if grid.get("rowCount", 0) < row_count or grid.get("columnCount", 0) < column_count:
            requests.append(
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": properties["sheetId"],
                            "gridProperties": {
                                "rowCount": row_count,
                                "columnCount": column_count,
                            },
                        },
                        "fields": "gridProperties.rowCount,gridProperties.columnCount",
                    }
                }
            )
    if requests:
        execute_google_request(
            lambda: service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": requests},
            )
        )


def build_value_update_batches(values_by_sheet):
    updates = []
    for sheet_name, headers in SHEETS.items():
        values = values_by_sheet.get(sheet_name, [headers]) or [headers]
        column_count = max(len(row) for row in values) if values else len(headers)
        for index in range(0, len(values), WRITE_CHUNK_SIZE):
            chunk = values[index : index + WRITE_CHUNK_SIZE]
            updates.append({"range": values_range(sheet_name, index + 1, column_count, len(chunk)), "values": chunk})
    return [updates[index : index + BATCH_UPDATE_RANGE_COUNT] for index in range(0, len(updates), BATCH_UPDATE_RANGE_COUNT)]


def build_export_values():
    works = list(Work.objects.select_related("source_section", "language", "target_source", "article", "article__container_work", "article__journal_issue").order_by("source_sequence", "source_number", "work_id"))
    attach_linked_authors(works)
    languages = Language.objects.order_by("sort_order", "code")
    sections = Section.objects.select_related("parent").order_by("sort_order", "source_code")
    tags = Tag.objects.select_related("parent").order_by("sort_order", "title")
    authors = Author.objects.order_by("sort_name", "display_name", "author_id")
    work_authors = WorkAuthor.objects.select_related("work", "author").order_by("work__source_sequence", "work__source_number", "sort_order", "id")
    work_tags = WorkTag.objects.select_related("work", "tag").order_by("work__source_sequence", "work__source_number", "sort_order", "id")
    journals = Journal.objects.order_by("title", "journal_id")
    issues = JournalIssue.objects.select_related("journal").order_by("journal__title", "year", "issue_number", "volume")
    articles = Article.objects.select_related("work", "journal_issue", "journal_issue__journal", "container_work").order_by("work__source_sequence", "work__source_number", "work_id")
    groups = WorkGroup.objects.order_by("title", "group_id")
    group_items = WorkGroupItem.objects.select_related("group", "work").order_by("group__title", "sort_order", "work__title")

    return {
        LANGUAGES_SHEET: [LANGUAGE_HEADERS] + [language_to_row(obj) for obj in languages],
        SECTIONS_SHEET: [SECTION_HEADERS] + [section_to_row(obj) for obj in sections],
        TAGS_SHEET: [TAG_HEADERS] + [tag_to_row(obj) for obj in tags],
        WORKS_SHEET: [WORK_HEADERS] + [work_to_row(obj) for obj in works],
        AUTHORS_SHEET: [AUTHOR_HEADERS] + [author_to_row(obj) for obj in authors],
        WORK_AUTHORS_SHEET: [WORK_AUTHOR_HEADERS] + [work_author_to_row(obj) for obj in work_authors],
        WORK_TAGS_SHEET: [WORK_TAG_HEADERS] + [work_tag_to_row(obj) for obj in work_tags],
        JOURNALS_SHEET: [JOURNAL_HEADERS] + [journal_to_row(obj) for obj in journals],
        JOURNAL_ISSUES_SHEET: [JOURNAL_ISSUE_HEADERS] + [issue_to_row(obj) for obj in issues],
        ARTICLE_PLACEMENTS_SHEET: [ARTICLE_PLACEMENT_HEADERS] + [article_to_row(obj) for obj in articles],
        WORK_GROUPS_SHEET: [WORK_GROUP_HEADERS] + [group_to_row(obj) for obj in groups],
        WORK_GROUP_ITEMS_SHEET: [WORK_GROUP_ITEM_HEADERS] + [group_item_to_row(obj) for obj in group_items],
    }


def work_to_row(work):
    return [
        work.work_id,
        work.source_sequence or "",
        work.source_number,
        work.work_type,
        work_container_index(work),
        work.source_section_id or "",
        work.source_section.source_code if work.source_section_id else "",
        work.language_id,
        work.language.code,
        work.raw_author_string,
        linked_authors_text(work),
        work.title,
        work.parallel_title,
        work.subtitle,
        work.title_remainder,
        work.volume_number,
        work.part_number,
        work.part_title,
        work.responsibility_statement,
        work.edition_statement,
        work.additional_edition_statement,
        work.publication_place,
        work.publisher,
        work.publication_date,
        work.inferred_year or "",
        work.extent,
        work.illustrations,
        work.dimensions,
        work.accompanying_material,
        work.circulation,
        work.physical_description,
        work.article_pages,
        work.page_start or "",
        work.page_end or "",
        work.series_statement,
        work.notes,
        target_raw_publication_details(work),
        target_data_source(work),
        target_first_seen_at(work),
        target_updated_at(work),
        work.description_status,
        work.public_review,
        work.isbn,
        work.issn,
        work.doi,
        work.url,
    ]


def target_source(work):
    return getattr(work, "target_source", None)


def target_raw_publication_details(work):
    source = target_source(work)
    if source and source.raw_publication_details:
        return source.raw_publication_details
    return work.publication_details


def target_data_source(work):
    source = target_source(work)
    return source.data_source if source else ""


def target_first_seen_at(work):
    source = target_source(work)
    return source.first_seen_at.isoformat() if source and source.first_seen_at else ""


def target_updated_at(work):
    source = target_source(work)
    return source.updated_at.isoformat() if source and source.updated_at else ""


def work_container_index(work):
    article = getattr(work, "article", None)
    if not article:
        return ""
    if article.container_work_id:
        return f"container:{article.container_work_id}"
    if article.journal_issue_id:
        return f"issue:{article.journal_issue_id}"
    return ""


def linked_authors_text(work):
    relations = getattr(work, "linked_author_relations", [])
    values = []
    for relation in relations:
        text = relation.name_as_printed or relation.author.display_name
        if relation.role:
            text = f"{text} [{relation.role}]"
        values.append(text)
    return "; ".join(values)


def attach_linked_authors(works):
    by_work_id = {work.work_id: [] for work in works}
    for relation in WorkAuthor.objects.select_related("author").order_by("work_id", "sort_order", "id"):
        if relation.work_id in by_work_id:
            by_work_id[relation.work_id].append(relation)
    for work in works:
        work.linked_author_relations = by_work_id.get(work.work_id, [])


def author_to_row(author):
    return [author.author_id, author.display_name, author.heading_name, author.sort_name, author.aliases, author.person_dates, author.authority_note, author.note]


def work_author_to_row(link):
    return [link.work_id, link.work.title, link.author_id, link.author.display_name, link.sort_order, link.role, link.source_text, link.name_as_printed, int(link.include_in_responsibility), int(link.is_primary_heading)]


def section_to_row(section):
    return [section.section_id, section.source_code, section.parent_id or "", section.parent.source_code if section.parent_id else "", section.title, section.note, section.description, section.sort_order]


def tag_to_row(tag):
    return [tag.tag_id, tag.title, tag.tag_type, tag.parent_id or "", tag.parent.title if tag.parent_id else "", tag.description, tag.sort_order]


def work_tag_to_row(link):
    return [link.work_id, link.work.title, link.tag_id, link.tag.title, link.sort_order, link.source_text]


def language_to_row(language):
    return [language.language_id, language.code, language.title, language.description, language.sort_order]


def journal_to_row(journal):
    return [journal.journal_id, journal.title, journal.parallel_title, journal.title_remainder, journal.responsibility_statement, journal.place, journal.publisher, journal.issn, journal.periodicity, journal.numbering_start, journal.numbering_end, journal.start_year or "", journal.end_year or "", journal.title_history_note, journal.description]


def issue_to_row(issue):
    return [issue.journal_issue_id, issue.journal_id, issue.journal.title, issue.title, issue.year or "", issue.publication_date, issue.issue_number, issue.volume, issue.part_number, issue.gross_number, issue.date_text, issue.chronology, issue.enumeration, issue.publication_place, issue.publisher, issue.publication_details, issue.issn, issue.isbn, issue.notes]


def article_to_row(article):
    issue = article.journal_issue
    container = article.container_work
    container_id = ""
    container_type = ""
    container_title = ""
    if issue:
        container_id = issue.journal_issue_id
        container_type = "journal_issue"
        container_title = describe_journal_issue(issue)
    elif container:
        container_id = container.work_id
        container_type = "container_work"
        container_title = container.title
    return [
        article.article_id,
        article.work_id,
        article.work.title,
        container_id,
        container_type,
        container_title,
        article.pages_raw,
        article.location_note,
        article.placement_note,
    ]


def describe_journal_issue(issue):
    bits = [issue.journal.title]
    if issue.year:
        bits.append(str(issue.year))
    if issue.issue_number:
        bits.append(f"№ {issue.issue_number}")
    if issue.volume:
        bits.append(f"т. {issue.volume}")
    return ", ".join(bits)


def container_to_row(work):
    return [work.work_id, work.title, work.inferred_year or "", work.publication_place, work.publisher, work.publication_date, work.publication_details, work.article_count]


def group_to_row(group):
    return [group.group_id, group.title, group.group_type, group.note]


def group_item_to_row(item):
    return [item.group_id, item.group.title, item.work_id, item.work.title, item.sort_order]


def language_to_row(language):
    return [language.language_id, language.code, language.title, language.description, language.sort_order]
