# Single-cell Colab workflow for mixed bibliography verification.
#
# Open the generated notebooks/book_colab.ipynb in Colab and run the only code
# cell. The cell installs dependencies if needed, reads GEMINI_API_KEY from
# Google Drive when available, uploads one input file, verifies records with
# Gemini Google Search grounding, and writes resumable checkpoints.

import csv
import getpass
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    import pandas as pd
    from google import genai
    from google.genai import types
    from google.colab import drive, files
except ModuleNotFoundError:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "google-genai", "pandas"], check=True)
    import pandas as pd
    from google import genai
    from google.genai import types
    from google.colab import drive, files


MODEL_ID = globals().get("MODEL_ID", "gemini-2.5-flash")
PROMPT_VERSION = "mixed-bibliography-verification-0.1-colab"
OUTPUT_DIR = Path("/content/bibliography_verification_results")
SLEEP_SECONDS = globals().get("SLEEP_SECONDS", 1.0)
MAX_ATTEMPTS = globals().get("MAX_ATTEMPTS", 3)
RETRY_SLEEP_SECONDS = globals().get("RETRY_SLEEP_SECONDS", 10.0)
CHECKPOINT_EVERY = globals().get("CHECKPOINT_EVERY", 20)
SAVE_AFTER_EVERY_RECORD = globals().get("SAVE_AFTER_EVERY_RECORD", True)
USE_GOOGLE_DRIVE_CHECKPOINT = globals().get("USE_GOOGLE_DRIVE_CHECKPOINT", True)
DRIVE_CHECKPOINT_ROOT = Path(globals().get("DRIVE_CHECKPOINT_ROOT", "/content/drive/MyDrive/bibliobon_colab_checkpoints"))
DRIVE_API_KEY_PATH = Path(globals().get("DRIVE_API_KEY_PATH", "/content/drive/MyDrive/bibliobon_colab_secrets/gemini_api_key.env"))


SOURCE_FIELDS = [
    "raw_author_string",
    "title",
    "title_remainder",
    "responsibility_statement",
    "publication_place",
    "publisher",
    "publication_date",
    "inferred_year",
    "extent",
    "isbn",
    "issn",
    "doi",
    "url",
    "pages",
    "citation_gost_2018_full",
    "citation_gost_2003_short",
    "warnings",
    "confidence",
]

HOST_FIELDS = [
    "host_type",
    "raw_author_string",
    "title",
    "title_remainder",
    "journal_title",
    "issue_year",
    "volume_number",
    "issue_number",
    "publication_place",
    "publisher",
    "publication_date",
    "inferred_year",
    "extent",
    "isbn",
    "issn",
    "url",
    "citation_gost_2018_full",
    "citation_gost_2003_short",
    "warnings",
    "confidence",
]

NULLABLE_SOURCE_FIELDS = {
    "raw_author_string",
    "title_remainder",
    "responsibility_statement",
    "publication_place",
    "publisher",
    "publication_date",
    "inferred_year",
    "extent",
    "isbn",
    "issn",
    "doi",
    "url",
    "pages",
}

NULLABLE_HOST_FIELDS = {
    "host_type",
    "raw_author_string",
    "title",
    "title_remainder",
    "journal_title",
    "issue_year",
    "volume_number",
    "issue_number",
    "publication_place",
    "publisher",
    "publication_date",
    "inferred_year",
    "extent",
    "isbn",
    "issn",
    "url",
}


SYSTEM_PROMPT = """
Ты — профессиональный эксперт-библиограф. Твоя задача — верифицировать одну сырую библиографическую строку через встроенный поиск Google и вернуть структурированные данные для импорта в Bibliobon.

На вход ты получаешь ОДНУ сырую строку. В ней может быть:
- монография;
- отдельный том многотомного издания;
- "склеенная" строка многотомника с несколькими томами;
- статья в журнале;
- статья в сборнике;
- глава в книге;
- материал конференции;
- газетная или другая периодическая публикация;
- электронный ресурс;
- неполная или дефектная запись.

Сначала классифицируй тип записи. Затем верни JSON-массив объектов. Каждый объект массива — одна импортируемая библиографическая единица. Даже если вход описывает одну книгу или одну статью, корневой JSON всё равно должен быть массивом из одного объекта.

Допустимые значения record_type:
- "monograph";
- "multivolume_part";
- "journal_article";
- "collection_article";
- "book_chapter";
- "conference_article";
- "newspaper_article";
- "electronic_resource";
- "unknown".

Общие правила:
1. Используй Google Search для проверки данных. Приоритетные источники: каталоги библиотек и национальных библиотек, РГБ, НЭБ, WorldCat, Google Книги, сайты издательств, сайты журналов, eLIBRARY, CyberLeninka, DOI/Crossref, каталоги конференций.
2. Не выдумывай неподтверждённые данные. Если год, издательство, страницы, ISBN/ISSN/DOI или общий объём не подтверждены, ставь null и добавляй предупреждение.
3. В названиях и подзаголовках исправляй очевидные опечатки, но не меняй авторскую или подтверждённую источником орфографию.
4. В сведениях, относящихся к заглавию, первую букву обычно пиши со строчной, если первое слово не является именем собственным, географическим названием или началом цитируемого официального названия.
5. В хронологических диапазонах и диапазонах внутри названий/подзаголовков используй короткое тире без пробелов: "1843–1934 гг.".
6. Не добавляй текст вне JSON.

Правила для книг:
1. Для монографии заполняй объект source. Поле host должно быть null.
2. Для книг с одним, двумя или тремя авторами:
   - source.raw_author_string: первый автор в форме "Фамилия И. О.";
   - citation_gost_2018_full начинается с первого автора;
   - после первой косой черты перечисляй всех авторов в прямой форме.
3. Для книг с четырьмя авторами:
   - source.raw_author_string = null;
   - полная запись начинается с заглавия;
   - после косой черты перечисляй всех четырёх авторов.
4. Для книг с пятью и более авторами:
   - source.raw_author_string = null;
   - полная запись начинается с заглавия;
   - после косой черты пиши первых трёх авторов и "[и др.]".
5. Если точный общий объём страниц не подтверждён, source.extent = null и в ГОСТ-строке используй маркер "[объем не установлен]".

Правила для многотомников:
1. Если входная строка описывает несколько томов с разными годами, издательствами или объёмами, запрещено собирать их в одну общую ГОСТ-строку.
2. Верни несколько объектов record_type="multivolume_part", по одному на каждый подтверждённый том.
3. В source.title сохраняй общее название серии/издания.
4. В source.title_remainder выноси конкретный том, например: "в 3 томах. Т. 1 : Центральная Африка".

Правила для статей и глав:
1. Для статьи/главы объект source описывает саму статью или главу.
2. Объект host описывает источник публикации: выпуск журнала, сборник, книгу, материалы конференции, газету или сайт.
3. Не смешивай страницы статьи и объём источника:
   - source.pages: диапазон страниц статьи, например "С. 15–27";
   - host.extent: общий объём сборника/книги/выпуска, например "320 с.", только если подтверждён.
4. Если точные страницы статьи не найдены, source.pages = null и добавь предупреждение.
5. Для журнальных статей:
   - host.host_type = "journal_issue";
   - host.journal_title: название журнала;
   - host.issue_year, host.volume_number, host.issue_number: год, том, номер/выпуск.
6. Для статей в сборниках и глав:
   - host.host_type = "collection" или "monograph";
   - host.title: название сборника/книги;
   - host.publication_place, host.publisher, host.publication_date, host.extent заполняй только если подтверждены.

Схема каждого объекта в корневом массиве:
{
  "record_type": "monograph | multivolume_part | journal_article | collection_article | book_chapter | conference_article | newspaper_article | electronic_resource | unknown",
  "source": {
    "raw_author_string": "автор/первый автор в форме 'Фамилия И. О.' или null",
    "title": "основное заглавие источника/статьи без внешних кавычек",
    "title_remainder": "подзаголовок или сведения, относящиеся к заглавию, или null",
    "responsibility_statement": "сведения об ответственности или null",
    "publication_place": "место издания или null",
    "publisher": "издательство или null",
    "publication_date": "год или дата публикации как строка или null",
    "inferred_year": "четырёхзначный год как строка, если publication_date ровно год, иначе null",
    "extent": "общий объём для книг/томов, например '240 с.', или null",
    "isbn": "ISBN или null",
    "issn": "ISSN или null",
    "doi": "DOI или null",
    "url": "URL или null",
    "pages": "страницы статьи/главы, например 'С. 15–27', или null",
    "citation_gost_2018_full": "полная запись источника/статьи по ГОСТ Р 7.0.100-2018",
    "citation_gost_2003_short": "краткая запись источника/статьи по ГОСТ",
    "warnings": [],
    "confidence": 0.0
  },
  "host": {
    "host_type": "journal_issue | collection | monograph | conference_proceedings | newspaper_issue | website | unknown",
    "raw_author_string": "авторы/редакторы источника или null",
    "title": "название источника публикации",
    "title_remainder": "подзаголовок или сведения об источнике, или null",
    "journal_title": "название журнала, если применимо, иначе null",
    "issue_year": "год выпуска, если применимо, иначе null",
    "volume_number": "том, если применимо, иначе null",
    "issue_number": "номер/выпуск, если применимо, иначе null",
    "publication_place": "место издания или null",
    "publisher": "издательство или null",
    "publication_date": "год или дата источника как строка или null",
    "inferred_year": "четырёхзначный год как строка, если publication_date ровно год, иначе null",
    "extent": "общий объём источника, если подтверждён, иначе null",
    "isbn": "ISBN источника или null",
    "issn": "ISSN источника или null",
    "url": "URL источника или null",
    "citation_gost_2018_full": "полная запись источника публикации как самостоятельного источника",
    "citation_gost_2003_short": "краткая запись источника публикации",
    "warnings": [],
    "confidence": 0.0
  },
  "keywords": ["3-5 ключевых слов"],
  "warnings": ["общие предупреждения"],
  "confidence": 0.0
}

Для монографий и отдельных томов host должен быть null. Для статей, глав и материалов конференций host должен быть объектом. Если тип записи неизвестен, всё равно верни объект со значением record_type="unknown", максимально заполненными подтверждёнными полями и предупреждениями.
""".strip()


def read_gemini_api_key_from_file(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("GEMINI_API_KEY="):
            return line.split("=", 1)[1].strip().strip("'\"")
    return text.strip().strip("'\"")


def ensure_drive_mounted() -> bool:
    try:
        drive.mount("/content/drive", force_remount=False)
        print("Google Drive mounted.")
        return True
    except Exception as exc:
        print(f"Google Drive mount failed: {exc}")
        return False


if "client" not in globals():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key and ensure_drive_mounted():
        print(f"Looking for GEMINI_API_KEY at {DRIVE_API_KEY_PATH}")
        api_key = read_gemini_api_key_from_file(DRIVE_API_KEY_PATH)
        if api_key:
            print(f"GEMINI_API_KEY loaded from {DRIVE_API_KEY_PATH}")
        else:
            print("GEMINI_API_KEY file was not found or was empty.")
    if not api_key:
        api_key = getpass.getpass("GEMINI_API_KEY: ").strip()
    os.environ["GEMINI_API_KEY"] = api_key
    client = genai.Client(api_key=api_key)
    print("Gemini client configured")


def now_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def load_txt_records(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        raw_text = clean_text(line)
        if raw_text:
            records.append({"raw_input": raw_text, "source_number": line_no})
    return records


def load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        raw_text = row.get("raw_input") or row.get("raw_text") or row.get("text") or row.get("record") or row.get("book")
        if isinstance(raw_text, str) and raw_text.strip():
            records.append({"raw_input": clean_text(raw_text), "source_number": row.get("source_number") or line_no})
    return records


def load_csv_records(path: Path) -> list[dict[str, Any]]:
    df = pd.read_csv(path)
    raw_column = next((name for name in ["raw_input", "raw_text", "text", "record", "book", "title"] if name in df.columns), None)
    if raw_column is None:
        raise ValueError("CSV input must contain one of: raw_input, raw_text, text, record, book, title")
    records = []
    for index, row in df.iterrows():
        raw_text = row.get(raw_column)
        if isinstance(raw_text, str) and raw_text.strip():
            records.append(
                {
                    "raw_input": clean_text(raw_text),
                    "source_number": row.get("source_number") if "source_number" in row else int(index) + 2,
                }
            )
    return records


def load_records(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return load_txt_records(path)
    if suffix == ".jsonl":
        return load_jsonl_records(path)
    if suffix == ".csv":
        return load_csv_records(path)
    raise ValueError("Input must be .txt, .csv, or .jsonl")


def build_config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.1,
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )


def parse_json_response(text: str) -> list[dict[str, Any]]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    if not stripped.startswith(("[", "{")):
        array_match = re.search(r"\[.*\]", stripped, flags=re.DOTALL)
        object_match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if array_match:
            stripped = array_match.group(0)
        elif object_match:
            stripped = object_match.group(0)
    data = json.loads(stripped)
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list) and all(isinstance(item, dict) for item in data):
        return data
    raise ValueError("Gemini response JSON is not an object or an array of objects")


def normalize_scalar(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return "; ".join(str(item).strip() for item in value if str(item).strip())
    return clean_text(str(value))


def normalize_warnings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def normalize_confidence(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_source(source: Any) -> dict[str, Any]:
    source = source if isinstance(source, dict) else {}
    normalized = {}
    for field in SOURCE_FIELDS:
        if field == "warnings":
            normalized[field] = normalize_warnings(source.get(field))
        elif field == "confidence":
            normalized[field] = normalize_confidence(source.get(field))
        else:
            normalized[field] = normalize_scalar(source.get(field))
    if not normalized.get("inferred_year") and re.fullmatch(r"\d{4}", normalized.get("publication_date") or ""):
        normalized["inferred_year"] = normalized["publication_date"]
    return normalized


def normalize_host(host: Any) -> dict[str, Any] | None:
    if host is None:
        return None
    host = host if isinstance(host, dict) else {}
    normalized = {}
    for field in HOST_FIELDS:
        if field == "warnings":
            normalized[field] = normalize_warnings(host.get(field))
        elif field == "confidence":
            normalized[field] = normalize_confidence(host.get(field))
        else:
            normalized[field] = normalize_scalar(host.get(field))
    if not normalized.get("inferred_year") and re.fullmatch(r"\d{4}", normalized.get("publication_date") or ""):
        normalized["inferred_year"] = normalized["publication_date"]
    return normalized


def normalize_record(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_type": normalize_scalar(item.get("record_type")) or "unknown",
        "source": normalize_source(item.get("source")),
        "host": normalize_host(item.get("host")),
        "keywords": normalize_warnings(item.get("keywords")),
        "warnings": normalize_warnings(item.get("warnings")),
        "confidence": normalize_confidence(item.get("confidence")),
    }


def normalize_records(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_record(item) for item in items]


def serialize_obj(obj: Any) -> Any:
    for method_name in ["to_json_dict", "model_dump", "dict"]:
        method = getattr(obj, method_name, None)
        if callable(method):
            try:
                return method()
            except TypeError:
                continue
    try:
        return json.loads(obj.model_dump_json())
    except Exception:
        return repr(obj)


def extract_grounding_chunks(response: Any) -> list[dict[str, Any]]:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return []
    metadata = getattr(candidates[0], "grounding_metadata", None)
    chunks = getattr(metadata, "grounding_chunks", None) if metadata else None
    if not chunks:
        return []
    extracted = []
    for index, chunk in enumerate(chunks, start=1):
        raw = serialize_obj(chunk)
        web = raw.get("web") if isinstance(raw, dict) else {}
        extracted.append(
            {
                "chunk_index": index,
                "uri": web.get("uri", "") if isinstance(web, dict) else "",
                "title": web.get("title", "") if isinstance(web, dict) else "",
                "raw": raw,
            }
        )
    return extracted


def verify_with_gemini_once(raw_input: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    response = client.models.generate_content(
        model=MODEL_ID,
        contents=json.dumps(
            {
                "task": "verify_mixed_bibliography_record",
                "raw_input": raw_input,
                "language": "ru",
                "target_standard": "ГОСТ Р 7.0.100-2018",
            },
            ensure_ascii=False,
        ),
        config=build_config(),
    )
    text = response.text or ""
    parsed = parse_json_response(text)
    grounding = extract_grounding_chunks(response)
    return normalize_records(parsed), grounding, text


def verify_with_retries(raw_input: str, max_attempts: int = MAX_ATTEMPTS) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, int, list[str]]:
    errors = []
    for attempt in range(1, max_attempts + 1):
        try:
            result, grounding, raw_text = verify_with_gemini_once(raw_input)
            return result, grounding, raw_text, attempt, errors
        except Exception as exc:
            errors.append(f"attempt {attempt}: {exc}")
            if attempt < max_attempts:
                print(f"  attempt {attempt} failed; retrying in {RETRY_SLEEP_SECONDS:g}s")
                time.sleep(RETRY_SLEEP_SECONDS)
    raise RuntimeError("; ".join(errors))


def is_quota_error(error_text: str) -> bool:
    markers = ["quota", "exceeded your current quota", "resource_exhausted", "rate limit", "rate_limit", "429"]
    lower_text = error_text.lower()
    return any(marker in lower_text for marker in markers)


print("Загрузите один входной файл: .txt, .csv или .jsonl")
uploaded = files.upload()
if len(uploaded) != 1:
    raise ValueError("Загрузите ровно один файл")
input_name = next(iter(uploaded.keys()))
input_path = Path("/content") / input_name
records = load_records(input_path)
print(f"input_path={input_path}")
print(f"records={len(records)}")
if records:
    print("first_record=", records[0]["raw_input"][:300])


def value_for_tsv(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def tsv_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in result_items(record):
        source = item.get("source") or {}
        host = item.get("host") or {}
        rows.append(
            {
                "work_id": "",
                "source_number": value_for_tsv(record.get("source_number")),
                "row_type": "import",
                "editor_note": "",
                "raw_input": value_for_tsv(record.get("raw_input")),
                "status": value_for_tsv(record.get("status")),
                "error": value_for_tsv(record.get("error")),
                "attempt_count": value_for_tsv(record.get("attempt_count")),
                "attempt_errors": value_for_tsv(record.get("attempt_errors", [])),
                "record_type": value_for_tsv(item.get("record_type")),
                "author": value_for_tsv(source.get("raw_author_string")),
                "title": value_for_tsv(source.get("title")),
                "title_remainder": value_for_tsv(source.get("title_remainder")),
                "responsibility_statement": value_for_tsv(source.get("responsibility_statement")),
                "publication_place": value_for_tsv(source.get("publication_place")),
                "publisher": value_for_tsv(source.get("publisher")),
                "publication_date": value_for_tsv(source.get("publication_date")),
                "inferred_year": value_for_tsv(source.get("inferred_year")),
                "extent": value_for_tsv(source.get("extent")),
                "isbn": value_for_tsv(source.get("isbn")),
                "issn": value_for_tsv(source.get("issn")),
                "doi": value_for_tsv(source.get("doi")),
                "url": value_for_tsv(source.get("url")),
                "article_pages": value_for_tsv(source.get("pages")),
                "citation_gost_2018_full": value_for_tsv(source.get("citation_gost_2018_full")),
                "citation_gost_2003_short": value_for_tsv(source.get("citation_gost_2003_short")),
                "host_type": value_for_tsv(host.get("host_type")),
                "host_author": value_for_tsv(host.get("raw_author_string")),
                "host_title": value_for_tsv(host.get("title")),
                "host_title_remainder": value_for_tsv(host.get("title_remainder")),
                "host_journal_title": value_for_tsv(host.get("journal_title")),
                "host_issue_year": value_for_tsv(host.get("issue_year")),
                "host_volume_number": value_for_tsv(host.get("volume_number")),
                "host_issue_number": value_for_tsv(host.get("issue_number")),
                "host_publication_place": value_for_tsv(host.get("publication_place")),
                "host_publisher": value_for_tsv(host.get("publisher")),
                "host_publication_date": value_for_tsv(host.get("publication_date")),
                "host_inferred_year": value_for_tsv(host.get("inferred_year")),
                "host_extent": value_for_tsv(host.get("extent")),
                "host_isbn": value_for_tsv(host.get("isbn")),
                "host_issn": value_for_tsv(host.get("issn")),
                "host_url": value_for_tsv(host.get("url")),
                "host_citation_gost_2018_full": value_for_tsv(host.get("citation_gost_2018_full")),
                "host_citation_gost_2003_short": value_for_tsv(host.get("citation_gost_2003_short")),
                "keywords": value_for_tsv(item.get("keywords", [])),
                "warnings": value_for_tsv(item.get("warnings", [])) or value_for_tsv(source.get("warnings", [])),
                "host_warnings": value_for_tsv(host.get("warnings", [])),
                "confidence": value_for_tsv(item.get("confidence")),
                "source_confidence": value_for_tsv(source.get("confidence")),
                "host_confidence": value_for_tsv(host.get("confidence")),
            }
        )
    return rows


def result_items(record: dict[str, Any]) -> list[dict[str, Any]]:
    result = record.get("result") or []
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)] or [{}]
    if isinstance(result, dict):
        return [result]
    return [{}]


def volume_context(record: dict[str, Any]) -> str:
    markers = []
    for item in result_items(record):
        source = item.get("source") or {}
        title = source.get("title") or ""
        title_remainder = source.get("title_remainder") or ""
        year = source.get("publication_date") or source.get("inferred_year") or ""
        marker = " ".join(part for part in [value_for_tsv(title), value_for_tsv(title_remainder), f"({year})" if year else ""] if part).strip()
        if marker:
            markers.append(marker)
    return "; ".join(markers) if markers else "Одиночная запись"


def write_outputs(output_dir: Path, output_records: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "verified_bibliography.jsonl").open("w", encoding="utf-8") as fh:
        for record in output_records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    tsv_data = [row for record in output_records for row in tsv_rows(record)]
    fieldnames = list(tsv_data[0].keys()) if tsv_data else ["work_id", "source_number", "row_type", "editor_note"]
    with (output_dir / "verified_bibliography.tsv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(tsv_data)

    grounding_rows = []
    for record in output_records:
        for chunk in record.get("grounding_chunks", []):
            grounding_rows.append(
                {
                    "source_number": record.get("source_number", ""),
                    "result_context": volume_context(record),
                    "chunk_index": chunk.get("chunk_index"),
                    "title": chunk.get("title", ""),
                    "uri": chunk.get("uri", ""),
                    "raw": json.dumps(chunk.get("raw", {}), ensure_ascii=False, sort_keys=True),
                }
            )
    with (output_dir / "grounding_sources.tsv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["source_number", "result_context", "chunk_index", "title", "uri", "raw"], delimiter="\t")
        writer.writeheader()
        writer.writerows(grounding_rows)

    (output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_manifest(run_id: str, started_at: str, input_path: Path, output_dir: Path, output_records: list[dict[str, Any]], checkpoint_dir: Path | None, run_status: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": now_stamp(),
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "checkpoint_dir": str(checkpoint_dir) if checkpoint_dir else "",
        "run_status": run_status,
        "prompt_version": PROMPT_VERSION,
        "model": MODEL_ID,
        "record_count": len(output_records),
        "ok_count": sum(1 for record in output_records if record["status"] == "ok"),
        "error_count": sum(1 for record in output_records if record["status"] == "error"),
        "skipped_count": sum(1 for record in output_records if record["status"] == "skipped"),
        "sleep_seconds": SLEEP_SECONDS,
        "max_attempts": MAX_ATTEMPTS,
        "retry_sleep_seconds": RETRY_SLEEP_SECONDS,
        "checkpoint_every": CHECKPOINT_EVERY,
        "save_after_every_record": SAVE_AFTER_EVERY_RECORD,
    }


def zip_outputs(output_dir: Path, zip_base_path: Path) -> Path:
    if zip_base_path.with_suffix(".zip").exists():
        zip_base_path.with_suffix(".zip").unlink()
    return Path(shutil.make_archive(str(zip_base_path), "zip", output_dir))


def save_checkpoint(output_records: list[dict[str, Any]], manifest: dict[str, Any], checkpoint_dir: Path | None) -> None:
    write_outputs(OUTPUT_DIR, output_records, manifest)
    zip_outputs(OUTPUT_DIR, OUTPUT_DIR.parent / "bibliography_verification_results")
    if checkpoint_dir:
        write_outputs(checkpoint_dir, output_records, manifest)
        zip_outputs(checkpoint_dir, checkpoint_dir / "bibliography_verification_results")


def mount_drive_for_checkpoints() -> Path | None:
    if not USE_GOOGLE_DRIVE_CHECKPOINT:
        return None
    if ensure_drive_mounted():
        DRIVE_CHECKPOINT_ROOT.mkdir(parents=True, exist_ok=True)
        return DRIVE_CHECKPOINT_ROOT
    print("Drive checkpoint disabled.")
    return None


def load_checkpoint_records(checkpoint_dir: Path | None) -> list[dict[str, Any]]:
    if not checkpoint_dir:
        return []
    path = checkpoint_dir / "verified_bibliography.jsonl"
    if not path.exists():
        legacy_path = checkpoint_dir / "verified_books.jsonl"
        path = legacy_path if legacy_path.exists() else path
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def checkpoint_should_save(processed_count: int) -> bool:
    if SAVE_AFTER_EVERY_RECORD:
        return True
    return CHECKPOINT_EVERY > 0 and processed_count % CHECKPOINT_EVERY == 0


RUN_ID = Path(input_name).stem + "-" + now_stamp()
if OUTPUT_DIR.exists():
    shutil.rmtree(OUTPUT_DIR)

checkpoint_root = mount_drive_for_checkpoints()
checkpoint_dir = checkpoint_root / Path(input_name).stem if checkpoint_root else None
if checkpoint_dir:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    print(f"checkpoint_dir={checkpoint_dir}")

checkpoint_records = load_checkpoint_records(checkpoint_dir)
done_records = {
    str(record.get("source_number")): record
    for record in checkpoint_records
    if record.get("status") == "ok" and record.get("source_number") is not None
}
if done_records:
    print(f"resume: loaded {len(done_records)} completed records from checkpoint")

output_records = list(done_records.values())
started_at = now_stamp()
stopped_early = False

for index, source in enumerate(records, start=1):
    source_number_key = str(source.get("source_number"))
    if source_number_key in done_records:
        print(f"{index}/{len(records)} source_number={source.get('source_number')} already done; skipping")
        continue

    record = {
        "source_number": source.get("source_number"),
        "raw_input": source["raw_input"],
        "prompt_version": PROMPT_VERSION,
        "model": MODEL_ID,
        "status": "ok",
        "attempt_count": 0,
        "attempt_errors": [],
        "result": [],
        "grounding_chunks": [],
        "raw_model_text": "",
    }
    try:
        result, grounding_chunks, raw_model_text, attempt_count, attempt_errors = verify_with_retries(source["raw_input"])
        record["attempt_count"] = attempt_count
        record["attempt_errors"] = attempt_errors
        record["result"] = result
        record["grounding_chunks"] = grounding_chunks
        record["raw_model_text"] = raw_model_text
    except Exception as exc:
        record["status"] = "error"
        record["attempt_count"] = MAX_ATTEMPTS
        record["error"] = str(exc)
        record["attempt_errors"] = str(exc).split("; ")
    output_records.append(record)
    print(f"{index}/{len(records)} source_number={record.get('source_number')} {record['status']} attempts={record['attempt_count']}")
    if record["status"] == "error":
        print("  error=", record.get("error", "")[:1000])
        if is_quota_error(record.get("error", "")):
            print("  quota error detected; stopping and saving current results")
            for skipped_source in records[index:]:
                output_records.append(
                    {
                        "source_number": skipped_source.get("source_number"),
                        "raw_input": skipped_source["raw_input"],
                        "prompt_version": PROMPT_VERSION,
                        "model": MODEL_ID,
                        "status": "skipped",
                        "attempt_count": 0,
                        "attempt_errors": [],
                        "error": "Skipped after quota/rate-limit error on a previous record.",
                        "result": [],
                        "grounding_chunks": [],
                        "raw_model_text": "",
                    }
                )
            stopped_early = True
            break
    current_manifest = build_manifest(RUN_ID, started_at, input_path, OUTPUT_DIR, output_records, checkpoint_dir, "running")
    if checkpoint_should_save(len(output_records)):
        save_checkpoint(output_records, current_manifest, checkpoint_dir)
        print(f"  checkpoint saved: records={len(output_records)}")
    if index < len(records) and SLEEP_SECONDS:
        time.sleep(SLEEP_SECONDS)

manifest = build_manifest(
    RUN_ID,
    started_at,
    input_path,
    OUTPUT_DIR,
    output_records,
    checkpoint_dir,
    "stopped" if stopped_early else "complete",
)
save_checkpoint(output_records, manifest, checkpoint_dir)
print(f"output_dir={OUTPUT_DIR}")
if checkpoint_dir:
    print(f"checkpoint_dir={checkpoint_dir}")
print(json.dumps(manifest, ensure_ascii=False, indent=2))

zip_path = zip_outputs(OUTPUT_DIR, OUTPUT_DIR.parent / "bibliography_verification_results")
print(zip_path)
files.download(zip_path)
