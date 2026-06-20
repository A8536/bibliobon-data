import hashlib
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from shutil import copy2

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Max
from django.utils import timezone

from .models import (
    Article,
    Author,
    Book,
    Collection,
    ImportApplyLog,
    ImportBatch,
    ImportDecision,
    ImportEntity,
    ImportEntityRelation,
    ImportGroup,
    ImportItem,
    ImportMatch,
    Journal,
    JournalIssue,
    Language,
    Section,
    Source,
    Work,
    WorkAuthor,
)


def normalize_whitespace(value):
    return re.sub(r"\s+", " ", (value or "").replace("\u00a0", " ")).strip()


def remove_punctuation_noise(value):
    value = (value or "").lower().replace("ё", "е")
    value = value.replace("\u00ad", "")
    value = re.sub(r"[«»„“”\"'`]", "", value)
    value = re.sub(r"[.,;:()\[\]{}]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_title(value):
    return remove_punctuation_noise(normalize_dashes(value))


def normalize_author_name(value):
    value = normalize_title(value)
    value = re.sub(r"\b([а-яa-z])\s+([а-яa-z])\b", r"\1\2", value)
    return value


def normalize_journal_title(value):
    return normalize_title(value)


def normalize_issue_number(value):
    value = normalize_dashes(value or "").lower()
    value = re.sub(r"(?:\b(?:n|no|номер)\b|№)\s*", "", value)
    value = re.sub(r"\s+", "", value)
    return value.strip(" .")


def normalize_year(value):
    match = re.search(r"(17|18|19|20)\d{2}", str(value or ""))
    return match.group(0) if match else ""


def normalize_pages(value):
    value = normalize_dashes(value or "").lower()
    value = re.sub(r"\b(с|стр|страницы)\.?\s*", "", value)
    value = re.sub(r"\s+", "", value)
    return value.strip(" .")


def normalize_extent(value):
    value = normalize_dashes(value or "").lower().replace("\u00a0", " ")
    value = re.sub(r"\b(с|стр|страницы)\.?\b", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" .")


def normalize_dimensions(value):
    value = normalize_dashes(value or "").lower().replace("\u00a0", " ")
    value = value.replace("×", "х").replace("x", "х")
    value = re.sub(r"\s+", "", value)
    return value.strip(" .")


def normalize_dashes(value):
    return (value or "").replace("–", "-").replace("—", "-").replace("−", "-")


CONTRIBUTOR_ROLE_LABELS_RU = {
    "author": "автор",
    "editor": "редактор",
    "responsible_editor": "ответственный редактор",
    "translator": "переводчик",
    "compiler": "составитель",
    "commentator": "комментатор",
    "illustrator": "художник / иллюстратор",
    "organization": "организация",
    "other": "другая роль",
}

CONTRIBUTOR_ROLE_ALIASES = {
    "авт": "author",
    "author": "author",
    "ред": "editor",
    "editor": "editor",
    "под ред": "editor",
    "общ ред": "responsible_editor",
    "под общ ред": "responsible_editor",
    "ответственный редактор": "responsible_editor",
    "отв ред": "responsible_editor",
    "пер": "translator",
    "перевод": "translator",
    "translator": "translator",
    "сост": "compiler",
    "составитель": "compiler",
    "compiler": "compiler",
    "коммент": "commentator",
    "комментарии": "commentator",
    "ил": "illustrator",
    "илл": "illustrator",
    "худ": "illustrator",
    "организация": "organization",
    "organization": "organization",
}


def normalize_contributor_role(value):
    normalized = normalize_title(value)
    normalized = re.sub(r"\bи\b", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return CONTRIBUTOR_ROLE_ALIASES.get(normalized, "other" if normalized else "")


def contributor_role_label_ru(role):
    if role in CONTRIBUTOR_ROLE_LABELS_RU:
        return CONTRIBUTOR_ROLE_LABELS_RU[role]
    role = normalize_contributor_role(role) or role
    return CONTRIBUTOR_ROLE_LABELS_RU.get(role, role or "")


def describe_existing_entity(existing_type, existing_id):
    fallback = normalize_whitespace(f"{existing_type} {existing_id}")
    if not existing_type or not existing_id:
        return fallback
    if existing_type == "author":
        try:
            author = Author.objects.annotate(work_count=Count("works")).get(author_id=existing_id)
        except Author.DoesNotExist:
            return fallback
        suffix = f" (работ: {author.work_count})" if author.work_count else ""
        return f"{author.display_name}{suffix}"
    if existing_type == "work":
        try:
            work = Work.objects.prefetch_related("authors").get(work_id=existing_id)
        except Work.DoesNotExist:
            return fallback
        author_text = "; ".join(author.display_name for author in work.authors.all())
        year = work.inferred_year or normalize_year(work.publication_date)
        bits = [bit for bit in [author_text, work.title, str(year) if year else ""] if bit]
        return " — ".join(bits) or fallback
    if existing_type == "journal":
        try:
            journal = Journal.objects.annotate(issue_count=Count("issues"), article_count=Count("issues__articles")).get(journal_id=existing_id)
        except Journal.DoesNotExist:
            return fallback
        count_bits = []
        if journal.issue_count:
            count_bits.append(f"{journal.issue_count} вып.")
        if journal.article_count:
            count_bits.append(f"{journal.article_count} ст.")
        suffix = f" ({', '.join(count_bits)})" if count_bits else ""
        return f"{journal.title}{suffix}"
    if existing_type == "journal_issue":
        try:
            issue = JournalIssue.objects.select_related("journal").get(journal_issue_id=existing_id)
        except JournalIssue.DoesNotExist:
            return fallback
        bits = [issue.journal.title]
        if issue.year:
            bits.append(str(issue.year))
        if issue.issue_number:
            bits.append(f"№ {issue.issue_number}")
        return " — ".join(bits) or fallback
    if existing_type == "collection":
        try:
            collection = Collection.objects.get(collection_id=existing_id)
        except Collection.DoesNotExist:
            return fallback
        bits = [collection.title]
        if collection.year:
            bits.append(str(collection.year))
        return " — ".join(bits) or fallback
    return fallback


def split_raw_records(raw_input):
    records = []
    buffer = []
    for line in (raw_input or "").splitlines():
        stripped = normalize_whitespace(line)
        if not stripped:
            if buffer:
                records.append(normalize_whitespace(" ".join(buffer)))
                buffer = []
            continue
        if (re.match(r"^\d+[\).\t ]+", stripped) or looks_like_record_start(stripped)) and buffer:
            records.append(normalize_whitespace(" ".join(buffer)))
            buffer = []
        buffer.append(stripped)
    if buffer:
        records.append(normalize_whitespace(" ".join(buffer)))
    return [strip_record_number(record) for record in records if record]


def looks_like_record_start(value):
    return bool(re.match(r"^[А-ЯЁA-Z][а-яёa-zA-Z'’\-]+(?:\s*[А-ЯA-Z]\.){1,2}\s+", value or ""))


def strip_record_number(value):
    return re.sub(r"^\d+[\).\t ]+", "", value or "").strip()


@dataclass
class ParsedRecord:
    detected_type: str
    data: dict
    confidence: float
    errors: list


def parse_record(raw_text):
    text = normalize_whitespace(normalize_dashes(raw_text))
    if "//" in text:
        return parse_article_record(text)
    return parse_book_record(text)


def parse_article_record(text):
    left, right = text.split("//", 1)
    authors, title = split_authors_title(left)
    host = parse_host_details(right)
    if host["issue_number"] or looks_like_journal(host["container_title"]):
        detected_type = ImportItem.DetectedType.JOURNAL_ARTICLE
    else:
        detected_type = ImportItem.DetectedType.COLLECTION_ARTICLE
    # raw_host is the full right-hand side after //; raw_parent_description is
    # the parent/container description. They currently match for journal
    # articles because page stripping is still handled separately.
    data = {
        "authors": authors,
        "title": title,
        "journal_title": host["container_title"] if detected_type == ImportItem.DetectedType.JOURNAL_ARTICLE else "",
        "collection_title": host["container_title"] if detected_type == ImportItem.DetectedType.COLLECTION_ARTICLE else "",
        "year": host["year"],
        "issue_number": host["issue_number"],
        "pages": host["pages"],
        "publication_place": host["publication_place"],
        "parent_title": host["container_title"],
        "parent_place": host["publication_place"],
        "parent_year": host["year"],
        "raw_parent_description": normalize_whitespace(right),
        "raw_host": normalize_whitespace(right),
    }
    confidence = 0.82 if title and host["container_title"] else 0.55
    return ParsedRecord(detected_type, data, confidence, [] if confidence >= 0.7 else ["Не все поля статьи распознаны уверенно."])


def parse_book_record(text):
    text, notes = extract_brace_notes(text)
    authors, title_and_tail = split_authors_title(text)
    title = title_and_tail
    title_remainder = ""
    responsibility = ""
    edition_statement = ""
    publication_place = ""
    publisher = ""
    year = ""
    extent = ""
    dimensions = ""
    parts = [part.strip(" .") for part in re.split(r"\s+-\s+|\.\s+-\s+", title_and_tail) if part.strip()]
    if parts:
        title = parts[0]
    tail = " - ".join(parts[1:]) if len(parts) > 1 else title_and_tail
    if len(parts) <= 1:
        sentence_pub_match = re.match(
            r"^(?P<title>.+?)\.\s+(?P<place>[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z.\- ]{0,80})[:,]\s*(?P<publisher>.*?)(?P<year>(17|18|19|20)\d{2})(?:\.\s*(?P<extent>\d+\s*с\.?))?",
            title_and_tail,
        )
        if sentence_pub_match and plausible_publication_place(sentence_pub_match.group("place")):
            title = sentence_pub_match.group("title").strip(" .")
            tail = title_and_tail[sentence_pub_match.start("place") :]
            publication_place = sentence_pub_match.group("place").strip(" .")
            publisher = sentence_pub_match.group("publisher").strip(" .,")
            year = sentence_pub_match.group("year")
            if sentence_pub_match.group("extent"):
                extent = sentence_pub_match.group("extent").strip(" .")
    title, title_edition = extract_edition_statement(title)
    edition_statement = title_edition or edition_statement
    tail, tail_editions = extract_edition_segments_from_tail(tail)
    if tail_editions and not edition_statement:
        edition_statement = tail_editions[0]
    title = strip_trailing_year(title, year or normalize_year(tail))
    title, responsibility = split_title_responsibility(title)
    title, title_edition = extract_edition_statement(title)
    edition_statement = edition_statement or title_edition
    title, title_remainder = split_title_remainder(title)
    pub_match = re.search(
        r"(?P<place>[А-ЯA-ZЁа-яёA-Za-z.,\- ]{1,80})[:]\s*(?P<publisher>.*?)(?P<year>(17|18|19|20)\d{2})",
        tail,
    )
    if not pub_match:
        pub_match = re.search(r"(?P<place>[А-ЯA-ZЁа-яёA-Za-z.\- ]{1,80})[:,]\s*(?P<publisher>.*?)(?P<year>(17|18|19|20)\d{2})", tail)
    if pub_match and not plausible_publication_place(pub_match.group("place")):
        pub_match = None
    if pub_match and not year:
        publication_place = pub_match.group("place").strip(" .")
        publisher = pub_match.group("publisher").strip(" .,")
        year = pub_match.group("year")
    elif not year:
        year = normalize_year(tail)
        place_match = re.search(r"([А-ЯA-ZЁА-Яа-яёA-Za-z.\- ]{1,80})[,.:]\s*(17|18|19|20)\d{2}", tail)
        if place_match and plausible_publication_place(place_match.group(1)):
            publication_place = place_match.group(1).strip(" .")
    extent_match = re.search(r"((?:\[[^\]]+\],\s*)?(?:\d+\s*,\s*)?\d+\s*с\.?|[IVXLCDM]+,\s*\d+\s*с\.?)", text, flags=re.I)
    if extent_match:
        extent = extent_match.group(1).strip(" .")
    dimensions_match = re.search(r"(\d+\s*[xх×]\s*\d+\s*мм\.?)", text, flags=re.I)
    if dimensions_match:
        dimensions = normalize_whitespace(dimensions_match.group(1)).strip(" .")
    data = {
        "authors": authors,
        "title": title.strip(" ."),
        "title_remainder": title_remainder,
        "responsibility_statement": responsibility,
        "responsibility_contributors": extract_responsibility_contributors(responsibility),
        "edition_statement": edition_statement,
        "publication_place": publication_place,
        "publisher": publisher,
        "year": year,
        "extent": extent,
        "dimensions": dimensions,
        "notes": notes,
    }
    confidence = 0.8 if title and year else 0.55
    return ParsedRecord(ImportItem.DetectedType.BOOK, data, confidence, [] if confidence >= 0.7 else ["Не все поля книги распознаны уверенно."])


def plausible_publication_place(value):
    normalized = normalize_whitespace(value).strip(" .").lower()
    normalized = normalized.replace("с.-петербург", "спб")
    known = {
        "б.м",
        "м",
        "москва",
        "спб",
        "спб",
        "пг",
        "петроград",
        "ленинград",
        "киев",
        "харьков",
        "волгоград",
        "горький",
        "новосибирск",
    }
    if "," in normalized:
        return all(plausible_publication_place(part) for part in normalized.split(",") if part.strip())
    return normalized in known or normalized.startswith(("спб", "москва", "пг", "петроград"))


def strip_trailing_year(title, year):
    title = normalize_whitespace(title).strip(" .")
    if year:
        title = re.sub(rf"(?:[.,;]\s*)?{re.escape(str(year))}\s*$", "", title).strip(" .")
    return title


def split_title_responsibility(title):
    title = normalize_whitespace(title).strip(" .")
    if " / " not in title:
        return title, ""
    main, responsibility = title.split(" / ", 1)
    return normalize_whitespace(main).strip(" ."), normalize_whitespace(responsibility).strip(" .")


def extract_edition_segments_from_tail(tail):
    segments = [part.strip(" .") for part in re.split(r"\s+-\s+", tail or "") if part.strip(" .")]
    kept = []
    editions = []
    for segment in segments:
        edition = edition_statement_from_text(segment)
        if edition and normalize_title(edition) == normalize_title(segment):
            editions.append(edition)
        else:
            kept.append(segment)
    return " - ".join(kept), editions


def extract_edition_statement(title):
    title = normalize_whitespace(title).strip(" .")
    if not title:
        return title, ""
    matches = list(re.finditer(EDITION_PATTERN, title, flags=re.I))
    if not matches:
        return title, ""
    match = matches[-1]
    suffix = title[match.end():].strip(" .")
    if suffix:
        return title, ""
    edition = normalize_edition_statement_text(match.group(0))
    cleaned = normalize_whitespace((title[:match.start()] + " " + title[match.end():]).strip(" ."))
    return cleaned, edition


EDITION_PATTERN = r"(?:\d+\s*-\s*е|[IVXLCDM]+)\s+изд\.?(?:\s+[а-яёa-z.]+){0,5}"


def edition_statement_from_text(value):
    match = re.fullmatch(EDITION_PATTERN, normalize_whitespace(value).strip(" ."), flags=re.I)
    return normalize_edition_statement_text(match.group(0)) if match else ""


def normalize_edition_statement_text(value):
    value = normalize_whitespace(value).strip()
    value = re.sub(r"\b(изд|доп|уточн)\b(?!\.)", r"\1.", value, flags=re.I)
    return value


def split_title_remainder(title):
    title, colon_remainder = split_main_title_and_remainder(title)
    if colon_remainder:
        return title, colon_remainder
    pieces = [part.strip(" .") for part in re.split(r"\.\s+", title) if part.strip(" .")]
    if len(pieces) >= 3 and title_sentence_has_date_range(pieces[1]) and catalog_remainder_sentence(pieces[2]):
        return ". ".join(pieces[:2]).strip(" ."), ". ".join(pieces[2:]).strip(" .")
    if len(pieces) >= 2 and should_split_sentence_title(pieces[0], ". ".join(pieces[1:])):
        return pieces[0].strip(" ."), ". ".join(pieces[1:]).strip(" .")
    return title, ""


def should_split_sentence_title(main, remainder):
    if not main or not remainder:
        return False
    main_tokens = re.findall(r"[\wА-Яа-яЁё]+", main)
    if len(main_tokens) > 4 and not catalog_remainder_sentence(remainder):
        return False
    if plausible_publication_place(remainder.split(",", 1)[0]):
        return False
    return True


def title_sentence_has_date_range(value):
    return bool(re.search(r"\b(17|18|19|20)\d{2}\s*-\s*(17|18|19|20)\d{2}\b", normalize_dashes(value or "")))


def catalog_remainder_sentence(value):
    normalized = normalize_title(value)
    return bool(re.search(r"\b(каталог|альбом каталог|альбом)\b", normalized))


def extract_responsibility_contributors(responsibility):
    responsibility = normalize_whitespace(responsibility).strip(" ./")
    if not responsibility:
        return []
    patterns = [
        (r"под\s+общ\.?\s+ред\.?\s+(.+)", "responsible_editor"),
        (r"под\s+ред\.?\s+(.+)", "editor"),
        (r"ред\.?\s+(.+)", "editor"),
        (r"пер\.?\s+(?:с\s+[а-яё]+\s+)?(.+)", "translator"),
        (r"сост\.?\s+(.+)", "compiler"),
        (r"коммент\.?\s+(.+)", "commentator"),
    ]
    for pattern, role in patterns:
        match = re.search(pattern, responsibility, flags=re.I)
        if match:
            names = split_responsibility_names(match.group(1))
            return [{"name": name, "role": role, "role_label": contributor_role_label_ru(role)} for name in names]
    return []


def split_responsibility_names(value):
    value = normalize_whitespace(value).strip(" .")
    value = re.sub(r"^(?:д-ра|канд\.?|проф\.?)\s+", "", value, flags=re.I)
    return [part.strip(" .") for part in re.split(r"\s*;\s*|\s*,\s*(?=[А-ЯЁA-Z]\.)", value) if part.strip(" .")]


def extract_brace_notes(text):
    notes = []

    def collect(match):
        note = normalize_whitespace(match.group(1)).strip(" ;")
        if note:
            notes.append(note)
        return " "

    cleaned = re.sub(r"\{([^{}]+)\}", collect, text or "")
    return normalize_whitespace(cleaned), "; ".join(notes)


def split_main_title_and_remainder(title):
    title = normalize_whitespace(title).strip(" .")
    if ":" not in title:
        return title, ""
    main, remainder = title.split(":", 1)
    main = normalize_whitespace(main).strip(" .")
    remainder = normalize_whitespace(remainder).strip(" .")
    if not main or not remainder:
        return title, ""
    return main, remainder


def split_authors_title(value):
    value = normalize_whitespace(value).strip(" .")
    author_pattern = r"[А-ЯЁA-Z][а-яёa-zA-Z'’\-]+(?:\s*[А-ЯA-Z]\.){1,2}"
    match = re.match(rf"^(?P<authors>(?:{author_pattern}(?:\s*,?\s*)?)+(?:\s*;\s*(?:{author_pattern}))*?)\s+(?P<title>.+)$", value)
    if not match:
        return [], value
    authors_text = match.group("authors")
    authors = split_author_list(authors_text)
    return authors, match.group("title").strip(" .")


def split_author_list(value):
    text = normalize_whitespace(value).strip(" ;,")
    if not text:
        return []
    separator = r"\s*(?:;|,(?=\s*[А-ЯЁA-Z][а-яёa-zA-Z'’\-]+\s*[А-ЯA-Z]\.))\s*"
    return [normalize_whitespace(part).strip(" ;,") for part in re.split(separator, text) if part.strip(" ;,")]


def parse_host_details(value):
    text = normalize_whitespace(normalize_dashes(value)).strip(" .")
    pages = ""
    pages_match = re.search(r"(?:С\.?|стр\.?)\s*(\d+\s*[-–—]\s*\d+|\d+)", text, flags=re.I)
    if pages_match:
        pages = normalize_pages(pages_match.group(1))
        text = text[: pages_match.start()].strip(" .")
    issue_number = ""
    issue_match = re.search(r"(?:№|N|No\.?|номер)\s*([0-9IVXLCDM]+(?:\s*[-–—]\s*[0-9IVXLCDM]+)?)", text, flags=re.I)
    if issue_match:
        issue_number = normalize_issue_number(issue_match.group(1))
    year = normalize_year(text)
    container_title = text
    if year:
        container_title = re.split(r"\b" + re.escape(year) + r"\b", container_title, maxsplit=1)[0]
    container_title = re.split(r"(?:№|N|No\.?|номер)\s*", container_title, maxsplit=1, flags=re.I)[0]
    publication_place = ""
    place_match = re.search(r"\b([А-ЯЁA-Z][А-ЯЁа-яёA-Za-z.\-]+)\s*,\s*(17|18|19|20)\d{2}\b", text)
    if place_match and not issue_number:
        publication_place = place_match.group(1)
        container_title = text[: place_match.start()].strip(" .")
    return {
        "container_title": container_title.strip(" .-"),
        "year": year,
        "issue_number": issue_number,
        "pages": pages,
        "publication_place": publication_place,
    }


def looks_like_journal(title):
    title_norm = normalize_title(title)
    return any(word in title_norm for word in ["журнал", "вестник", "бонист", "нумизмат", "известия"])


@transaction.atomic
def parse_import_batch(import_batch):
    clear_import_batch_parse(import_batch)
    work_index = build_work_match_index()
    author_index = build_author_match_index()
    for raw_text in split_raw_records(import_batch.raw_input):
        parsed = parse_record(raw_text)
        normalized = normalize_parsed_data(parsed)
        item = ImportItem.objects.create(
            import_batch=import_batch,
            raw_text=raw_text,
            detected_type=parsed.detected_type,
            status=ImportItem.Status.NEEDS_REVIEW if parsed.errors else ImportItem.Status.PARSED,
            confidence=parsed.confidence,
            parsed_data_json=parsed.data,
            normalized_data_json=normalized,
            errors_json=parsed.errors,
        )
        existing_work = find_core_work_match(parsed, work_index=work_index)
        if existing_work and parsed.detected_type != ImportItem.DetectedType.JOURNAL_ARTICLE:
            mark_item_existing_match(item, existing_work, parsed)
            continue
        create_entities_for_item(item)
    create_import_matches(import_batch, work_index=work_index, author_index=author_index)
    create_import_groups(import_batch)
    recalculate_import_status(import_batch)
    import_batch.parsed_at = timezone.now()
    import_batch.save(update_fields=["status", "parsed_at", "updated_at"])
    return import_batch


def clear_import_batch_parse(import_batch):
    ImportApplyLog.objects.filter(import_batch=import_batch).delete()
    ImportDecision.objects.filter(import_batch=import_batch).delete()
    ImportMatch.objects.filter(import_batch=import_batch).delete()
    ImportGroup.objects.filter(import_batch=import_batch).delete()
    ImportEntityRelation.objects.filter(import_batch=import_batch).delete()
    ImportEntity.objects.filter(import_batch=import_batch).delete()
    ImportItem.objects.filter(import_batch=import_batch).delete()


def normalize_parsed_data(parsed):
    data = parsed.data
    return {
        "title": normalize_title(data.get("title")),
        "authors": [normalize_author_name(author) for author in data.get("authors", [])],
        "journal_title": normalize_journal_title(data.get("journal_title")),
        "collection_title": normalize_title(data.get("collection_title")),
        "year": normalize_year(data.get("year")),
        "issue_number": normalize_issue_number(data.get("issue_number")),
        "pages": normalize_pages(data.get("pages")),
    }


def build_work_match_index():
    rows = []
    for work in Work.objects.prefetch_related("authors").iterator(chunk_size=500):
        authors = list(work.authors.all())
        rows.append(
            {
                "work": work,
                "authors": authors,
                "author_names": {normalize_author_name(author.display_name) for author in authors},
                "main_title": normalize_match_title(work.title),
                "combined_title": normalize_title(work_combined_title(work)),
                "year": str(work.inferred_year or normalize_year(work.publication_date) or ""),
                "year_missing": not work.inferred_year and not normalize_year(work.publication_date),
            }
        )
    return rows


def build_author_match_index():
    return [
        {
            "author": author,
            "normalized_name": normalize_author_name(author.display_name),
            "work_count": author.work_count,
        }
        for author in Author.objects.annotate(work_count=Count("works")).iterator(chunk_size=500)
    ]


def work_combined_title(work):
    return normalize_whitespace(
        " ".join(
            bit
            for bit in [
                work.title,
                work.subtitle,
                work.title_remainder,
                work.responsibility_statement or work.responsibility_note,
            ]
            if bit
        )
    )


def parsed_combined_title(data):
    return normalize_whitespace(
        " ".join(
            bit
            for bit in [
                data.get("title", ""),
                data.get("title_remainder", ""),
                data.get("responsibility_statement", ""),
            ]
            if bit
        )
    )


def find_core_work_match(parsed, work_index=None):
    data = parsed.data
    title = normalize_match_title(data.get("title"))
    combined_title = normalize_title(parsed_combined_title(data))
    year = normalize_year(data.get("year"))
    author_names = set(normalize_author_name(author) for author in data.get("authors", []))
    best = None
    best_score = 0
    rows = filtered_work_match_rows(work_index if work_index is not None else build_work_match_index(), year, author_names)
    for row in rows:
        work = row["work"]
        title_score = similarity(title, row["main_title"])
        combined_score = similarity(combined_title, row["combined_title"])
        title_score = max(title_score, combined_score)
        year_match = bool(year and row["year"] == year)
        work_year_missing = bool(year and row["year_missing"])
        author_match = bool(author_names and row["author_names"] and author_names & row["author_names"])
        author_compatible = author_match or not author_names or not row["author_names"]
        score = title_score * 0.75 + (0.15 if year_match else 0) + (0.1 if author_match else 0)
        if title_score >= 0.9 and year_match and author_compatible:
            score = max(score, 0.95)
        if title_score >= 0.97 and work_year_missing and author_compatible:
            score = max(score, 0.93)
        if score > best_score:
            best = work
            best_score = score
    return best if best and best_score >= 0.9 else None


def filtered_work_match_rows(work_index, year, author_names):
    rows = work_index
    if year:
        year_rows = [row for row in rows if row["year"] == year or row["year_missing"]]
        if year_rows:
            rows = year_rows
    if author_names:
        author_rows = [row for row in rows if row["author_names"] and author_names & row["author_names"]]
        if author_rows:
            rows = author_rows
    return rows


def normalize_match_title(value):
    main, _remainder = split_main_title_and_remainder(value or "")
    return normalize_title(main)


def mark_item_existing_match(item, work, parsed):
    comparison = compare_work_to_parsed_data(work, parsed.data)
    has_updates = any(row["status"] == "new_in_source" for row in comparison["fields"])
    has_conflicts = any(row["status"] == "different" for row in comparison["fields"])
    has_missing_in_source = any(row["status"] == "missing_in_source" for row in comparison["fields"])
    if parsed.detected_type in {ImportItem.DetectedType.JOURNAL_ARTICLE, ImportItem.DetectedType.COLLECTION_ARTICLE}:
        status = ImportItem.Status.STRUCTURAL_CONFLICT
        comparison["summary"] = "Найдена похожая запись, но источник описывает её как часть родительского издания."
    elif has_updates or has_conflicts:
        status = ImportItem.Status.FOUND_EXISTING_WITH_DIFFERENCES
        if has_updates and not has_conflicts:
            comparison["summary"] = "Будут добавлены только безопасные пустые поля."
        else:
            comparison["summary"] = "Есть отличия или возможные дополнения."
    else:
        status = ImportItem.Status.FOUND_EXISTING_NO_CHANGES
        if has_missing_in_source:
            comparison["summary"] = "В источнике меньше сведений, изменений не требуется."
        else:
            comparison["summary"] = "Изменений не требуется."
    item.status = status
    item.matched_existing_type = "work"
    item.matched_existing_id = work.work_id
    item.comparison_json = comparison
    item.save(update_fields=["status", "matched_existing_type", "matched_existing_id", "comparison_json", "updated_at"])


def compare_work_to_parsed_data(work, data):
    existing_authors = "; ".join(author.display_name for author in work.authors.all())
    article = work_article(work)
    issue = article.journal_issue if article and article.journal_issue_id else None
    existing_parent = ""
    existing_issue_number = ""
    if article:
        if article.container_work_id:
            existing_parent = article.container_work.title
        elif article.journal_issue_id:
            existing_parent = article.journal_issue.journal.title
            existing_issue_number = article.journal_issue.issue_number or ""
    if not existing_parent:
        existing_parent = work.host_title or work.publication_details
    if not existing_issue_number and issue:
        existing_issue_number = issue.issue_number or ""
    parsed_title_combined = parsed_combined_title(data)
    source_title = parsed_title_combined if normalize_title(work.title) == normalize_title(parsed_title_combined) else data.get("title", "")
    rows = [
        author_comparison_row(existing_authors, "; ".join(data.get("authors", []))),
        comparison_row("Название", work.title, source_title),
        comparison_row("Уточнение названия", work.subtitle or work.title_remainder, data.get("title_remainder", ""), source_extra_is_nonblocking=True),
        comparison_row("Ответственность", work.responsibility_statement or work.responsibility_note, data.get("responsibility_statement", ""), source_extra_is_nonblocking=True),
        comparison_row("Сведения об издании", work.edition_statement, data.get("edition_statement", ""), source_extra_is_nonblocking=True),
        comparison_row("Место издания", work.publication_place, data.get("publication_place") or data.get("parent_place", "")),
        comparison_row("Издательство / типография", work.publisher, data.get("publisher", "")),
        comparison_row("Год", str(work.inferred_year or work.publication_date or ""), data.get("year", "")),
        comparison_row("Страницы", work.extent, data.get("extent", ""), normalizer=normalize_extent),
        comparison_row("Размер", work.dimensions, data.get("dimensions", ""), normalizer=normalize_dimensions, source_extra_is_nonblocking=True),
        comparison_row("Родительское издание", existing_parent, data.get("parent_title") or data.get("collection_title") or data.get("journal_title", "")),
        comparison_row("Номер выпуска", existing_issue_number, data.get("issue_number", "")),
        comparison_row("Страницы статьи", work.article_pages or (article.pages if article else ""), data.get("pages", "")),
        comparison_row("Примечания", work.notes or work.public_review, data.get("notes", "")),
    ]
    return {"existing_type": "work", "existing_id": work.work_id, "fields": rows}


def work_article(work):
    try:
        return work.article
    except Article.DoesNotExist:
        return None


def comparison_row(label, existing_value, source_value, normalizer=normalize_title, source_extra_is_nonblocking=False):
    existing_value = normalize_whitespace(str(existing_value or ""))
    source_value = normalize_whitespace(str(source_value or ""))
    if normalizer(existing_value) == normalizer(source_value):
        status = "same"
    elif source_value and not existing_value:
        status = "source_extra" if source_extra_is_nonblocking else "new_in_source"
    elif existing_value and not source_value:
        status = "missing_in_source"
    else:
        status = "different"
    return {
        "label": label,
        "existing": existing_value,
        "source": source_value,
        "status": status,
    }


def author_comparison_row(existing_value, source_value):
    row = comparison_row("Автор", existing_value, source_value)
    if row["status"] == "different" and author_initials_are_incomplete(row["existing"], row["source"]):
        row["status"] = "author_incomplete_initials"
    return row


def author_initials_are_incomplete(existing_value, source_value):
    existing = [author_signature(part) for part in str(existing_value or "").split(";") if part.strip()]
    source = [author_signature(part) for part in str(source_value or "").split(";") if part.strip()]
    if not existing or len(existing) != len(source):
        return False
    for existing_author, source_author in zip(existing, source):
        if existing_author["surname"] != source_author["surname"]:
            return False
        if not source_author["initials"]:
            return False
        if not "".join(existing_author["initials"]).startswith("".join(source_author["initials"])):
            return False
        if len(source_author["initials"]) > len(existing_author["initials"]):
            return False
    return any(len(source_author["initials"]) < len(existing_author["initials"]) for existing_author, source_author in zip(existing, source))


def author_signature(value):
    value = normalize_whitespace(str(value or "")).replace(".", " ")
    parts = [part for part in re.split(r"\s+", value) if part]
    surname = normalize_author_name(parts[0]) if parts else ""
    initials = [part.casefold()[0] for part in parts[1:] if part]
    return {"surname": surname, "initials": initials}


def entity_key(entity_type, data):
    if entity_type == ImportEntity.EntityType.AUTHOR:
        return normalize_author_name(data["name"])
    if entity_type == ImportEntity.EntityType.JOURNAL:
        return normalize_journal_title(data["title"])
    if entity_type == ImportEntity.EntityType.JOURNAL_ISSUE:
        return "|".join([normalize_journal_title(data["journal_title"]), normalize_year(data.get("year")), normalize_issue_number(data.get("issue_number"))])
    if entity_type == ImportEntity.EntityType.COLLECTION:
        return "|".join([normalize_title(data["title"]), normalize_year(data.get("year"))])
    if entity_type == ImportEntity.EntityType.ARTICLE:
        return "|".join([normalize_title(data["title"]), ",".join(sorted(normalize_author_name(a) for a in data.get("authors", []))), data.get("parent_key", ""), normalize_pages(data.get("pages"))])
    if entity_type == ImportEntity.EntityType.BOOK:
        return "|".join([normalize_title(data["title"]), normalize_year(data.get("year")), ",".join(sorted(normalize_author_name(a) for a in data.get("authors", [])))])
    return hashlib.sha1(repr(sorted(data.items())).encode("utf-8")).hexdigest()


def get_or_create_entity(import_batch, entity_type, label, data, confidence=0.0):
    key = entity_key(entity_type, data)
    entity, created = ImportEntity.objects.get_or_create(
        import_batch=import_batch,
        entity_type=entity_type,
        normalized_key=key,
        defaults={
            "label": label[:1024] or key,
            "data_json": data,
            "status": ImportEntity.Status.UNRESOLVED,
            "confidence": confidence,
        },
    )
    if not created:
        merged = {**entity.data_json, **{k: v for k, v in data.items() if v not in ("", None, [])}}
        entity.data_json = merged
        entity.confidence = max(entity.confidence, confidence)
        entity.save(update_fields=["data_json", "confidence", "updated_at"])
    return entity


def create_entities_for_item(item):
    data = item.parsed_data_json
    batch = item.import_batch
    authors = [
        get_or_create_entity(batch, ImportEntity.EntityType.AUTHOR, author, {"name": author}, item.confidence)
        for author in data.get("authors", [])
    ]
    if item.detected_type == ImportItem.DetectedType.JOURNAL_ARTICLE:
        journal = get_or_create_entity(batch, ImportEntity.EntityType.JOURNAL, data.get("journal_title") or "Журнал без названия", {"title": data.get("journal_title", "")}, item.confidence)
        issue = get_or_create_entity(
            batch,
            ImportEntity.EntityType.JOURNAL_ISSUE,
            journal_issue_label(data),
            {"journal_title": data.get("journal_title", ""), "year": data.get("year", ""), "issue_number": data.get("issue_number", "")},
            item.confidence,
        )
        article = get_or_create_entity(
            batch,
            ImportEntity.EntityType.ARTICLE,
            data.get("title", ""),
            {**data, "parent_key": issue.normalized_key, "item_id": item.id, "raw_text": item.raw_text},
            item.confidence,
        )
        create_relation(batch, journal, issue, "journal_has_issue")
        create_relation(batch, issue, article, "issue_has_article")
        for author in authors:
            create_relation(batch, author, article, "author_of")
    elif item.detected_type == ImportItem.DetectedType.COLLECTION_ARTICLE:
        collection = get_or_create_entity(batch, ImportEntity.EntityType.COLLECTION, data.get("collection_title") or "Сборник без названия", {"title": data.get("collection_title", ""), "year": data.get("year", "")}, item.confidence)
        article = get_or_create_entity(batch, ImportEntity.EntityType.ARTICLE, data.get("title", ""), {**data, "parent_key": collection.normalized_key, "item_id": item.id, "raw_text": item.raw_text}, item.confidence)
        create_relation(batch, collection, article, "article_in_collection")
        for author in authors:
            create_relation(batch, author, article, "author_of")
    elif item.detected_type == ImportItem.DetectedType.BOOK:
        book = get_or_create_entity(batch, ImportEntity.EntityType.BOOK, data.get("title", ""), {**data, "item_id": item.id, "raw_text": item.raw_text}, item.confidence)
        for author in authors:
            create_relation(batch, author, book, "author_of")


def journal_issue_label(data):
    bits = [data.get("journal_title") or "Журнал без названия"]
    if data.get("year"):
        bits.append(str(data["year"]))
    if data.get("issue_number"):
        bits.append(f"№ {data['issue_number']}")
    return " — ".join(bits)


def create_relation(batch, parent, child, relation_type):
    ImportEntityRelation.objects.get_or_create(import_batch=batch, parent_entity=parent, child_entity=child, relation_type=relation_type)


def create_import_matches(import_batch, work_index=None, author_index=None):
    for entity in ImportEntity.objects.filter(import_batch=import_batch):
        create_matches_for_entity(entity, work_index=work_index, author_index=author_index)
    auto_link_exact_issue_candidates(import_batch)
    auto_resolve_same_issue_articles(import_batch)


def create_matches_for_entity(entity, work_index=None, author_index=None):
    entity.matches.all().delete()
    if entity.entity_type == ImportEntity.EntityType.AUTHOR:
        create_author_matches(entity, author_index=author_index)
    elif entity.entity_type == ImportEntity.EntityType.JOURNAL:
        create_journal_matches(entity)
    elif entity.entity_type in {ImportEntity.EntityType.BOOK, ImportEntity.EntityType.ARTICLE}:
        create_work_matches(entity, work_index=work_index)
    elif entity.entity_type == ImportEntity.EntityType.JOURNAL_ISSUE:
        create_issue_matches(entity)
    elif entity.entity_type == ImportEntity.EntityType.COLLECTION:
        create_collection_matches(entity)
    set_entity_default_status(entity)


def refresh_matches_for_entities(entities, work_index=None, author_index=None):
    for entity in entities:
        create_matches_for_entity(entity, work_index=work_index, author_index=author_index)
    if entities:
        auto_link_exact_issue_candidates(entities[0].import_batch)
        auto_resolve_same_issue_articles(entities[0].import_batch)


def reconcile_import_auto_links(import_batch):
    linked = auto_link_exact_issue_candidates(import_batch)
    if linked:
        auto_resolve_same_issue_articles(import_batch)
        recalculate_import_status(import_batch)
    return linked


def refresh_item_from_manual_parse(item, parsed_data, user=None):
    parsed_data = normalize_manual_parsed_data(item.detected_type, parsed_data)
    parsed = ParsedRecord(item.detected_type, parsed_data, 0.9, [])
    with transaction.atomic():
        clear_import_item_entities(item)
        ImportDecision.objects.filter(import_batch=item.import_batch, item=item).delete()
        item.parsed_data_json = parsed_data
        item.normalized_data_json = normalize_parsed_data(parsed)
        item.confidence = max(item.confidence or 0, 0.9)
        item.errors_json = []
        item.status = ImportItem.Status.PARSED
        item.matched_existing_type = ""
        item.matched_existing_id = ""
        item.comparison_json = {}
        item.save(
            update_fields=[
                "parsed_data_json",
                "normalized_data_json",
                "confidence",
                "errors_json",
                "status",
                "matched_existing_type",
                "matched_existing_id",
                "comparison_json",
                "updated_at",
            ]
        )
        work_index = build_work_match_index()
        author_index = build_author_match_index()
        existing_work = find_core_work_match(parsed, work_index=work_index)
        if existing_work and item.detected_type != ImportItem.DetectedType.JOURNAL_ARTICLE:
            mark_item_existing_match(item, existing_work, parsed)
            affected_entities = []
        else:
            create_entities_for_item(item)
            affected_entities = list(item_related_entities(item))
            refresh_matches_for_entities(affected_entities, work_index=work_index, author_index=author_index)
        ImportGroup.objects.filter(import_batch=item.import_batch).delete()
        create_import_groups(item.import_batch)
        recalculate_import_status(item.import_batch)
    return item


def normalize_manual_parsed_data(detected_type, data):
    cleaned = {}
    for key, value in (data or {}).items():
        if key == "authors":
            if isinstance(value, str):
                cleaned[key] = [normalize_whitespace(part).strip(" ;,") for part in re.split(r"\s*;\s*", value) if part.strip(" ;,")]
            else:
                cleaned[key] = [normalize_whitespace(part) for part in value if normalize_whitespace(part)]
        elif key == "responsibility_contributors":
            cleaned[key] = value if isinstance(value, list) else []
        elif key == "edition_statement":
            cleaned[key] = normalize_edition_statement_text(value) if value is not None else ""
        else:
            cleaned[key] = normalize_whitespace(str(value or "")).strip(" .") if value is not None else ""
    defaults = {
        ImportItem.DetectedType.BOOK: [
            "authors",
            "title",
            "title_remainder",
            "responsibility_statement",
            "responsibility_contributors",
            "edition_statement",
            "publication_place",
            "publisher",
            "year",
            "extent",
            "dimensions",
            "notes",
        ],
        ImportItem.DetectedType.JOURNAL_ARTICLE: [
            "authors",
            "title",
            "journal_title",
            "year",
            "issue_number",
            "pages",
            "raw_host",
            "raw_parent_description",
        ],
        ImportItem.DetectedType.COLLECTION_ARTICLE: [
            "authors",
            "title",
            "collection_title",
            "year",
            "pages",
            "raw_host",
            "raw_parent_description",
        ],
    }
    for key in defaults.get(detected_type, defaults[ImportItem.DetectedType.BOOK]):
        cleaned.setdefault(key, [] if key == "authors" else "")
    return cleaned


def clear_import_item_entities(item):
    owned_ids = set(import_item_entities(item).values_list("id", flat=True))
    if not owned_ids:
        return
    relation_pairs = list(
        ImportEntityRelation.objects.filter(import_batch=item.import_batch)
        .filter(Q_for_relation_entities(owned_ids))
        .values_list("parent_entity_id", "child_entity_id")
    )
    related_ids = {entity_id for pair in relation_pairs for entity_id in pair}
    ImportEntityRelation.objects.filter(import_batch=item.import_batch).filter(
        Q_for_relation_entities(owned_ids)
    ).delete()
    orphan_candidates = set(owned_ids) | related_ids
    for entity_id in list(orphan_candidates):
        entity = ImportEntity.objects.filter(pk=entity_id).first()
        if not entity:
            continue
        if ImportGroup.objects.filter(import_batch=item.import_batch, root_entity=entity).exists():
            continue
        if ImportEntityRelation.objects.filter(import_batch=item.import_batch).filter(Q_for_relation_entities({entity_id})).exists():
            continue
        entity.delete()


def Q_for_relation_entities(entity_ids):
    from django.db.models import Q

    return Q(parent_entity_id__in=entity_ids) | Q(child_entity_id__in=entity_ids)


def import_item_entities(item):
    return ImportEntity.objects.filter(import_batch=item.import_batch, data_json__item_id=item.id)


def item_related_entities(item):
    work_entities = list(
        ImportEntity.objects.filter(
            import_batch=item.import_batch,
            data_json__item_id=item.id,
        )
    )
    ids = {entity.id for entity in work_entities}
    frontier = set(ids)
    while frontier:
        related_ids = set(
            ImportEntityRelation.objects.filter(import_batch=item.import_batch)
            .filter(Q_for_relation_entities(frontier))
            .values_list("parent_entity_id", "child_entity_id")
        )
        flattened = {entity_id for pair in related_ids for entity_id in pair}
        new_ids = flattened - ids
        ids.update(new_ids)
        frontier = new_ids
    return ImportEntity.objects.filter(id__in=ids)


def import_item_ids_for_entity(entity):
    ids = set()
    if entity.data_json.get("item_id"):
        ids.add(entity.data_json["item_id"])
    relation_pairs = ImportEntityRelation.objects.filter(import_batch=entity.import_batch).filter(
        Q_for_relation_entities({entity.id})
    ).values_list("parent_entity__data_json", "child_entity__data_json")
    for parent_data, child_data in relation_pairs:
        for data in (parent_data or {}, child_data or {}):
            if data.get("item_id"):
                ids.add(data["item_id"])
    if entity.entity_type == ImportEntity.EntityType.JOURNAL:
        issue_ids = ImportEntityRelation.objects.filter(
            import_batch=entity.import_batch,
            parent_entity=entity,
            relation_type="journal_has_issue",
        ).values_list("child_entity_id", flat=True)
        article_data = ImportEntityRelation.objects.filter(
            import_batch=entity.import_batch,
            parent_entity_id__in=issue_ids,
            relation_type="issue_has_article",
        ).values_list("child_entity__data_json", flat=True)
        ids.update(data.get("item_id") for data in article_data if data.get("item_id"))
    return {item_id for item_id in ids if item_id}


def entity_only_postponed_items(entity):
    item_ids = import_item_ids_for_entity(entity)
    if not item_ids:
        return False
    return not ImportItem.objects.filter(import_batch=entity.import_batch, id__in=item_ids).exclude(status=ImportItem.Status.POSTPONED).exists()


def entity_excluded_from_current_apply(entity):
    return entity_only_postponed_items(entity)


def entities_for_current_apply(import_batch, **filters):
    return [
        entity
        for entity in ImportEntity.objects.filter(import_batch=import_batch, **filters)
        if not entity_excluded_from_current_apply(entity)
    ]


def create_author_matches(entity, author_index=None):
    name = entity.data_json.get("name", "")
    normalized_name = normalize_author_name(name)
    for row in author_index if author_index is not None else build_author_match_index():
        author = row["author"]
        score = similarity(normalized_name, row["normalized_name"])
        if score >= 0.7:
            ImportMatch.objects.create(import_batch=entity.import_batch, entity=entity, existing_type="author", existing_id=author.author_id, score=score, match_reason_json={"name_similarity": score, "work_count": row["work_count"]})


def create_journal_matches(entity):
    title = entity.data_json.get("title", "")
    for journal in Journal.objects.annotate(issue_count=Count("issues")).iterator():
        score = similarity(normalize_journal_title(title), normalize_journal_title(journal.title))
        if score >= 0.7:
            ImportMatch.objects.create(import_batch=entity.import_batch, entity=entity, existing_type="journal", existing_id=journal.journal_id, score=score, match_reason_json={"title_similarity": score, "issue_count": journal.issue_count})


def create_issue_matches(entity):
    data = entity.data_json
    journal_title = normalize_journal_title(data.get("journal_title"))
    year = normalize_year(data.get("year"))
    number = normalize_issue_number(data.get("issue_number"))
    for issue in JournalIssue.objects.select_related("journal").iterator():
        title_score = similarity(journal_title, normalize_journal_title(issue.journal.title))
        year_match = year and str(issue.year or "") == year
        number_match = number and normalize_issue_number(issue.issue_number) == number
        score = title_score * 0.5 + (0.3 if year_match else 0) + (0.2 if number_match else 0)
        if score >= 0.7:
            ImportMatch.objects.create(import_batch=entity.import_batch, entity=entity, existing_type="journal_issue", existing_id=issue.journal_issue_id, score=round(score, 3), match_reason_json={"journal_title_similarity": title_score, "year_match": bool(year_match), "issue_number_match": bool(number_match)})


def auto_link_exact_issue_candidates(import_batch, journal_entity=None):
    queryset = ImportEntity.objects.filter(
        import_batch=import_batch,
        entity_type=ImportEntity.EntityType.JOURNAL_ISSUE,
        status=ImportEntity.Status.UNRESOLVED,
    )
    if journal_entity is not None:
        issue_ids = ImportEntityRelation.objects.filter(
            import_batch=import_batch,
            parent_entity=journal_entity,
            relation_type="journal_has_issue",
            child_entity__entity_type=ImportEntity.EntityType.JOURNAL_ISSUE,
        ).values_list("child_entity_id", flat=True)
        queryset = queryset.filter(id__in=issue_ids)
    linked = 0
    for issue_entity in queryset:
        if auto_link_exact_issue_candidate(issue_entity):
            linked += 1
    return linked


def auto_link_exact_issue_candidate(issue_entity):
    candidate = exact_issue_candidate_for_linked_journal(issue_entity)
    if not candidate:
        return False
    issue_entity.status = ImportEntity.Status.LINKED_EXISTING
    issue_entity.matched_existing_type = "journal_issue"
    issue_entity.matched_existing_id = candidate.journal_issue_id
    issue_entity.save(update_fields=["status", "matched_existing_type", "matched_existing_id", "updated_at"])
    refresh_article_matches_for_container(issue_entity)
    refresh_groups_for_entity(issue_entity)
    return True


def exact_issue_candidate_for_linked_journal(issue_entity):
    if issue_entity.entity_type != ImportEntity.EntityType.JOURNAL_ISSUE or issue_entity.status != ImportEntity.Status.UNRESOLVED:
        return None
    data = issue_entity.data_json or {}
    year = normalize_year(data.get("year"))
    issue_number = normalize_issue_number(data.get("issue_number"))
    if not year or not issue_number:
        return None
    journal_entity = linked_journal_entity_for_issue(issue_entity)
    if not journal_entity:
        return None
    candidates = []
    for match in issue_entity.matches.filter(existing_type="journal_issue").order_by("-score", "existing_id"):
        try:
            issue = JournalIssue.objects.select_related("journal").get(journal_issue_id=match.existing_id)
        except JournalIssue.DoesNotExist:
            continue
        if issue.journal_id != journal_entity.matched_existing_id:
            continue
        if str(issue.year or "") != year:
            continue
        if normalize_issue_number(issue.issue_number) != issue_number:
            continue
        candidates.append(issue)
    if len(candidates) != 1:
        return None
    return candidates[0]


def linked_journal_entity_for_issue(issue_entity):
    relation = (
        ImportEntityRelation.objects.filter(
            import_batch=issue_entity.import_batch,
            child_entity=issue_entity,
            relation_type="journal_has_issue",
            parent_entity__entity_type=ImportEntity.EntityType.JOURNAL,
            parent_entity__status=ImportEntity.Status.LINKED_EXISTING,
            parent_entity__matched_existing_type="journal",
        )
        .select_related("parent_entity")
        .first()
    )
    if not relation or not relation.parent_entity.matched_existing_id:
        return None
    return relation.parent_entity


def create_collection_matches(entity):
    title = normalize_title(entity.data_json.get("title"))
    for collection in Collection.objects.iterator():
        score = similarity(title, normalize_title(collection.title))
        if score >= 0.72:
            ImportMatch.objects.create(import_batch=entity.import_batch, entity=entity, existing_type="collection", existing_id=collection.collection_id, score=score, match_reason_json={"title_similarity": score})


def create_work_matches(entity, work_index=None):
    data = entity.data_json
    title = normalize_match_title(data.get("title"))
    combined_title = normalize_title(parsed_combined_title(data))
    year = normalize_year(data.get("year"))
    author_names = set(normalize_author_name(a) for a in data.get("authors", []))
    rows = filtered_work_match_rows(work_index if work_index is not None else build_work_match_index(), year, author_names)
    for row in rows:
        work = row["work"]
        title_score = similarity(title, row["main_title"])
        combined_score = similarity(combined_title, row["combined_title"])
        title_score = max(title_score, combined_score)
        year_match = bool(year and row["year"] == year)
        author_match = bool(author_names and row["author_names"] and author_names & row["author_names"])
        author_compatible = author_match or not author_names or not row["author_names"]
        score = title_score * 0.75 + (0.15 if year_match else 0) + (0.1 if author_match else 0)
        if title_score >= 0.98 and year_match and author_compatible:
            score = max(score, 0.95)
        if score >= 0.7:
            reason = {"title_similarity": title_score, "combined_title_similarity": combined_score, "year_match": year_match, "author_match": author_match}
            if entity.entity_type == ImportEntity.EntityType.ARTICLE:
                reason.update(article_issue_match_context(entity, work))
            ImportMatch.objects.create(import_batch=entity.import_batch, entity=entity, existing_type="work", existing_id=work.work_id, score=round(score, 3), match_reason_json=reason)


def article_issue_match_context(entity, work):
    import_issue = article_import_issue_entity(entity)
    selected_import_issue = selected_existing_issue_for_import_issue(import_issue)
    if selected_import_issue:
        import_label = issue_label_from_existing_issue(selected_import_issue)
        import_journal = normalize_journal_title(selected_import_issue.journal.title)
        import_year = str(selected_import_issue.year or "")
        import_number = normalize_issue_number(selected_import_issue.issue_number)
    else:
        import_label = issue_label_from_import_data(import_issue.data_json) if import_issue else ""
        import_journal = normalize_journal_title(import_issue.data_json.get("journal_title")) if import_issue else ""
        import_year = normalize_year(import_issue.data_json.get("year")) if import_issue else ""
        import_number = normalize_issue_number(import_issue.data_json.get("issue_number")) if import_issue else ""

    article = work_article(work)
    existing_issue = article.journal_issue if article and article.journal_issue_id else None
    existing_label = issue_label_from_existing_issue(existing_issue) if existing_issue else ""
    existing_journal = normalize_journal_title(existing_issue.journal.title) if existing_issue else ""
    existing_year = str(existing_issue.year or "") if existing_issue else ""
    existing_number = normalize_issue_number(existing_issue.issue_number) if existing_issue else ""

    same_journal = bool(import_journal and existing_journal and import_journal == existing_journal)
    same_year = bool(import_year and existing_year and import_year == existing_year)
    same_issue_number = bool(import_number and existing_number and import_number == existing_number)
    same_issue = None
    if import_label and existing_label:
        same_issue = bool(same_journal and same_year and same_issue_number)
    return {
        "import_issue_label": import_label,
        "existing_issue_label": existing_label,
        "same_journal": same_journal,
        "same_year": same_year,
        "same_issue_number": same_issue_number,
        "same_issue": same_issue,
    }


def selected_existing_issue_for_import_issue(import_issue):
    if (
        import_issue
        and import_issue.entity_type == ImportEntity.EntityType.JOURNAL_ISSUE
        and import_issue.status == ImportEntity.Status.LINKED_EXISTING
        and import_issue.matched_existing_type == "journal_issue"
        and import_issue.matched_existing_id
    ):
        try:
            return JournalIssue.objects.select_related("journal").get(journal_issue_id=import_issue.matched_existing_id)
        except JournalIssue.DoesNotExist:
            return None
    return None


def article_import_issue_entity(article_entity):
    relation = (
        ImportEntityRelation.objects.filter(
            import_batch=article_entity.import_batch,
            child_entity=article_entity,
            relation_type="issue_has_article",
            parent_entity__entity_type=ImportEntity.EntityType.JOURNAL_ISSUE,
        )
        .select_related("parent_entity")
        .first()
    )
    return relation.parent_entity if relation else None


def issue_label_from_import_data(data):
    bits = [data.get("journal_title") or "Журнал без названия"]
    if data.get("year"):
        bits.append(str(data["year"]))
    if data.get("issue_number"):
        bits.append(f"№ {data['issue_number']}")
    return ", ".join(bits)


def issue_label_from_existing_issue(issue):
    if not issue:
        return ""
    bits = [issue.journal.title]
    if issue.year:
        bits.append(str(issue.year))
    if issue.issue_number:
        bits.append(f"№ {issue.issue_number}")
    return ", ".join(bits)


def similarity(left, right):
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return round(SequenceMatcher(None, left, right).ratio(), 3)


def set_entity_default_status(entity):
    best = entity.matches.order_by("-score").first()
    if best and best.score >= 0.95:
        if entity.entity_type == ImportEntity.EntityType.JOURNAL_ISSUE and journal_issue_best_match_is_ambiguous(entity, best):
            entity.status = ImportEntity.Status.UNRESOLVED
            entity.matched_existing_type = ""
            entity.matched_existing_id = ""
            entity.save(update_fields=["status", "matched_existing_type", "matched_existing_id", "updated_at"])
            return
        if entity.entity_type == ImportEntity.EntityType.ARTICLE and best.match_reason_json.get("same_issue") is False:
            entity.status = ImportEntity.Status.UNRESOLVED
            entity.matched_existing_type = ""
            entity.matched_existing_id = ""
            entity.save(update_fields=["status", "matched_existing_type", "matched_existing_id", "updated_at"])
            return
        entity.status = ImportEntity.Status.LINKED_EXISTING
        entity.matched_existing_type = best.existing_type
        entity.matched_existing_id = best.existing_id
    elif best:
        entity.status = ImportEntity.Status.UNRESOLVED
    else:
        entity.status = ImportEntity.Status.WILL_CREATE
    entity.save(update_fields=["status", "matched_existing_type", "matched_existing_id", "updated_at"])


def journal_issue_best_match_is_ambiguous(entity, best):
    data = entity.data_json or {}
    year = normalize_year(data.get("year"))
    issue_number = normalize_issue_number(data.get("issue_number"))
    journal_title = normalize_journal_title(data.get("journal_title"))
    if not year or not issue_number or not journal_title:
        return False
    exact_candidates = 0
    for match in entity.matches.filter(existing_type="journal_issue", score=best.score):
        try:
            issue = JournalIssue.objects.select_related("journal").get(journal_issue_id=match.existing_id)
        except JournalIssue.DoesNotExist:
            continue
        if normalize_journal_title(issue.journal.title) != journal_title:
            continue
        if str(issue.year or "") != year:
            continue
        if normalize_issue_number(issue.issue_number) != issue_number:
            continue
        exact_candidates += 1
        if exact_candidates > 1:
            return True
    return False


def auto_resolve_same_issue_articles(import_batch):
    for entity in ImportEntity.objects.filter(import_batch=import_batch, entity_type=ImportEntity.EntityType.ARTICLE).prefetch_related("matches"):
        auto_resolve_same_issue_article(entity)


def auto_resolve_same_issue_article(entity):
    if entity.entity_type != ImportEntity.EntityType.ARTICLE:
        return False
    best = entity.matches.filter(existing_type="work").order_by("-score", "existing_id").first()
    if not best or not same_issue_article_match_is_safe(entity, best):
        return False
    entity.status = ImportEntity.Status.LINKED_EXISTING
    entity.matched_existing_type = best.existing_type
    entity.matched_existing_id = best.existing_id
    entity.save(update_fields=["status", "matched_existing_type", "matched_existing_id", "updated_at"])
    item = import_item_for_entity(entity)
    if item:
        try:
            work = Work.objects.get(work_id=best.existing_id)
        except Work.DoesNotExist:
            work = None
        comparison = compare_work_to_parsed_data(work, item.parsed_data_json) if work else {"fields": []}
        if safe_linked_existing_supplement_labels(entity):
            comparison["summary"] = "Все поля совпадают, кроме технических. Технические поля будут добавлены в пустые места."
        else:
            comparison["summary"] = "Все данные уже есть в базе. При применении строка будет пропущена без изменений."
        item.status = ImportItem.Status.FOUND_EXISTING_NO_CHANGES
        item.matched_existing_type = best.existing_type
        item.matched_existing_id = best.existing_id
        item.comparison_json = comparison
        item.save(update_fields=["status", "matched_existing_type", "matched_existing_id", "comparison_json", "updated_at"])
    return True


def same_issue_article_match_is_safe(entity, match):
    reason = match.match_reason_json or {}
    return bool(
        reason.get("same_issue") is True
        and reason.get("same_journal") is True
        and reason.get("same_year") is True
        and reason.get("same_issue_number") is True
        and reason.get("author_match") is True
        and float(reason.get("title_similarity") or 0) >= 0.9
        and not same_issue_match_has_blocking_conflict(entity, match.existing_id)
    )


def same_issue_match_has_blocking_conflict(entity, work_id):
    item = import_item_for_entity(entity)
    if not item:
        return True
    try:
        work = Work.objects.get(work_id=work_id)
    except Work.DoesNotExist:
        return True
    comparison = compare_work_to_parsed_data(work, item.parsed_data_json)
    blocking_labels = {"Название", "Год", "Номер выпуска", "Страницы статьи"}
    return any(row.get("label") in blocking_labels and row.get("status") == "different" for row in comparison.get("fields", []))


def is_auto_resolved_existing_article(entity):
    if (
        not entity
        or entity.entity_type != ImportEntity.EntityType.ARTICLE
        or entity.status != ImportEntity.Status.LINKED_EXISTING
        or entity.matched_existing_type != "work"
        or not entity.matched_existing_id
    ):
        return False
    match = entity.matches.filter(existing_type="work", existing_id=entity.matched_existing_id).order_by("-score").first()
    return bool(match and same_issue_article_match_is_safe(entity, match))


def author_entity_only_used_by_auto_resolved_existing_articles(entity):
    if entity.entity_type != ImportEntity.EntityType.AUTHOR:
        return False
    relations = list(
        ImportEntityRelation.objects.filter(
            import_batch=entity.import_batch,
            parent_entity=entity,
            relation_type="author_of",
        ).select_related("child_entity")
    )
    return bool(relations) and all(is_auto_resolved_existing_article(relation.child_entity) for relation in relations)


def safe_linked_existing_supplement_labels(entity):
    if not is_auto_resolved_existing_article(entity):
        return []
    try:
        work = Work.objects.select_related("article").get(work_id=entity.matched_existing_id)
    except Work.DoesNotExist:
        return []
    data = entity.data_json or {}
    source = getattr(work, "target_source", None)
    labels = []
    article = work_article(work)
    if data.get("pages") and not normalize_whitespace(work.article_pages):
        labels.append("Страницы статьи")
    if article and data.get("pages") and (not normalize_whitespace(article.pages) or not normalize_whitespace(article.pages_raw)):
        labels.append("Страницы статьи")
    if data.get("year") and not normalize_whitespace(work.publication_date) and not work.inferred_year:
        labels.append("Год")
    raw_host = data.get("raw_parent_description") or data.get("raw_host") or data.get("raw_text") or ""
    if raw_host and not normalize_whitespace(work.publication_details):
        labels.append("raw_parent_description")
    if source:
        if raw_host and not normalize_whitespace(source.raw_publication_details):
            labels.append("raw_publication_details")
        if data.get("parent_title") and not normalize_whitespace(source.raw_host_title):
            labels.append("raw_host")
        provenance = import_source_provenance(entity.import_batch)
        if provenance and (not normalize_whitespace(source.data_source) or normalize_whitespace(source.data_source) == "editor"):
            labels.append("data_source")
    return sorted(set(labels))


def import_item_for_entity(entity):
    item_id = entity.data_json.get("item_id")
    if not item_id:
        return None
    try:
        return ImportItem.objects.get(id=item_id, import_batch=entity.import_batch)
    except ImportItem.DoesNotExist:
        return None


def create_import_groups(import_batch):
    for issue in ImportEntity.objects.filter(import_batch=import_batch, entity_type=ImportEntity.EntityType.JOURNAL_ISSUE):
        article_count = ImportEntityRelation.objects.filter(import_batch=import_batch, parent_entity=issue, relation_type="issue_has_article").count()
        group = ImportGroup.objects.create(import_batch=import_batch, group_type=ImportGroup.GroupType.JOURNAL_ISSUE_GROUP, label=issue.label, root_entity=issue)
        group.status = group_status_for_root(issue)
        group.save(update_fields=["status", "updated_at"])
    for collection in ImportEntity.objects.filter(import_batch=import_batch, entity_type=ImportEntity.EntityType.COLLECTION):
        group = ImportGroup.objects.create(import_batch=import_batch, group_type=ImportGroup.GroupType.COLLECTION_VOLUME_GROUP, label=collection.label, root_entity=collection)
        group.status = group_status_for_root(collection)
        group.save(update_fields=["status", "updated_at"])
    books = ImportEntity.objects.filter(import_batch=import_batch, entity_type=ImportEntity.EntityType.BOOK)
    if books.exists():
        status = ImportGroup.Status.READY if all(entity.status != ImportEntity.Status.UNRESOLVED for entity in books) else ImportGroup.Status.NEEDS_REVIEW
        ImportGroup.objects.create(import_batch=import_batch, group_type=ImportGroup.GroupType.STANDALONE_BOOKS, label="Отдельные книги", status=status)


def group_status_for_root(root):
    related = related_entities_for_root(root)
    if any(entity.status == ImportEntity.Status.ERROR for entity in related):
        return ImportGroup.Status.ERROR
    if any(entity.status == ImportEntity.Status.UNRESOLVED for entity in related):
        return ImportGroup.Status.NEEDS_REVIEW
    return ImportGroup.Status.READY


def related_entities_for_root(root):
    entities = [root]
    child_ids = list(ImportEntityRelation.objects.filter(parent_entity=root).values_list("child_entity_id", flat=True))
    entities.extend(ImportEntity.objects.filter(id__in=child_ids))
    parent_ids = list(ImportEntityRelation.objects.filter(child_entity=root).values_list("parent_entity_id", flat=True))
    entities.extend(ImportEntity.objects.filter(id__in=parent_ids))
    return entities


def recalculate_import_status(import_batch):
    unresolved = any(
        not author_entity_only_used_by_auto_resolved_existing_articles(entity)
        and not entity_excluded_from_current_apply(entity)
        for entity in ImportEntity.objects.filter(import_batch=import_batch, status=ImportEntity.Status.UNRESOLVED)
    )
    errors = ImportItem.objects.filter(import_batch=import_batch, status=ImportItem.Status.ERROR).exists()
    item_review = ImportItem.objects.filter(
        import_batch=import_batch,
        status__in=[ImportItem.Status.FOUND_EXISTING_WITH_DIFFERENCES, ImportItem.Status.STRUCTURAL_CONFLICT],
    ).exists()
    if errors:
        import_batch.status = ImportBatch.Status.REVIEW_REQUIRED
    elif unresolved or item_review:
        import_batch.status = ImportBatch.Status.REVIEW_REQUIRED
    else:
        import_batch.status = ImportBatch.Status.READY_TO_APPLY
    ImportGroup.objects.filter(import_batch=import_batch).update(updated_at=timezone.now())


def apply_entity_decision(entity, decision_type, target_type="", target_id="", user=None, payload=None):
    payload = payload or {}
    with transaction.atomic():
        ImportDecision.objects.create(import_batch=entity.import_batch, entity=entity, decision_type=decision_type, target_type=target_type, target_id=target_id, payload_json=payload, created_by=user)
        if decision_type == ImportDecision.DecisionType.CREATE:
            entity.status = ImportEntity.Status.WILL_CREATE
            entity.matched_existing_type = ""
            entity.matched_existing_id = ""
        elif decision_type == ImportDecision.DecisionType.LINK_EXISTING:
            entity.status = ImportEntity.Status.LINKED_EXISTING
            entity.matched_existing_type = target_type
            entity.matched_existing_id = target_id
        elif decision_type == ImportDecision.DecisionType.UPDATE_EXISTING:
            entity.status = ImportEntity.Status.WILL_UPDATE_EXISTING
            entity.matched_existing_type = target_type
            entity.matched_existing_id = target_id
        elif decision_type == ImportDecision.DecisionType.REJECT:
            entity.status = ImportEntity.Status.IGNORED
        elif decision_type == ImportDecision.DecisionType.POSTPONE:
            entity.status = ImportEntity.Status.UNRESOLVED
        entity.save()
        if entity.entity_type == ImportEntity.EntityType.JOURNAL and entity.status == ImportEntity.Status.LINKED_EXISTING:
            auto_link_exact_issue_candidates(entity.import_batch, journal_entity=entity)
        refresh_dependent_article_matches(entity)
        refresh_groups_for_entity(entity)
        recalculate_import_status(entity.import_batch)


def refresh_dependent_article_matches(entity):
    if entity.entity_type == ImportEntity.EntityType.JOURNAL_ISSUE:
        refresh_article_matches_for_container(entity)
    elif entity.entity_type == ImportEntity.EntityType.JOURNAL:
        issue_ids = ImportEntityRelation.objects.filter(
            import_batch=entity.import_batch,
            parent_entity=entity,
            relation_type="journal_has_issue",
            child_entity__entity_type=ImportEntity.EntityType.JOURNAL_ISSUE,
        ).values_list("child_entity_id", flat=True)
        for issue in ImportEntity.objects.filter(id__in=issue_ids):
            refresh_article_matches_for_container(issue)


def refresh_article_matches_for_container(issue_entity):
    if issue_entity.entity_type != ImportEntity.EntityType.JOURNAL_ISSUE:
        return
    article_ids = ImportEntityRelation.objects.filter(
        import_batch=issue_entity.import_batch,
        parent_entity=issue_entity,
        relation_type="issue_has_article",
        child_entity__entity_type=ImportEntity.EntityType.ARTICLE,
    ).values_list("child_entity_id", flat=True)
    for article in ImportEntity.objects.filter(id__in=article_ids).prefetch_related("matches"):
        for match in article.matches.filter(existing_type="work"):
            try:
                work = Work.objects.get(work_id=match.existing_id)
            except Work.DoesNotExist:
                continue
            reason = dict(match.match_reason_json or {})
            reason.update(article_issue_match_context(article, work))
            match.match_reason_json = reason
            match.save(update_fields=["match_reason_json"])
        if article.status == ImportEntity.Status.UNRESOLVED:
            set_entity_default_status(article)
        auto_resolve_same_issue_article(article)
        refresh_groups_for_entity(article)


def apply_item_decision(item, decision_type, user=None, payload=None):
    payload = payload or {}
    if decision_type not in {
        ImportDecision.DecisionType.SKIP,
        ImportDecision.DecisionType.UPDATE_EXISTING,
        ImportDecision.DecisionType.REJECT,
        ImportDecision.DecisionType.POSTPONE,
    }:
        raise ValueError("Unsupported import item decision.")
    with transaction.atomic():
        ImportDecision.objects.create(
            import_batch=item.import_batch,
            item=item,
            decision_type=decision_type,
            target_type=item.matched_existing_type,
            target_id=item.matched_existing_id,
            payload_json=payload,
            created_by=user,
        )
        if decision_type in {ImportDecision.DecisionType.SKIP, ImportDecision.DecisionType.UPDATE_EXISTING}:
            item.status = ImportItem.Status.READY
        elif decision_type == ImportDecision.DecisionType.REJECT:
            item.status = ImportItem.Status.REJECTED
        elif decision_type == ImportDecision.DecisionType.POSTPONE:
            item.status = ImportItem.Status.POSTPONED
        item.save(update_fields=["status", "updated_at"])
        recalculate_import_status(item.import_batch)


def refresh_groups_for_entity(entity):
    group_ids = set()
    group_ids.update(ImportGroup.objects.filter(root_entity=entity).values_list("id", flat=True))
    parent_ids = ImportEntityRelation.objects.filter(child_entity=entity).values_list("parent_entity_id", flat=True)
    child_ids = ImportEntityRelation.objects.filter(parent_entity=entity).values_list("child_entity_id", flat=True)
    group_ids.update(ImportGroup.objects.filter(root_entity_id__in=list(parent_ids) + list(child_ids)).values_list("id", flat=True))
    for group in ImportGroup.objects.filter(id__in=group_ids):
        if group.root_entity:
            group.status = group_status_for_root(group.root_entity)
        group.save(update_fields=["status", "updated_at"])


def group_container_relation_type(group):
    if group.group_type == ImportGroup.GroupType.JOURNAL_ISSUE_GROUP:
        return "issue_has_article"
    if group.group_type == ImportGroup.GroupType.COLLECTION_VOLUME_GROUP:
        return "article_in_collection"
    return ""


def group_root_entity_type(group):
    if group.group_type == ImportGroup.GroupType.JOURNAL_ISSUE_GROUP:
        return ImportEntity.EntityType.JOURNAL_ISSUE
    if group.group_type == ImportGroup.GroupType.COLLECTION_VOLUME_GROUP:
        return ImportEntity.EntityType.COLLECTION
    return ""


def group_article_count(group):
    relation_type = group_container_relation_type(group)
    if not relation_type or not group.root_entity_id:
        return 0
    return ImportEntityRelation.objects.filter(
        import_batch=group.import_batch,
        parent_entity=group.root_entity,
        relation_type=relation_type,
    ).count()


def refresh_import_group(group):
    if not group.root_entity_id:
        group.status = ImportGroup.Status.NEEDS_REVIEW
    elif group.group_type in {ImportGroup.GroupType.JOURNAL_ISSUE_GROUP, ImportGroup.GroupType.COLLECTION_VOLUME_GROUP} and group_article_count(group) == 0:
        group.status = ImportGroup.Status.NEEDS_REVIEW
    else:
        group.status = group_status_for_root(group.root_entity)
    group.save(update_fields=["status", "updated_at"])


def split_article_to_new_group(group, article_entity, user=None):
    if not group.root_entity_id:
        raise ValueError("У группы нет контейнера.")
    relation_type = group_container_relation_type(group)
    entity_type = group_root_entity_type(group)
    if not relation_type or not entity_type:
        raise ValueError("Эту группу нельзя разделить через перенос статьи.")
    if article_entity.import_batch_id != group.import_batch_id or article_entity.entity_type != ImportEntity.EntityType.ARTICLE:
        raise ValueError("Выбранная запись не является статьёй этой пачки импорта.")
    relation = ImportEntityRelation.objects.filter(
        import_batch=group.import_batch,
        parent_entity=group.root_entity,
        child_entity=article_entity,
        relation_type=relation_type,
    ).first()
    if not relation:
        raise ValueError("Эта статья не связана с текущей группой.")

    with transaction.atomic():
        root = group.root_entity
        data = dict(root.data_json or {})
        data["split_from_group_id"] = group.id
        data["split_from_root_entity_id"] = root.id
        data["split_article_entity_id"] = article_entity.id
        article_label = article_entity.label or "статья"
        label = f"{root.label} / новая группа для: {article_label}"
        if root.status == ImportEntity.Status.WILL_CREATE:
            new_status = ImportEntity.Status.WILL_CREATE
            matched_existing_type = ""
            matched_existing_id = ""
        else:
            new_status = ImportEntity.Status.UNRESOLVED
            matched_existing_type = ""
            matched_existing_id = ""
        new_root = ImportEntity.objects.create(
            import_batch=group.import_batch,
            entity_type=entity_type,
            label=label[:1024],
            normalized_key=f"{root.normalized_key}|split|{article_entity.id}",
            data_json=data,
            status=new_status,
            confidence=root.confidence,
            matched_existing_type=matched_existing_type,
            matched_existing_id=matched_existing_id,
        )
        new_group = ImportGroup.objects.create(
            import_batch=group.import_batch,
            group_type=group.group_type,
            label=new_root.label,
            root_entity=new_root,
        )
        relation.delete()
        ImportEntityRelation.objects.create(
            import_batch=group.import_batch,
            parent_entity=new_root,
            child_entity=article_entity,
            relation_type=relation_type,
        )
        refresh_import_group(group)
        refresh_import_group(new_group)
        ImportDecision.objects.create(
            import_batch=group.import_batch,
            group=group,
            entity=article_entity,
            decision_type=ImportDecision.DecisionType.SPLIT_GROUP,
            payload_json={
                "from_group_id": group.id,
                "to_group_id": new_group.id,
                "from_root_entity_id": root.id,
                "to_root_entity_id": new_root.id,
                "article_entity_id": article_entity.id,
                "relation_type": relation_type,
            },
            created_by=user,
        )
        recalculate_import_status(group.import_batch)
    return new_group


def move_article_to_group(from_group, article_entity, target_group, user=None):
    if from_group.import_batch_id != target_group.import_batch_id:
        raise ValueError("Нельзя переносить статью между разными импортами.")
    if from_group.group_type != target_group.group_type:
        raise ValueError("Нельзя переносить статью в группу другого типа.")
    if not from_group.root_entity_id or not target_group.root_entity_id:
        raise ValueError("У одной из групп нет контейнера.")
    relation_type = group_container_relation_type(from_group)
    if not relation_type:
        raise ValueError("Этот тип группы не поддерживает перенос статей.")
    if article_entity.import_batch_id != from_group.import_batch_id or article_entity.entity_type != ImportEntity.EntityType.ARTICLE:
        raise ValueError("Выбранная запись не является статьёй этой пачки импорта.")
    relation = ImportEntityRelation.objects.filter(
        import_batch=from_group.import_batch,
        parent_entity=from_group.root_entity,
        child_entity=article_entity,
        relation_type=relation_type,
    ).first()
    if not relation:
        raise ValueError("Эта статья не связана с текущей группой.")

    with transaction.atomic():
        old_root = from_group.root_entity
        new_root = target_group.root_entity
        relation.delete()
        ImportEntityRelation.objects.get_or_create(
            import_batch=from_group.import_batch,
            parent_entity=new_root,
            child_entity=article_entity,
            relation_type=relation_type,
        )
        refresh_import_group(from_group)
        refresh_import_group(target_group)
        ImportDecision.objects.create(
            import_batch=from_group.import_batch,
            group=from_group,
            entity=article_entity,
            decision_type=ImportDecision.DecisionType.MOVE_TO_GROUP,
            target_type="import_group",
            target_id=str(target_group.id),
            payload_json={
                "from_group_id": from_group.id,
                "to_group_id": target_group.id,
                "from_root_entity_id": old_root.id,
                "to_root_entity_id": new_root.id,
                "article_entity_id": article_entity.id,
                "relation_type": relation_type,
            },
            created_by=user,
        )
        recalculate_import_status(from_group.import_batch)
    return target_group


def build_import_plan(import_batch):
    entities = ImportEntity.objects.filter(import_batch=import_batch)
    unresolved = list(entities.filter(status=ImportEntity.Status.UNRESOLVED))
    item_decisions = latest_item_decisions(import_batch)
    item_decision_counts = item_decision_type_counts(item_decisions.values())
    selected_update_fields = selected_update_field_count(item_decisions.values())
    preview = build_import_plan_preview(import_batch, entities, item_decisions)
    validation = validate_import_batch(import_batch)
    can_apply = not validation["blocking"] and import_batch.status != ImportBatch.Status.APPLIED
    problems = [item["message"] for item in validation["blocking"]]
    unresolved_for_plan = [
        entity
        for entity in unresolved
        if not author_entity_only_used_by_auto_resolved_existing_articles(entity)
    ]
    return {
        "can_apply": can_apply,
        "status_label": import_plan_status_label(import_batch, can_apply, problems),
        "will_create": entity_counts(entities.filter(status=ImportEntity.Status.WILL_CREATE)),
        "linked_existing": entity_counts(entities.filter(status=ImportEntity.Status.LINKED_EXISTING)),
        "will_update": entity_counts(entities.filter(status=ImportEntity.Status.WILL_UPDATE_EXISTING)),
        "ignored": entity_counts(entities.filter(status=ImportEntity.Status.IGNORED)),
        "item_decisions": item_decision_counts,
        "item_decision_rows": item_decision_rows(item_decision_counts),
        "selected_update_fields": selected_update_fields,
        "unresolved": [{"id": entity.id, "type": entity_type_label_ru(entity.entity_type), "label": entity.label} for entity in unresolved_for_plan],
        "problems": problems,
        "preview": preview,
        "validation": validation,
        "summary": {
            "create": len(preview["create_rows"]),
            "update": len(preview["update_rows"]) + sum(1 for row in preview["already_existing_rows"] if row["supplement_labels"]),
            "skip": len(preview["skipped_rows"]) + sum(1 for row in preview["already_existing_rows"] if not row["supplement_labels"]),
            "postpone_reject": len(preview["postponed_rows"]) + len(preview["rejected_rows"]),
        },
    }


def validate_import_batch(import_batch):
    validation = {"blocking": [], "warnings": [], "ok": []}
    entities = list(ImportEntity.objects.filter(import_batch=import_batch).prefetch_related("matches"))
    groups = list(ImportGroup.objects.filter(import_batch=import_batch).select_related("root_entity"))
    item_decisions = latest_item_decisions(import_batch)

    for entity in entities:
        if entity.status != ImportEntity.Status.UNRESOLVED:
            continue
        if author_entity_only_used_by_auto_resolved_existing_articles(entity):
            continue
        if entity_excluded_from_current_apply(entity):
            continue
        if entity.entity_type == ImportEntity.EntityType.AUTHOR:
            add_validation_item(
                validation,
                "blocking",
                "unresolved_author",
                "Есть автор без решения",
                f"Для автора «{entity.label}» нужно выбрать: создать нового или связать с существующим.",
                target_url=f"/imports/{import_batch.pk}/authors/",
                target_label=entity.label,
            )
        else:
            add_validation_item(
                validation,
                "blocking",
                "unresolved_entity",
                "Есть сущность без решения",
                f"Нужно выбрать действие для «{entity.label}».",
                target_url=entity_target_url(import_batch, entity),
                target_label=entity.label,
            )

    for item in ImportItem.objects.filter(import_batch=import_batch, status=ImportItem.Status.FOUND_EXISTING_WITH_DIFFERENCES):
        add_validation_item(
            validation,
            "blocking",
                "unresolved_found_existing_with_differences",
                "Не проверены возможные дополнения",
                f"Нужно проверить возможные дополнения по строке «{short_text(item.raw_text)}»: дополнить, пропустить, отложить или отклонить.",
            target_url=f"/imports/{import_batch.pk}/items/{item.pk}/",
            target_label=item.raw_text,
        )
    for item in ImportItem.objects.filter(import_batch=import_batch, status=ImportItem.Status.STRUCTURAL_CONFLICT):
        add_validation_item(
            validation,
            "blocking",
                "unresolved_structural_conflict",
                "Не проверен структурный конфликт",
                f"Нужно проверить структуру описания: источник описывает строку «{short_text(item.raw_text)}» иначе, чем база.",
            target_url=f"/imports/{import_batch.pk}/items/{item.pk}/",
            target_label=item.raw_text,
        )

    for article in [entity for entity in entities if entity.entity_type == ImportEntity.EntityType.ARTICLE]:
        if entity_excluded_from_current_apply(article):
            continue
        has_parent = ImportEntityRelation.objects.filter(
            import_batch=import_batch,
            child_entity=article,
            relation_type__in=["issue_has_article", "article_in_collection"],
        ).exists()
        if not has_parent:
            add_validation_item(
                validation,
                "blocking",
                "article_without_container",
                "Статья без контейнера",
                f"У статьи «{article.label}» не выбран выпуск журнала или сборник.",
                target_url=entity_target_url(import_batch, article),
                target_label=article.label,
            )

    for group in groups:
        article_count = group_article_count(group)
        if group.group_type in {ImportGroup.GroupType.JOURNAL_ISSUE_GROUP, ImportGroup.GroupType.COLLECTION_VOLUME_GROUP} and article_count == 0:
            add_validation_item(
                validation,
                "warnings",
                "empty_group",
                "Пустая группа",
                f"В группе «{group.label}» больше нет статей после разделения или переноса.",
                target_url=f"/imports/{import_batch.pk}/groups/{group.pk}/",
                target_label=group.label,
            )
            continue
        if import_group_needs_review(group):
            if group.root_entity and entity_excluded_from_current_apply(group.root_entity):
                continue
            add_validation_item(
                validation,
                "blocking",
                "group_needs_review",
                "Группа требует решения",
                f"Группа «{group.label}» ещё не готова к применению.",
                target_url=f"/imports/{import_batch.pk}/groups/{group.pk}/",
                target_label=group.label,
            )

    for entity in entities:
        if entity.status != ImportEntity.Status.WILL_CREATE:
            continue
        if author_entity_only_used_by_auto_resolved_existing_articles(entity):
            continue
        if entity_excluded_from_current_apply(entity):
            continue
        if not (entity.label or "").strip():
            add_validation_item(
                validation,
                "blocking",
                "create_entity_without_label",
                "У создаваемой записи нет названия",
                "Новая сущность не может быть создана без имени или заглавия.",
                target_url=entity_target_url(import_batch, entity),
                target_label=entity_type_label_ru(entity.entity_type),
            )
        if entity.entity_type == ImportEntity.EntityType.ARTICLE and not (entity.data_json.get("pages") or "").strip():
            add_validation_item(
                validation,
                "warnings",
                "new_article_without_pages",
                "У новой статьи нет страниц",
                f"Статья «{entity.label}» будет создана без страниц.",
                target_url=entity_target_url(import_batch, entity),
                target_label=entity.label,
            )
        if entity.entity_type == ImportEntity.EntityType.JOURNAL_ISSUE:
            if not str(entity.data_json.get("year") or "").strip() or not str(entity.data_json.get("issue_number") or "").strip():
                add_validation_item(
                    validation,
                    "warnings",
                    "new_issue_without_year_or_number",
                    "У выпуска неполные выходные данные",
                    f"Выпуск «{entity.label}» будет создан без года или номера.",
                    target_url=entity_target_url(import_batch, entity),
                    target_label=entity.label,
                )
        if entity.entity_type == ImportEntity.EntityType.COLLECTION and not str(entity.data_json.get("year") or "").strip():
            add_validation_item(
                validation,
                "warnings",
                "new_collection_without_year",
                "У сборника не указан год",
                f"Сборник «{entity.label}» будет создан без года.",
                target_url=entity_target_url(import_batch, entity),
                target_label=entity.label,
            )
        if entity.entity_type == ImportEntity.EntityType.AUTHOR and is_suspicious_author_label(entity.label):
            add_validation_item(
                validation,
                "warnings",
                "suspicious_author_name",
                "Сомнительное имя автора",
                f"Автор «{entity.label}» выглядит слишком коротким или неполным.",
                target_url=f"/imports/{import_batch.pk}/authors/",
                target_label=entity.label,
            )
        best_match = entity.matches.order_by("-score").first()
        if best_match and best_match.score >= 0.9:
            add_validation_item(
                validation,
                "warnings",
                "high_score_match_created_new",
                "Есть сильное совпадение, но выбрано создание новой записи",
                f"«{entity.label}» будет создано как новая запись, хотя найден кандидат «{describe_existing_entity(best_match.existing_type, best_match.existing_id)}».",
                target_url=entity_target_url(import_batch, entity),
                target_label=entity.label,
            )

    for decision in item_decisions.values():
        if decision.decision_type != ImportDecision.DecisionType.UPDATE_EXISTING:
            continue
        if not update_decision_has_applicable_fields(decision):
            add_validation_item(
                validation,
                "warnings",
                "update_existing_no_applicable_fields",
                "Дополнение не изменит запись",
                f"Для строки «{short_text(decision.item.raw_text)}» не выбраны применимые пустые поля.",
                target_url=f"/imports/{import_batch.pk}/items/{decision.item.pk}/",
                target_label=decision.item.raw_text,
            )

    if not ImportItem.objects.filter(
        import_batch=import_batch,
        status__in=[
            ImportItem.Status.NEEDS_REVIEW,
            ImportItem.Status.FOUND_EXISTING_WITH_DIFFERENCES,
            ImportItem.Status.STRUCTURAL_CONFLICT,
            ImportItem.Status.ERROR,
        ],
    ).exists():
        add_validation_item(validation, "ok", "items_have_state", "Строки имеют состояние", "Все строки импорта разобраны или имеют сохранённое решение.")
    if not any(
        entity.entity_type == ImportEntity.EntityType.AUTHOR
        and entity.status == ImportEntity.Status.UNRESOLVED
        and not author_entity_only_used_by_auto_resolved_existing_articles(entity)
        and not entity_excluded_from_current_apply(entity)
        for entity in entities
    ):
        add_validation_item(validation, "ok", "authors_resolved", "Авторы решены", "По всем авторам принято решение или авторы не требуются.")
    if not any(item["code"] == "article_without_container" for item in validation["blocking"]):
        add_validation_item(validation, "ok", "articles_have_containers", "Статьи имеют контейнеры", "Все статьи связаны с выпуском журнала или сборником.")
    add_validation_item(validation, "ok", "backup_will_be_created", "Перед применением будет создан backup", "Apply создаёт резервную копию SQLite перед записью.")
    add_validation_item(validation, "ok", "detailed_plan_ready", "Подробный план сформирован", "Редактор может проверить конкретные создаваемые и дополняемые записи.")
    return validation


def add_validation_item(validation, level, code, title, message, target_url="", target_label=""):
    validation[level].append(
        {
            "level": level,
            "code": code,
            "title": title,
            "message": message,
            "target_url": target_url,
            "target_label": target_label,
        }
    )


def short_text(value, length=120):
    value = normalize_whitespace(value)
    return value if len(value) <= length else value[: length - 1].rstrip() + "…"


def entity_target_url(import_batch, entity):
    group_id = group_id_for_entity(entity)
    if group_id:
        return f"/imports/{import_batch.pk}/groups/{group_id}/"
    item_id = entity.data_json.get("item_id")
    if item_id:
        return f"/imports/{import_batch.pk}/items/{item_id}/"
    if entity.entity_type == ImportEntity.EntityType.AUTHOR:
        return f"/imports/{import_batch.pk}/authors/"
    return f"/imports/{import_batch.pk}/review/"


def is_suspicious_author_label(label):
    normalized = normalize_whitespace(label)
    return len(normalized) <= 3 or normalized in {"-", "?", "н.", "н н"}


def update_decision_has_applicable_fields(decision):
    selected = selected_field_labels(decision)
    replacements = replacement_field_labels(decision)
    for row in decision.item.comparison_json.get("fields", []):
        if selected is not None and row.get("label") not in selected and row.get("label") not in replacements:
            continue
        if row.get("status") == "new_in_source" and normalize_whitespace(row.get("source")):
            return True
        if row.get("status") == "different" and row.get("label") in replacements and replacement_supported(row.get("label")):
            return True
    return False


def build_import_plan_preview(import_batch, entities=None, item_decisions=None):
    entities = entities if entities is not None else ImportEntity.objects.filter(import_batch=import_batch)
    item_decisions = item_decisions if item_decisions is not None else latest_item_decisions(import_batch)
    entity_list = list(entities.prefetch_related("matches") if hasattr(entities, "prefetch_related") else entities)
    return {
        "create_rows": create_preview_rows(import_batch, entity_list),
        "update_rows": update_existing_preview_rows(item_decisions.values()),
        "already_existing_rows": already_existing_article_preview_rows(import_batch, entity_list),
        "author_rows": author_preview_rows(import_batch, entity_list),
        "container_rows": container_preview_rows(import_batch, entity_list),
        "skipped_rows": item_decision_preview_rows(item_decisions.values(), ImportDecision.DecisionType.SKIP),
        "postponed_rows": item_decision_preview_rows(item_decisions.values(), ImportDecision.DecisionType.POSTPONE),
        "rejected_rows": item_decision_preview_rows(item_decisions.values(), ImportDecision.DecisionType.REJECT),
        "group_problem_rows": group_problem_preview_rows(import_batch),
    }


ENTITY_TYPE_LABELS_RU = {
    ImportEntity.EntityType.AUTHOR: "Автор",
    ImportEntity.EntityType.BOOK: "Книга",
    ImportEntity.EntityType.ARTICLE: "Статья",
    ImportEntity.EntityType.JOURNAL: "Журнал",
    ImportEntity.EntityType.JOURNAL_ISSUE: "Выпуск журнала",
    ImportEntity.EntityType.COLLECTION: "Сборник",
    ImportEntity.EntityType.COLLECTION_VOLUME: "Том сборника",
    ImportEntity.EntityType.PUBLISHER: "Издатель",
    ImportEntity.EntityType.THEME: "Тема",
    ImportEntity.EntityType.SECTION: "Раздел",
}

BATCH_STATUS_LABELS_RU = {
    ImportBatch.Status.DRAFT: "Черновик",
    ImportBatch.Status.PARSED: "Разобран",
    ImportBatch.Status.REVIEW_REQUIRED: "Требует решений",
    ImportBatch.Status.READY_TO_APPLY: "Готов к применению",
    ImportBatch.Status.APPLIED: "Применён",
    ImportBatch.Status.CANCELLED: "Отменён",
}

ITEM_DETECTED_TYPE_LABELS_RU = {
    ImportItem.DetectedType.BOOK: "Книга",
    ImportItem.DetectedType.JOURNAL_ARTICLE: "Статья в журнале",
    ImportItem.DetectedType.NEWSPAPER_ARTICLE: "Статья в газете",
    ImportItem.DetectedType.COLLECTION_ARTICLE: "Статья в сборнике",
    ImportItem.DetectedType.JOURNAL: "Журнал",
    ImportItem.DetectedType.JOURNAL_ISSUE: "Выпуск журнала",
    ImportItem.DetectedType.COLLECTION: "Сборник",
    ImportItem.DetectedType.VOLUME: "Том",
    ImportItem.DetectedType.AUTHOR: "Автор",
    ImportItem.DetectedType.UNKNOWN: "Не распознано",
}

ITEM_STATUS_LABELS_RU = {
    ImportItem.Status.PARSED: "Разобрано",
    ImportItem.Status.NEEDS_REVIEW: "Требует проверки",
    ImportItem.Status.FOUND_EXISTING_NO_CHANGES: "Найдена в базе без изменений",
    ImportItem.Status.FOUND_EXISTING_WITH_DIFFERENCES: "Найдена в базе, есть дополнения",
    ImportItem.Status.STRUCTURAL_CONFLICT: "Структура требует проверки",
    ImportItem.Status.READY: "Решение принято",
    ImportItem.Status.APPLIED: "Применено",
    ImportItem.Status.REJECTED: "Отклонено",
    ImportItem.Status.POSTPONED: "Отложено",
    ImportItem.Status.ERROR: "Ошибка",
}

ENTITY_STATUS_LABELS_RU = {
    ImportEntity.Status.UNRESOLVED: "Требует решения",
    ImportEntity.Status.WILL_CREATE: "Будет создано",
    ImportEntity.Status.LINKED_EXISTING: "Связано с существующей записью",
    ImportEntity.Status.WILL_UPDATE_EXISTING: "Будет дополнено",
    ImportEntity.Status.IGNORED: "Игнорируется",
    ImportEntity.Status.APPLIED: "Применено",
    ImportEntity.Status.ERROR: "Ошибка",
}

GROUP_TYPE_LABELS_RU = {
    ImportGroup.GroupType.JOURNAL_ISSUE_GROUP: "Журнал и выпуск",
    ImportGroup.GroupType.COLLECTION_VOLUME_GROUP: "Сборник",
    ImportGroup.GroupType.AUTHOR_GROUP: "Авторская группа",
    ImportGroup.GroupType.STANDALONE_BOOKS: "Отдельные книги",
    ImportGroup.GroupType.UNRESOLVED: "Неразобранная группа",
}

GROUP_STATUS_LABELS_RU = {
    ImportGroup.Status.NEEDS_REVIEW: "Требует решения",
    ImportGroup.Status.PARTIALLY_READY: "Частично готово",
    ImportGroup.Status.READY: "Готово",
    ImportGroup.Status.APPLIED: "Применено",
    ImportGroup.Status.POSTPONED: "Отложено",
    ImportGroup.Status.ERROR: "Ошибка",
}

DECISION_TYPE_LABELS_RU = {
    ImportDecision.DecisionType.CREATE: "Создать новую запись",
    ImportDecision.DecisionType.LINK_EXISTING: "Связать с существующей записью",
    ImportDecision.DecisionType.UPDATE_EXISTING: "Дополнить существующую запись",
    ImportDecision.DecisionType.SKIP: "Пропустить без изменений",
    ImportDecision.DecisionType.REJECT: "Отклонить",
    ImportDecision.DecisionType.POSTPONE: "Отложить",
    ImportDecision.DecisionType.SPLIT_GROUP: "Вынести в новую группу",
    ImportDecision.DecisionType.MOVE_TO_GROUP: "Перенести в другую группу",
}

COMPARISON_STATUS_LABELS_RU = {
    "same": "совпадает",
    "different": "отличается",
    "author_incomplete_initials": "в источнике неполные инициалы",
    "new_in_source": "будет добавлено при выборе «Дополнить»",
    "missing_in_source": "нет в источнике",
    "source_extra": "есть только в источнике; сейчас не записывается автоматически",
}


CONTAINER_ENTITY_TYPES = {
    ImportEntity.EntityType.JOURNAL,
    ImportEntity.EntityType.JOURNAL_ISSUE,
    ImportEntity.EntityType.COLLECTION,
    ImportEntity.EntityType.COLLECTION_VOLUME,
}


def entity_type_label_ru(entity_type):
    return ENTITY_TYPE_LABELS_RU.get(entity_type, entity_type)


def batch_status_label_ru(status):
    return BATCH_STATUS_LABELS_RU.get(status, status)


def item_detected_type_label_ru(detected_type):
    return ITEM_DETECTED_TYPE_LABELS_RU.get(detected_type, detected_type)


def item_status_label_ru(status):
    return ITEM_STATUS_LABELS_RU.get(status, status)


def entity_status_label_ru(status):
    return ENTITY_STATUS_LABELS_RU.get(status, status)


def group_type_label_ru(group_type):
    return GROUP_TYPE_LABELS_RU.get(group_type, group_type)


def group_status_label_ru(status):
    return GROUP_STATUS_LABELS_RU.get(status, status)


def decision_type_label_ru(decision_type):
    return DECISION_TYPE_LABELS_RU.get(decision_type, decision_type)


def comparison_status_label_ru(status):
    return COMPARISON_STATUS_LABELS_RU.get(status, status)


def import_plan_status_label(import_batch, can_apply, problems=None):
    if import_batch.status == ImportBatch.Status.APPLIED:
        return "Применён"
    if import_batch.status == ImportBatch.Status.CANCELLED:
        return "Отменён"
    if can_apply and not problems:
        return "Готов к применению"
    return "Требует решений"


def create_preview_rows(import_batch, entities):
    rows = []
    for entity in entities:
        if entity.status != ImportEntity.Status.WILL_CREATE:
            continue
        if author_entity_only_used_by_auto_resolved_existing_articles(entity):
            continue
        if entity_excluded_from_current_apply(entity):
            continue
        rows.append(
            {
                "entity_id": entity.id,
                "type": entity.entity_type,
                "type_label": entity_type_label_ru(entity.entity_type),
                "label": entity.label,
                "details": entity_create_details(import_batch, entity),
                "group_id": group_id_for_entity(entity),
            }
        )
    return rows


def entity_create_details(import_batch, entity):
    details = []
    authors = parent_entities(entity, "author_of")
    if authors:
        details.append("Авторы: " + "; ".join(author.label for author in authors))
    issue = parent_entity(entity, "issue_has_article")
    if issue:
        details.append(f"Выпуск: {issue.label}")
    collection = parent_entity(entity, "article_in_collection")
    if collection:
        details.append(f"Сборник: {collection.label}")
    journal = parent_entity(entity, "journal_has_issue")
    if journal:
        details.append(f"Журнал: {journal.label}")
    if entity.entity_type == ImportEntity.EntityType.JOURNAL:
        count = ImportEntityRelation.objects.filter(import_batch=import_batch, parent_entity=entity, relation_type="journal_has_issue").count()
        if count:
            details.append(f"Выпусков в импорте: {count}")
    if entity.entity_type == ImportEntity.EntityType.COLLECTION:
        count = ImportEntityRelation.objects.filter(import_batch=import_batch, parent_entity=entity, relation_type="article_in_collection").count()
        if count:
            details.append(f"Статей в сборнике: {count}")
    return details


def update_existing_preview_rows(decisions):
    rows = []
    for decision in decisions:
        if decision.decision_type != ImportDecision.DecisionType.UPDATE_EXISTING:
            continue
        item = decision.item
        selected_labels = selected_field_labels(decision)
        replacement_labels = replacement_field_labels(decision)
        applied_fields = []
        replacement_fields = []
        ignored_fields = []
        for row in item.comparison_json.get("fields", []):
            label = row.get("label", "")
            if selected_labels is not None and label not in selected_labels and label not in replacement_labels:
                continue
            field = {
                "label": label,
                "existing": row.get("existing", ""),
                "source": row.get("source", ""),
                "status": row.get("status", ""),
            }
            if row.get("status") == "new_in_source" and (selected_labels is None or label in selected_labels):
                applied_fields.append(field)
            elif row.get("status") == "different" and label in replacement_labels:
                if replacement_supported(label):
                    replacement_fields.append(field)
                else:
                    ignored_fields.append({**field, "reason": "replacement_not_supported"})
            elif selected_labels is not None:
                ignored_fields.append(field)
        rows.append(
            {
                "item_id": item.id,
                "existing_label": describe_existing_entity(item.matched_existing_type, item.matched_existing_id)
                if item.matched_existing_id
                else item.raw_text,
                "raw_text": item.raw_text,
                "fields": applied_fields,
                "replacement_fields": replacement_fields,
                "ignored_fields": ignored_fields,
            }
        )
    return rows


def author_preview_rows(import_batch, entities):
    rows = []
    for entity in entities:
        if entity.entity_type != ImportEntity.EntityType.AUTHOR:
            continue
        if author_entity_only_used_by_auto_resolved_existing_articles(entity):
            continue
        if entity_excluded_from_current_apply(entity):
            continue
        related_count = ImportEntityRelation.objects.filter(import_batch=import_batch, parent_entity=entity, relation_type="author_of").count()
        action = ""
        target_label = ""
        if entity.status == ImportEntity.Status.WILL_CREATE:
            action = "Будет создан"
        elif entity.status == ImportEntity.Status.LINKED_EXISTING:
            action = "Будет связан"
            target_label = describe_existing_entity(entity.matched_existing_type, entity.matched_existing_id)
        elif entity.status == ImportEntity.Status.UNRESOLVED:
            action = "Требует решения"
        elif entity.status == ImportEntity.Status.IGNORED:
            action = "Игнорируется"
        rows.append(
            {
                "entity_id": entity.id,
                "label": entity.label,
                "action": action,
                "target_label": target_label,
                "related_count": related_count,
            }
        )
    return rows


def already_existing_article_preview_rows(import_batch, entities):
    rows = []
    for entity in entities:
        if not is_auto_resolved_existing_article(entity):
            continue
        item = import_item_for_entity(entity)
        supplement_labels = safe_linked_existing_supplement_labels(entity)
        issue = parent_entity(entity, "issue_has_article")
        rows.append(
            {
                "item_id": item.id if item else None,
                "raw_text": item.raw_text if item else entity.data_json.get("raw_text", ""),
                "existing_label": describe_existing_entity(entity.matched_existing_type, entity.matched_existing_id),
                "issue_label": issue.label if issue else "",
                "supplement_labels": supplement_labels,
                "action": "Будут дополнены пустые технические поля" if supplement_labels else "Будет пропущено без изменений",
            }
        )
    return rows


def container_preview_rows(import_batch, entities):
    rows = []
    for entity in entities:
        if entity.entity_type not in CONTAINER_ENTITY_TYPES:
            continue
        if entity_excluded_from_current_apply(entity):
            continue
        action = ""
        target_label = ""
        if entity.status == ImportEntity.Status.WILL_CREATE:
            action = "Будет создано"
        elif entity.status == ImportEntity.Status.LINKED_EXISTING:
            action = "Будет связано"
            target_label = describe_existing_entity(entity.matched_existing_type, entity.matched_existing_id)
        elif entity.status == ImportEntity.Status.UNRESOLVED:
            action = "Требует решения"
        elif entity.status == ImportEntity.Status.IGNORED:
            action = "Игнорируется"
        rows.append(
            {
                "entity_id": entity.id,
                "group_id": group_id_for_entity(entity),
                "type_label": entity_type_label_ru(entity.entity_type),
                "label": entity.label,
                "action": action,
                "target_label": target_label,
                "details": entity_create_details(import_batch, entity),
            }
        )
    return rows


def item_decision_preview_rows(decisions, decision_type):
    rows = []
    for decision in decisions:
        if decision.decision_type != decision_type:
            continue
        item = decision.item
        rows.append(
            {
                "item_id": item.id,
                "raw_text": item.raw_text,
                "existing_label": describe_existing_entity(item.matched_existing_type, item.matched_existing_id)
                if item.matched_existing_id
                else "",
            }
        )
    return rows


def group_problem_preview_rows(import_batch):
    rows = []
    groups = ImportGroup.objects.filter(import_batch=import_batch).select_related("root_entity")
    for group in groups:
        if import_group_needs_review(group):
            if group.root_entity and entity_excluded_from_current_apply(group.root_entity):
                continue
            rows.append(
                {
                    "group_id": group.id,
                    "label": group.label,
                    "type": group_type_label_ru(group.group_type),
                    "status": group_status_label_ru(group.status),
                    "article_count": group_article_count(group),
                }
            )
    return rows


def import_group_needs_review(group):
    if group.root_entity and entity_excluded_from_current_apply(group.root_entity):
        return False
    if group.group_type == ImportGroup.GroupType.STANDALONE_BOOKS:
        return ImportEntity.objects.filter(
            import_batch=group.import_batch,
            entity_type=ImportEntity.EntityType.BOOK,
            status=ImportEntity.Status.UNRESOLVED,
        ).exists()
    needs_review = group.status not in {ImportGroup.Status.READY, ImportGroup.Status.APPLIED}
    if group.root_entity and group.root_entity.status == ImportEntity.Status.UNRESOLVED:
        needs_review = True
    if group.group_type in {ImportGroup.GroupType.JOURNAL_ISSUE_GROUP, ImportGroup.GroupType.COLLECTION_VOLUME_GROUP} and group_article_count(group) == 0:
        needs_review = True
    return needs_review


def group_id_for_entity(entity):
    group = ImportGroup.objects.filter(import_batch=entity.import_batch, root_entity=entity).first()
    return group.id if group else None


def entity_counts(queryset):
    counts = {}
    for entity in queryset:
        counts[entity.entity_type] = counts.get(entity.entity_type, 0) + 1
    return counts


def latest_item_decisions(import_batch):
    decisions = {}
    queryset = (
        ImportDecision.objects.filter(import_batch=import_batch, item__isnull=False)
        .select_related("item")
        .order_by("item_id", "-updated_at", "-id")
    )
    for decision in queryset:
        decisions.setdefault(decision.item_id, decision)
    return decisions


def item_decision_type_counts(decisions):
    counts = {
        ImportDecision.DecisionType.SKIP: 0,
        ImportDecision.DecisionType.UPDATE_EXISTING: 0,
        ImportDecision.DecisionType.REJECT: 0,
        ImportDecision.DecisionType.POSTPONE: 0,
    }
    for decision in decisions:
        if decision.decision_type in counts:
            counts[decision.decision_type] += 1
    return counts


def item_decision_rows(counts):
    return [
        {"label": "Пропущено как уже существующие", "count": counts.get(ImportDecision.DecisionType.SKIP, 0)},
        {"label": "Помечено к дополнению существующих записей", "count": counts.get(ImportDecision.DecisionType.UPDATE_EXISTING, 0)},
        {"label": "Отложено", "count": counts.get(ImportDecision.DecisionType.POSTPONE, 0)},
        {"label": "Отклонено", "count": counts.get(ImportDecision.DecisionType.REJECT, 0)},
    ]


def selected_update_field_count(decisions):
    total = 0
    for decision in decisions:
        if decision.decision_type != ImportDecision.DecisionType.UPDATE_EXISTING:
            continue
        selected = decision.payload_json.get("selected_fields")
        if selected is None:
            total += safe_new_in_source_field_count(decision.item)
        else:
            total += len(selected)
        total += len(decision.payload_json.get("replacement_fields") or [])
    return total


def safe_new_in_source_field_count(item):
    return sum(1 for row in item.comparison_json.get("fields", []) if row.get("status") == "new_in_source")


def readiness_problems(import_batch):
    return [item["message"] for item in validate_import_batch(import_batch)["blocking"]]


def backup_sqlite_database(label):
    db_name = settings.DATABASES["default"].get("NAME", "")
    db_path = Path(db_name)
    if not db_path.exists() or db_path.suffix != ".sqlite":
        return None
    timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{db_path.stem}.{label}.{timestamp}{db_path.suffix}"
    copy2(db_path, backup_path)
    return backup_path


def apply_update_existing_item_decisions(import_batch):
    updated = []
    decisions = latest_item_decisions(import_batch)
    for decision in decisions.values():
        if decision.decision_type != ImportDecision.DecisionType.UPDATE_EXISTING:
            continue
        item = decision.item
        if item.matched_existing_type != "work" or not item.matched_existing_id:
            updated.append(update_noop_entry(item, "unsupported_target"))
            item.status = ImportItem.Status.APPLIED
            item.save(update_fields=["status", "updated_at"])
            continue
        try:
            work = Work.objects.select_for_update().get(work_id=item.matched_existing_id)
        except Work.DoesNotExist:
            updated.append(update_noop_entry(item, "target_not_found"))
            item.status = ImportItem.Status.APPLIED
            item.save(update_fields=["status", "updated_at"])
            continue
        entry = apply_safe_work_updates(item, work, decision, import_batch=import_batch)
        updated.append(entry)
        item.status = ImportItem.Status.APPLIED
        item.save(update_fields=["status", "updated_at"])
    return updated


def update_noop_entry(item, reason):
    return {
        "type": "work",
        "id": item.matched_existing_id,
        "item_id": item.id,
        "label": item.raw_text,
        "updated_fields": [],
        "skipped_fields": [],
        "status": "no_op",
        "reason": reason,
    }


def apply_safe_work_updates(item, work, decision=None, import_batch=None):
    updated_fields = []
    skipped_fields = []
    work_updates = {}
    source_updates = {}
    source = getattr(work, "target_source", None)
    selected_labels = selected_field_labels(decision)
    replacement_labels = replacement_field_labels(decision)
    provenance = import_source_provenance(import_batch or item.import_batch)
    if provenance:
        fill_source_data_source(source, provenance, source_updates, updated_fields)
    for row in item.comparison_json.get("fields", []):
        label = row.get("label", "")
        wants_supplement = selected_labels is None or label in selected_labels
        wants_replacement = label in replacement_labels
        if not wants_supplement and not wants_replacement:
            continue
        value = normalize_whitespace(row.get("source", ""))
        if not value:
            continue
        if row.get("status") == "different":
            if wants_replacement:
                applied = apply_work_replacement_field(item, work, source, label, value, work_updates, source_updates, updated_fields)
                if not applied:
                    skipped_fields.append({"label": label, "value": value, "reason": "replacement_not_supported"})
            elif selected_labels is not None:
                skipped_fields.append({"label": label, "value": value, "reason": "replacement_not_selected"})
            continue
        if row.get("status") != "new_in_source":
            if selected_labels is not None or wants_replacement:
                skipped_fields.append({"label": label, "value": value, "reason": "status_not_supported"})
            continue
        applied = apply_safe_work_field(item, work, source, label, value, work_updates, source_updates, updated_fields)
        if not applied:
            skipped_fields.append({"label": label, "value": value, "reason": "unsupported_or_nonempty"})
    if work_updates:
        for field, value in work_updates.items():
            setattr(work, field, value)
        work.save(update_fields=list(work_updates))
    if source and source_updates:
        source_updates["updated_at"] = timezone.now()
        for field, value in source_updates.items():
            setattr(source, field, value)
        source.save(update_fields=list(source_updates))
    return {
        "type": "work",
        "id": work.work_id,
        "item_id": item.id,
        "label": describe_existing_entity("work", work.work_id),
        "updated_fields": updated_fields,
        "skipped_fields": skipped_fields,
        "status": "updated" if updated_fields else "no_op",
    }


def import_source_provenance(import_batch):
    if not import_batch or not import_batch.source_name:
        return ""
    if import_batch.source_type:
        return f"{import_batch.source_name} ({import_batch.source_type})"
    return import_batch.source_name


def selected_field_labels(decision):
    if not decision:
        return None
    selected = decision.payload_json.get("selected_fields")
    if selected is None:
        return None
    return set(selected)


def replacement_field_labels(decision):
    if not decision:
        return set()
    return set(decision.payload_json.get("replacement_fields") or [])


def replacement_supported(label):
    return label in {
        "Уточнение названия",
        "Ответственность",
        "Сведения об издании",
        "Место издания",
        "Издательство / типография",
        "Год",
        "Страницы",
        "Страницы статьи",
        "Примечания",
    }


def apply_work_replacement_field(item, work, source, label, value, work_updates, source_updates, updated_fields):
    if not replacement_supported(label):
        return False
    if label == "Уточнение названия":
        return replace_text_field(work, "title_remainder", value, work_updates, updated_fields, "work") | replace_source_text_field(source, "title_remainder", value, source_updates, updated_fields)
    if label == "Ответственность":
        return replace_text_field(work, "responsibility_statement", value, work_updates, updated_fields, "work") | replace_source_text_field(source, "responsibility_statement", value, source_updates, updated_fields)
    if label == "Сведения об издании":
        return replace_text_field(work, "edition_statement", value, work_updates, updated_fields, "work") | replace_source_text_field(source, "edition_statement", value, source_updates, updated_fields)
    if label == "Место издания":
        return replace_text_field(work, "publication_place", value, work_updates, updated_fields, "work") | replace_source_text_field(source, "publication_place", value, source_updates, updated_fields)
    if label == "Издательство / типография":
        return replace_text_field(work, "publisher", value, work_updates, updated_fields, "work") | replace_source_text_field(source, "publisher", value, source_updates, updated_fields)
    if label == "Год":
        applied = replace_text_field(work, "publication_date", value, work_updates, updated_fields, "work")
        year = normalize_year(value)
        if year:
            work_updates["inferred_year"] = int(year)
            updated_fields.append({"model": "work", "field": "inferred_year", "value": int(year), "operation": "replace"})
            applied = True
        if source:
            applied = replace_source_text_field(source, "publication_date", value, source_updates, updated_fields) or applied
            if year:
                source_updates["inferred_year"] = int(year)
                updated_fields.append({"model": "source", "field": "inferred_year", "value": int(year), "operation": "replace"})
                applied = True
        return applied
    if label == "Страницы":
        return replace_text_field(work, "extent", value, work_updates, updated_fields, "work") | replace_source_text_field(source, "extent", value, source_updates, updated_fields)
    if label == "Страницы статьи":
        return replace_text_field(work, "article_pages", value, work_updates, updated_fields, "work")
    if label == "Примечания":
        return replace_text_field(work, "notes", value, work_updates, updated_fields, "work") | replace_source_text_field(source, "notes", value, source_updates, updated_fields)
    return False


def apply_safe_work_field(item, work, source, label, value, work_updates, source_updates, updated_fields):
    if label == "Место издания":
        return fill_text_field(work, "publication_place", value, work_updates, updated_fields, "work") | fill_source_text_field(source, "publication_place", value, source_updates, updated_fields)
    if label == "Издательство / типография":
        return fill_text_field(work, "publisher", value, work_updates, updated_fields, "work") | fill_source_text_field(source, "publisher", value, source_updates, updated_fields)
    if label == "Год":
        applied = fill_text_field(work, "publication_date", value, work_updates, updated_fields, "work")
        year = normalize_year(value)
        if year and work.inferred_year is None:
            work_updates["inferred_year"] = int(year)
            updated_fields.append({"model": "work", "field": "inferred_year", "value": int(year)})
            applied = True
        if source:
            applied = fill_source_text_field(source, "publication_date", value, source_updates, updated_fields) or applied
            if year and source.inferred_year is None:
                source_updates["inferred_year"] = int(year)
                updated_fields.append({"model": "source", "field": "inferred_year", "value": int(year)})
                applied = True
        return applied
    if label == "Страницы":
        return fill_text_field(work, "extent", value, work_updates, updated_fields, "work") | fill_source_text_field(source, "extent", value, source_updates, updated_fields)
    if label == "Страницы статьи":
        return fill_text_field(work, "article_pages", value, work_updates, updated_fields, "work")
    if label == "Родительское издание":
        raw_value = item.parsed_data_json.get("raw_parent_description") or value
        applied = fill_text_field(work, "host_title", value, work_updates, updated_fields, "work")
        if not applied:
            applied = fill_text_field(work, "publication_details", raw_value, work_updates, updated_fields, "work")
        if source:
            source_applied = fill_source_text_field(source, "raw_host_title", value, source_updates, updated_fields)
            if not source_applied:
                source_applied = fill_source_text_field(source, "raw_publication_details", raw_value, source_updates, updated_fields)
            applied = source_applied or applied
        return applied
    return False


def apply_linked_existing_entity_supplements(import_batch):
    updated = []
    provenance = import_source_provenance(import_batch)
    for entity in ImportEntity.objects.filter(
        import_batch=import_batch,
        status=ImportEntity.Status.LINKED_EXISTING,
        matched_existing_type="work",
        entity_type__in=[ImportEntity.EntityType.BOOK, ImportEntity.EntityType.ARTICLE],
    ):
        try:
            work = Work.objects.select_for_update().get(work_id=entity.matched_existing_id)
        except Work.DoesNotExist:
            continue
        entry = apply_safe_entity_work_supplements(import_batch, entity, work, provenance)
        if entry["updated_fields"] or entry["skipped_fields"] or is_auto_resolved_existing_article(entity):
            updated.append(entry)
        item = import_item_for_entity(entity)
        if item and is_auto_resolved_existing_article(entity):
            item.status = ImportItem.Status.APPLIED
            item.save(update_fields=["status", "updated_at"])
    for entity in ImportEntity.objects.filter(
        import_batch=import_batch,
        status=ImportEntity.Status.LINKED_EXISTING,
        matched_existing_type="journal_issue",
        entity_type=ImportEntity.EntityType.JOURNAL_ISSUE,
    ):
        try:
            issue = JournalIssue.objects.select_for_update().get(journal_issue_id=entity.matched_existing_id)
        except JournalIssue.DoesNotExist:
            continue
        entry = apply_safe_issue_supplements(entity, issue)
        if entry["updated_fields"]:
            updated.append(entry)
    return updated


def apply_safe_entity_work_supplements(import_batch, entity, work, provenance):
    updated_fields = []
    skipped_fields = []
    work_updates = {}
    source_updates = {}
    data = entity.data_json or {}
    source = getattr(work, "target_source", None)
    item = import_item_for_entity(entity)
    if provenance:
        fill_source_data_source(source, provenance, source_updates, updated_fields)
    if data.get("pages"):
        fill_text_field(work, "article_pages", data["pages"], work_updates, updated_fields, "work")
        article = work_article(work)
        if article:
            article_updates = {}
            if not article.pages:
                article.pages = data["pages"]
                article_updates["pages"] = data["pages"]
                updated_fields.append({"model": "article", "field": "pages", "value": data["pages"]})
            if not article.pages_raw:
                article.pages_raw = data["pages"]
                article_updates["pages_raw"] = data["pages"]
                updated_fields.append({"model": "article", "field": "pages_raw", "value": data["pages"]})
            if article_updates:
                article.save(update_fields=list(article_updates))
    if data.get("year"):
        year_value = str(data["year"])
        fill_text_field(work, "publication_date", year_value, work_updates, updated_fields, "work")
        year = normalize_year(year_value)
        if year and work.inferred_year is None:
            work_updates["inferred_year"] = int(year)
            updated_fields.append({"model": "work", "field": "inferred_year", "value": int(year)})
        if source:
            fill_source_text_field(source, "publication_date", year_value, source_updates, updated_fields)
            if year and source.inferred_year is None:
                source_updates["inferred_year"] = int(year)
                updated_fields.append({"model": "source", "field": "inferred_year", "value": int(year)})
    raw_host = data.get("raw_parent_description") or data.get("raw_host") or data.get("raw_text") or ""
    parent_title = data.get("parent_title") or data.get("journal_title") or data.get("collection_title") or ""
    if parent_title:
        fill_text_field(work, "host_title", parent_title, work_updates, updated_fields, "work")
    if raw_host:
        fill_text_field(work, "publication_details", raw_host, work_updates, updated_fields, "work")
        if source:
            fill_source_text_field(source, "raw_publication_details", raw_host, source_updates, updated_fields)
    if parent_title and source:
        fill_source_text_field(source, "raw_host_title", parent_title, source_updates, updated_fields)
    if work_updates:
        for field, value in work_updates.items():
            setattr(work, field, value)
        work.save(update_fields=list(work_updates))
    if source and source_updates:
        source_updates["updated_at"] = timezone.now()
        for field, value in source_updates.items():
            setattr(source, field, value)
        source.save(update_fields=list(source_updates))
    return {
        "type": "work",
        "id": work.work_id,
        "entity_id": entity.id,
        "item_id": item.id if item else None,
        "label": describe_existing_entity("work", work.work_id),
        "updated_fields": updated_fields,
        "skipped_fields": skipped_fields,
        "status": "updated" if updated_fields else "no_op",
        "reason": "" if updated_fields else "already_existing_no_safe_supplements",
        "source_name": import_batch.source_name,
    }


def apply_safe_issue_supplements(entity, issue):
    updated_fields = []
    updates = {}
    data = entity.data_json or {}
    year = normalize_year(data.get("year"))
    if year and issue.year is None:
        updates["year"] = int(year)
        updated_fields.append({"model": "journal_issue", "field": "year", "value": int(year)})
    issue_number = data.get("issue_number") or ""
    if issue_number and not issue.issue_number:
        updates["issue_number"] = issue_number
        updated_fields.append({"model": "journal_issue", "field": "issue_number", "value": issue_number})
    raw_value = entity.label
    if raw_value and not issue.publication_details:
        updates["publication_details"] = raw_value
        updated_fields.append({"model": "journal_issue", "field": "publication_details", "value": raw_value})
    if updates:
        for field, value in updates.items():
            setattr(issue, field, value)
        issue.save(update_fields=list(updates))
    return {
        "type": "journal_issue",
        "id": issue.journal_issue_id,
        "entity_id": entity.id,
        "label": describe_existing_entity("journal_issue", issue.journal_issue_id),
        "updated_fields": updated_fields,
        "skipped_fields": [],
        "status": "updated" if updated_fields else "no_op",
    }


def fill_text_field(obj, field, value, updates, updated_fields, model_name):
    if not obj or getattr(obj, field):
        return False
    updates[field] = value
    updated_fields.append({"model": model_name, "field": field, "value": value})
    return True


def fill_source_text_field(source, field, value, updates, updated_fields):
    return fill_text_field(source, field, value, updates, updated_fields, "source")


def replace_text_field(obj, field, value, updates, updated_fields, model_name):
    if not obj:
        return False
    updates[field] = value
    updated_fields.append({"model": model_name, "field": field, "value": value, "operation": "replace"})
    return True


def replace_source_text_field(source, field, value, updates, updated_fields):
    return replace_text_field(source, field, value, updates, updated_fields, "source")


def fill_source_data_source(source, value, updates, updated_fields):
    if not source or not value:
        return False
    current = normalize_whitespace(source.data_source)
    if current and current != "editor":
        return False
    updates["data_source"] = value
    updated_fields.append({"model": "source", "field": "data_source", "value": value})
    return True


def serialized_import_decisions(import_batch):
    decisions = []
    for decision in import_batch.decisions.values(
        "id",
        "entity_id",
        "item_id",
        "group_id",
        "decision_type",
        "target_type",
        "target_id",
        "payload_json",
        "created_by_id",
        "created_at",
        "updated_at",
    ):
        decisions.append(
            {
                **decision,
                "created_at": decision["created_at"].isoformat() if decision["created_at"] else None,
                "updated_at": decision["updated_at"].isoformat() if decision["updated_at"] else None,
            }
        )
    return decisions


@transaction.atomic
def apply_import_batch(import_batch, user=None):
    plan = build_import_plan(import_batch)
    if not plan["can_apply"] or plan["problems"]:
        return {"applied": False, "problems": plan["problems"] or ["Есть нерешённые решения."]}
    backup_path = backup_sqlite_database("before-import-apply")
    created = []
    updated = apply_update_existing_item_decisions(import_batch)
    updated.extend(apply_linked_existing_entity_supplements(import_batch))
    relations = []
    entity_map = {}
    language = Language.objects.filter(code="ru").first() or Language.objects.first()
    section = Section.objects.order_by("sort_order", "source_code").first()

    for entity in entities_for_current_apply(import_batch, entity_type=ImportEntity.EntityType.AUTHOR):
        if author_entity_only_used_by_auto_resolved_existing_articles(entity):
            continue
        if entity.status == ImportEntity.Status.LINKED_EXISTING:
            entity_map[entity.id] = entity.matched_existing_id
        elif entity.status == ImportEntity.Status.WILL_CREATE:
            author = Author.objects.create(author_id=next_model_id(Author, "author_id", "author"), display_name=entity.data_json.get("name") or entity.label, heading_name=entity.data_json.get("name") or entity.label, sort_name=entity.data_json.get("name") or entity.label)
            entity_map[entity.id] = author.author_id
            entity.created_entity_type = "author"
            entity.created_entity_id = author.author_id
            entity.status = ImportEntity.Status.APPLIED
            entity.save()
            created.append({"type": "author", "id": author.author_id, "label": author.display_name, "source_name": import_batch.source_name})

    for entity in entities_for_current_apply(import_batch, entity_type=ImportEntity.EntityType.JOURNAL):
        if entity.status == ImportEntity.Status.LINKED_EXISTING:
            entity_map[entity.id] = entity.matched_existing_id
        elif entity.status == ImportEntity.Status.WILL_CREATE:
            journal = Journal.objects.create(journal_id=next_model_id(Journal, "journal_id", "journal"), title=entity.data_json.get("title") or entity.label)
            entity_map[entity.id] = journal.journal_id
            entity.created_entity_type = "journal"
            entity.created_entity_id = journal.journal_id
            entity.status = ImportEntity.Status.APPLIED
            entity.save()
            created.append({"type": "journal", "id": journal.journal_id, "label": journal.title, "source_name": import_batch.source_name})

    for entity in entities_for_current_apply(import_batch, entity_type=ImportEntity.EntityType.JOURNAL_ISSUE):
        if entity.status == ImportEntity.Status.LINKED_EXISTING:
            entity_map[entity.id] = entity.matched_existing_id
            continue
        if entity.status != ImportEntity.Status.WILL_CREATE:
            continue
        journal_entity = parent_entity(entity, "journal_has_issue")
        journal_id = entity_map.get(journal_entity.id) if journal_entity else ""
        if not journal_id:
            continue
        data = entity.data_json
        issue = JournalIssue.objects.create(
            journal_issue_id=next_model_id(JournalIssue, "journal_issue_id", "journal-issue"),
            journal_id=journal_id,
            year=int(data["year"]) if str(data.get("year", "")).isdigit() else None,
            issue_number=data.get("issue_number", ""),
            publication_details=entity.label,
        )
        entity_map[entity.id] = issue.journal_issue_id
        entity.created_entity_type = "journal_issue"
        entity.created_entity_id = issue.journal_issue_id
        entity.status = ImportEntity.Status.APPLIED
        entity.save()
        created.append({"type": "journal_issue", "id": issue.journal_issue_id, "label": str(issue), "source_name": import_batch.source_name})

    for entity in entities_for_current_apply(import_batch, entity_type=ImportEntity.EntityType.COLLECTION):
        if entity.status == ImportEntity.Status.LINKED_EXISTING:
            entity_map[entity.id] = entity.matched_existing_id
        elif entity.status == ImportEntity.Status.WILL_CREATE:
            source_number = next_source_number()
            work = Work.objects.create(
                work_id=next_model_id(Work, "work_id", "work"),
                source_number=source_number,
                source_sequence=source_number,
                work_type=Work.WorkType.CONTAINER,
                is_container=True,
                source_section=section,
                language=language,
                title=entity.data_json.get("title") or entity.label,
                publication_date=entity.data_json.get("year", ""),
                inferred_year=int(entity.data_json["year"]) if str(entity.data_json.get("year", "")).isdigit() else None,
                description_status=Work.DescriptionStatus.NEEDS_REVIEW,
            )
            collection = Collection.objects.create(
                collection_id=next_model_id(Collection, "collection_id", "collection"),
                parent_work=work,
                title=entity.data_json.get("title") or entity.label,
                year=int(entity.data_json["year"]) if str(entity.data_json.get("year", "")).isdigit() else None,
                source_text=entity.data_json.get("raw_text", ""),
            )
            entity_map[entity.id] = collection.collection_id
            entity.created_entity_type = "collection"
            entity.created_entity_id = collection.collection_id
            entity.status = ImportEntity.Status.APPLIED
            entity.save()
            created.append({"type": "collection", "id": collection.collection_id, "label": collection.title, "source_name": import_batch.source_name})

    for entity_type in [ImportEntity.EntityType.BOOK, ImportEntity.EntityType.ARTICLE]:
        for entity in entities_for_current_apply(import_batch, entity_type=entity_type, status=ImportEntity.Status.WILL_CREATE):
            source_number = next_source_number()
            work = Work.objects.create(
                work_id=next_model_id(Work, "work_id", "work"),
                source_number=source_number,
                source_sequence=source_number,
                work_type=Work.WorkType.ARTICLE if entity_type == ImportEntity.EntityType.ARTICLE else Work.WorkType.BOOK,
                source_section=section,
                language=language,
                raw_author_string="; ".join(entity.data_json.get("authors", [])),
                title=entity.data_json.get("title") or entity.label,
                publication_place=entity.data_json.get("publication_place", ""),
                publisher=entity.data_json.get("publisher", ""),
                publication_date=entity.data_json.get("year", ""),
                inferred_year=int(entity.data_json["year"]) if str(entity.data_json.get("year", "")).isdigit() else None,
                extent=entity.data_json.get("extent", ""),
                article_pages=entity.data_json.get("pages", ""),
                publication_details=entity.data_json.get("raw_text", ""),
                description_status=Work.DescriptionStatus.NEEDS_REVIEW,
            )
            if entity_type == ImportEntity.EntityType.BOOK:
                Book.objects.create(book_id=next_model_id(Book, "book_id", "book"), work=work, page_count=entity.data_json.get("extent", ""))
            else:
                issue_entity = parent_entity(entity, "issue_has_article")
                journal_issue_id = entity_map.get(issue_entity.id) if issue_entity else ""
                collection_entity = parent_entity(entity, "article_in_collection")
                collection_id = entity_map.get(collection_entity.id) if collection_entity else ""
                Article.objects.create(
                    article_id=next_model_id(Article, "article_id", "article"),
                    work=work,
                    collection_id=collection_id or None,
                    journal_issue_id=journal_issue_id or None,
                    pages=entity.data_json.get("pages", ""),
                    pages_raw=entity.data_json.get("pages", ""),
                )
            entity_map[entity.id] = work.work_id
            entity.created_entity_type = "work"
            entity.created_entity_id = work.work_id
            entity.status = ImportEntity.Status.APPLIED
            entity.save()
            created.append({"type": entity_type, "id": work.work_id, "label": work.title, "source_name": import_batch.source_name})
            attach_authors_to_work(import_batch, entity, work, entity_map, relations)

    import_batch.status = ImportBatch.Status.APPLIED
    import_batch.applied_at = timezone.now()
    import_batch.save(update_fields=["status", "applied_at", "updated_at"])
    ImportApplyLog.objects.create(
        import_batch=import_batch,
        applied_by=user,
        summary_json={
            "created": len(created),
            "updated": len([entry for entry in updated if entry.get("status") == "updated"]),
            "update_noop": len([entry for entry in updated if entry.get("status") == "no_op"]),
            "relations": len(relations),
            "backup_path": str(backup_path) if backup_path else "",
            "source_name": import_batch.source_name,
            "source_type": import_batch.source_type,
        },
        created_entities_json=created,
        updated_entities_json=updated,
        created_relations_json=relations,
        decisions_json=serialized_import_decisions(import_batch),
        raw_input=import_batch.raw_input,
    )
    return {"applied": True, "created": created, "updated": updated, "relations": relations, "backup_path": str(backup_path) if backup_path else ""}


def parent_entity(entity, relation_type):
    relation = ImportEntityRelation.objects.filter(child_entity=entity, relation_type=relation_type).select_related("parent_entity").first()
    return relation.parent_entity if relation else None


def parent_entities(entity, relation_type):
    return [
        relation.parent_entity
        for relation in ImportEntityRelation.objects.filter(child_entity=entity, relation_type=relation_type).select_related("parent_entity")
    ]


def attach_authors_to_work(import_batch, entity, work, entity_map, relations):
    author_relations = ImportEntityRelation.objects.filter(import_batch=import_batch, child_entity=entity, relation_type="author_of").select_related("parent_entity")
    for index, relation in enumerate(author_relations, start=1):
        author_id = entity_map.get(relation.parent_entity_id) or relation.parent_entity.matched_existing_id
        if not author_id:
            continue
        WorkAuthor.objects.get_or_create(work=work, author_id=author_id, defaults={"sort_order": index, "role": "author", "name_as_printed": relation.parent_entity.label})
        relations.append({"type": "author_of", "work_id": work.work_id, "author_id": author_id})


def next_model_id(model, field_name, prefix):
    last = 0
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    for value in model.objects.values_list(field_name, flat=True):
        match = pattern.match(str(value))
        if match:
            last = max(last, int(match.group(1)))
    return f"{prefix}-{last + 1:06d}"


def next_source_number():
    value = Work.objects.aggregate(max_number=Max("source_number"))
    return max(900000000, int(value["max_number"] or 0)) + 1
