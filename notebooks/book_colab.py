# %% [markdown]
# # Book Bibliography Verification with Gemini Google Search
#
# Colab-блокнот для проверки и структурирования библиографических записей обычных книг через Gemini API с Google Search grounding.
#
# Входной файл: один `.txt`, `.csv` или `.jsonl` файл. Для `.txt`: одна запись книги на одной строке.
#
# Выходные файлы:
#
# - `verified_books.jsonl`
# - `verified_books.tsv`
# - `grounding_sources.tsv`
# - `run_manifest.json`
# - `book_verification_results.zip`

# %%
import subprocess
import sys
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "google-genai", "pandas"], check=True)

import csv
import getpass
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import pandas as pd
from google import genai
from google.genai import types
from google.colab import files

MODEL_ID = "gemini-2.5-flash"
PROMPT_VERSION = "book-verification-0.3-colab"
OUTPUT_DIR = Path("/content/book_verification_results")
SLEEP_SECONDS = 1.0
MAX_ATTEMPTS = 3
RETRY_SLEEP_SECONDS = 10.0

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    api_key = getpass.getpass("GEMINI_API_KEY: ").strip()
os.environ["GEMINI_API_KEY"] = api_key
client = genai.Client(api_key=api_key)
print("Gemini client configured")

# %%
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
    from google.colab import files
except ModuleNotFoundError:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "google-genai", "pandas"], check=True)
    import pandas as pd
    from google import genai
    from google.genai import types
    from google.colab import files

MODEL_ID = globals().get("MODEL_ID", "gemini-2.5-flash")
PROMPT_VERSION = "book-verification-0.4-colab"
OUTPUT_DIR = Path("/content/book_verification_results")
SLEEP_SECONDS = globals().get("SLEEP_SECONDS", 1.0)
MAX_ATTEMPTS = globals().get("MAX_ATTEMPTS", 3)
RETRY_SLEEP_SECONDS = globals().get("RETRY_SLEEP_SECONDS", 10.0)

if "client" not in globals():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        api_key = getpass.getpass("GEMINI_API_KEY: ").strip()
    os.environ["GEMINI_API_KEY"] = api_key
    client = genai.Client(api_key=api_key)
    print("Gemini client configured")

EXPECTED_FIELDS = [
    "raw_author_string",
    "book_title",
    "book_subtitle",
    "responsibility_statement",
    "publication_city",
    "publisher",
    "publication_year",
    "total_pages",
    "isbn",
    "full_book_gost",
    "short_book_gost",
    "keywords",
    "warnings",
    "confidence",
]

NULLABLE_FIELDS = {
    "raw_author_string",
    "book_subtitle",
    "responsibility_statement",
    "publication_city",
    "publisher",
    "publication_year",
    "total_pages",
    "isbn",
}

SYSTEM_PROMPT = """
Ты — профессиональный эксперт-библиограф. Твоя задача — верифицировать произвольную книгу через встроенный поиск Google и составить для неё структурированное библиографическое описание.

На вход ты получаешь ОДНУ сырую строку с черновыми, возможно неполными или дефектными, данными книги.

Твои обязательные действия:
1. Используя встроенный поиск Google, найди официальные издательские данные книги. Приоритетные источники: каталоги библиотек и национальных библиотек, РГБ, НЭБ, WorldCat, Google Книги, сайты издательств, крупные книжные порталы. Не используй неподтвержденные данные, если они не встречаются в найденных источниках.
2. Проверь и исправь опечатки в названии книги. Убери внешние кавычки. Сохрани авторскую орфографию только там, где она явно подтверждена источником.
3. Найди и проверь: автора или авторов, основное заглавие, подзаголовок, сведения об ответственности, город издания, издательство, год, общий объем страниц, ISBN.
4. Особые правила для книг с несколькими авторами по ГОСТ Р 7.0.100-2018:
   А. Если у книги один, два или три автора:
      - В поле "raw_author_string" пиши первого автора в форме заголовка: "Фамилия И. О.".
      - В поле "full_book_gost" начинай запись с первого автора. После первой косой черты (/) перечисляй всех авторов в прямой форме: "И. О. Фамилия".
      - В поле "short_book_gost" в начале перечисляй всех авторов в форме "Фамилия, И. О." через запятую.
   Б. Если у книги четыре автора:
      - Поле "raw_author_string" оставь null.
      - "full_book_gost" начинай с названия. После первой косой черты (/) перечисляй всех четырех авторов в прямой форме.
      - "short_book_gost" начинай с названия, без авторов в начале.
   В. Если у книги пять и более авторов:
      - Поле "raw_author_string" оставь null.
      - "full_book_gost" начинай с названия. После первой косой черты (/) пиши первых трех авторов в прямой форме, затем "[и др.]".
      - "short_book_gost" начинай с названия, без авторов в начале.
5. Не выдумывай физический объем. Если найденные источники не подтверждают точное число страниц, в поле "total_pages" поставь null и не подставляй страницы в ГОСТ-строки; используй маркер "[объем не установлен]".
6. Не выдумывай издательство, год, ISBN или сведения об ответственности. Если данные не подтверждены, ставь null в соответствующее поле и добавляй предупреждение в "warnings".
7. Особые правила для многотомных изданий.
   Если на вход поступает строка, описывающая многотомное издание, где для разных томов указаны разные годы издания, разные издательства или разный объем страниц, тебе запрещено собирать их в одну общую строку ГОСТа.
   Вместо этого:
   - расщепи сырую запись на несколько независимых объектов, по одному на каждый упомянутый том;
   - для каждого тома через поиск Google найди или подтверди его индивидуальные характеристики: год, подзаголовок/номер тома, страницы;
   - в поле "book_title" всегда сохраняй общее название серии;
   - в поле "book_subtitle" выноси конкретное обозначение тома, например "в 3 томах. Т. 1 : Центральная Африка".
8. Сформируй ответ строго в формате JSON без текста до или после JSON. Корневой элемент ответа должен быть массивом объектов, даже если обрабатывается всего одна книга. Каждый объект в массиве представляет отдельную книгу или отдельный том многотомного издания.

Схема каждого объекта в массиве:
{
  "raw_author_string": "Первый автор в форме 'Фамилия И. О.' или null",
  "book_title": "Основное заглавие книги без внешних кавычек; для многотомников здесь сохраняется общее название серии",
  "book_subtitle": "Подзаголовок или сведения, относящиеся к заглавию; для многотомников номер и название конкретного тома; если нет, null",
  "responsibility_statement": "Сведения об ответственности в прямой форме; если нет, null",
  "publication_city": "Полное название города издания; если не подтверждено, null",
  "publisher": "Название издательства без кавычек и без лишнего слова 'издательство'; если не подтверждено, null",
  "publication_year": "Год издания четырехзначным числом как строка; если не подтверждено, null",
  "total_pages": "Только число общего объема страниц как строка; если не подтверждено, null",
  "isbn": "ISBN в найденной форме; если нет, null",
  "full_book_gost": "Полная запись книги или конкретного тома по ГОСТ Р 7.0.100-2018 с 'Текст : непосредственный.'",
  "short_book_gost": "Краткая запись книги или конкретного тома по ГОСТ",
  "keywords": ["3-5 точных ключевых слов или словосочетаний"],
  "warnings": ["краткие предупреждения о неподтвержденных или сомнительных данных"],
  "confidence": число от 0 до 1
}

Если источники противоречат друг другу, выбери наиболее авторитетный источник, а конфликт кратко опиши в "warnings". Не добавляй текст вне JSON.
""".strip()


def now_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def load_txt_records(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        raw_text = clean_text(line)
        if not raw_text:
            continue
        records.append({
            "raw_input": raw_text,
            "source_number": line_no,
        })
    return records


def load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        raw_text = row.get("raw_input") or row.get("raw_text") or row.get("text") or row.get("record") or row.get("book")
        if not isinstance(raw_text, str) or not raw_text.strip():
            continue
        records.append({
            "raw_input": clean_text(raw_text),
            "source_number": row.get("source_number") or line_no,
        })
    return records


def load_csv_records(path: Path) -> list[dict[str, Any]]:
    df = pd.read_csv(path)
    raw_column = next((name for name in ["raw_input", "raw_text", "text", "record", "book", "title"] if name in df.columns), None)
    if raw_column is None:
        raise ValueError("CSV input must contain one of: raw_input, raw_text, text, record, book, title")
    records = []
    for index, row in df.iterrows():
        raw_text = row.get(raw_column)
        if not isinstance(raw_text, str) or not raw_text.strip():
            continue
        records.append({
            "raw_input": clean_text(raw_text),
            "source_number": row.get("source_number") if "source_number" in row else int(index) + 2,
        })
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


def parse_json_response(text: str) -> dict[str, Any] | list[dict[str, Any]]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    if not stripped.startswith(("{", "[")):
        array_match = re.search(r"\[.*\]", stripped, flags=re.DOTALL)
        object_match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if array_match:
            stripped = array_match.group(0)
        elif object_match:
            stripped = object_match.group(0)
    data = json.loads(stripped)
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and all(isinstance(item, dict) for item in data):
        return data
    raise ValueError("Gemini response JSON is not an object or an array of objects")
    return data


def normalize_result(row: dict[str, Any]) -> dict[str, Any]:
    normalized = {field: row.get(field, None if field in NULLABLE_FIELDS else "" if field not in {"keywords", "warnings"} else []) for field in EXPECTED_FIELDS}
    for field in EXPECTED_FIELDS:
        if field in {"keywords", "warnings"}:
            if not isinstance(normalized[field], list):
                normalized[field] = [str(normalized[field])]
            normalized[field] = [str(item).strip() for item in normalized[field] if str(item).strip()]
        elif field == "confidence":
            try:
                normalized[field] = float(normalized[field])
            except (TypeError, ValueError):
                normalized[field] = None
        elif normalized[field] is None:
            normalized[field] = None
        else:
            normalized[field] = clean_text(str(normalized[field]))
    return normalized


def normalize_results(data: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [normalize_result(row) for row in data]
    return [normalize_result(data)]


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
        extracted.append({
            "chunk_index": index,
            "uri": web.get("uri", "") if isinstance(web, dict) else "",
            "title": web.get("title", "") if isinstance(web, dict) else "",
            "raw": raw,
        })
    return extracted


def verify_with_gemini_once(raw_input: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    response = client.models.generate_content(
        model=MODEL_ID,
        contents=raw_input,
        config=build_config(),
    )
    text = response.text or ""
    parsed = parse_json_response(text)
    grounding = extract_grounding_chunks(response)
    return normalize_results(parsed), grounding, text


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


print("Загрузите один входной файл с книгами: .txt, .csv или .jsonl")
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


def inferred_year_from(publication_date: Any) -> str:
    text = value_for_tsv(publication_date)
    return text if re.fullmatch(r"\d{4}", text) else ""


def extent_from_pages(total_pages: Any) -> str:
    text = value_for_tsv(total_pages)
    if not text:
        return ""
    number_match = re.fullmatch(r"\d+", text)
    if number_match:
        return f"{text} с."
    return text


def result_items(record: dict[str, Any]) -> list[dict[str, Any]]:
    result = record.get("result") or []
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)] or [{}]
    if isinstance(result, dict):
        return [result]
    return [{}]


def tsv_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for result in result_items(record):
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
                "author": value_for_tsv(result.get("raw_author_string")),
                "title": value_for_tsv(result.get("book_title")),
                "title_remainder": value_for_tsv(result.get("book_subtitle")),
                "responsibility_statement": value_for_tsv(result.get("responsibility_statement")),
                "publication_place": value_for_tsv(result.get("publication_city")),
                "publisher": value_for_tsv(result.get("publisher")),
                "publication_date": value_for_tsv(result.get("publication_year")),
                "inferred_year": inferred_year_from(result.get("publication_year")),
                "extent": extent_from_pages(result.get("total_pages")),
                "isbn": value_for_tsv(result.get("isbn")),
                "citation_gost_2018_full": value_for_tsv(result.get("full_book_gost")),
                "citation_gost_2003_short": value_for_tsv(result.get("short_book_gost")),
                "keywords": value_for_tsv(result.get("keywords", [])),
                "warnings": value_for_tsv(result.get("warnings", [])),
                "confidence": value_for_tsv(result.get("confidence")),
            }
        )
    return rows


def volume_context(record: dict[str, Any]) -> str:
    markers = []
    for result in result_items(record):
        volume = result.get("book_subtitle") or ""
        year = result.get("publication_year") or ""
        marker = " ".join(part for part in [value_for_tsv(volume), f"({year})" if year else ""] if part).strip()
        if marker:
            markers.append(marker)
    return "; ".join(markers) if markers else "Одиночная книга"

def write_outputs(output_dir: Path, output_records: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "verified_books.jsonl").open("w", encoding="utf-8") as fh:
        for record in output_records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    tsv_data = [row for record in output_records for row in tsv_rows(record)]
    fieldnames = list(tsv_data[0].keys()) if tsv_data else ["work_id", "source_number", "row_type", "editor_note"]
    with (output_dir / "verified_books.tsv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(tsv_data)

    grounding_rows = []
    for record in output_records:
        for chunk in record.get("grounding_chunks", []):
            grounding_rows.append({
                "source_number": record.get("source_number", ""),
                "volume_context": volume_context(record),
                "chunk_index": chunk.get("chunk_index"),
                "title": chunk.get("title", ""),
                "uri": chunk.get("uri", ""),
                "raw": json.dumps(chunk.get("raw", {}), ensure_ascii=False, sort_keys=True),
            })
    with (output_dir / "grounding_sources.tsv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["source_number", "volume_context", "chunk_index", "title", "uri", "raw"], delimiter="\t")
        writer.writeheader()
        writer.writerows(grounding_rows)

    (output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

RUN_ID = Path(input_name).stem + "-" + now_stamp()
if OUTPUT_DIR.exists():
    shutil.rmtree(OUTPUT_DIR)

output_records = []
started_at = now_stamp()

for index, source in enumerate(records, start=1):
    record = {
        "source_number": source.get("source_number"),
        "raw_input": source["raw_input"],
        "prompt_version": PROMPT_VERSION,
        "model": MODEL_ID,
        "status": "ok",
        "attempt_count": 0,
        "attempt_errors": [],
        "result": {},
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
    if index < len(records) and SLEEP_SECONDS:
        time.sleep(SLEEP_SECONDS)

manifest = {
    "run_id": RUN_ID,
    "started_at": started_at,
    "finished_at": now_stamp(),
    "input_path": str(input_path),
    "output_dir": str(OUTPUT_DIR),
    "prompt_version": PROMPT_VERSION,
    "model": MODEL_ID,
    "record_count": len(output_records),
    "ok_count": sum(1 for record in output_records if record["status"] == "ok"),
    "error_count": sum(1 for record in output_records if record["status"] == "error"),
    "sleep_seconds": SLEEP_SECONDS,
    "max_attempts": MAX_ATTEMPTS,
    "retry_sleep_seconds": RETRY_SLEEP_SECONDS,
}
write_outputs(OUTPUT_DIR, output_records, manifest)
print(f"output_dir={OUTPUT_DIR}")
print(json.dumps(manifest, ensure_ascii=False, indent=2))

zip_path = shutil.make_archive("/content/book_verification_results", "zip", OUTPUT_DIR)
print(zip_path)
files.download(zip_path)
