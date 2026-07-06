# Single-cell Colab workflow for bibliography verification through explicit
# web-search evidence. Gemini receives only scraped snippets/page text and does
# not use the paid Google Search grounding tool.

import csv
import getpass
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

try:
    import pandas as pd
    import requests
    from bs4 import BeautifulSoup
    from google import genai
    from google.genai import types
    from google.colab import drive, files
except ModuleNotFoundError:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "google-genai", "pandas", "requests", "beautifulsoup4"],
        check=True,
    )
    import pandas as pd
    import requests
    from bs4 import BeautifulSoup
    from google import genai
    from google.genai import types
    from google.colab import drive, files


MODEL_ID = globals().get("MODEL_ID", "gemini-2.5-flash")
PROMPT_VERSION = "mixed-bibliography-web-evidence-0.2-colab"
OUTPUT_DIR = Path("/content/bibliography_web_evidence_results")
SLEEP_SECONDS = globals().get("SLEEP_SECONDS", 5.0)
MAX_ATTEMPTS = globals().get("MAX_ATTEMPTS", 3)
RETRY_SLEEP_SECONDS = globals().get("RETRY_SLEEP_SECONDS", 10.0)
SEARCH_TIMEOUT = globals().get("SEARCH_TIMEOUT", 15)
PAGE_TIMEOUT = globals().get("PAGE_TIMEOUT", 15)
MAX_SEARCH_RESULTS = globals().get("MAX_SEARCH_RESULTS", 8)
MAX_FETCHED_PAGES = globals().get("MAX_FETCHED_PAGES", 4)
MAX_PAGE_TEXT_CHARS = globals().get("MAX_PAGE_TEXT_CHARS", 4500)
MAX_EVIDENCE_CHARS = globals().get("MAX_EVIDENCE_CHARS", 18000)
SAVE_AFTER_EVERY_RECORD = globals().get("SAVE_AFTER_EVERY_RECORD", True)
CHECKPOINT_EVERY = globals().get("CHECKPOINT_EVERY", 20)
USE_GOOGLE_DRIVE_CHECKPOINT = globals().get("USE_GOOGLE_DRIVE_CHECKPOINT", True)
DRIVE_CHECKPOINT_ROOT = Path(globals().get("DRIVE_CHECKPOINT_ROOT", "/content/drive/MyDrive/bibliobon_colab_web_evidence_checkpoints"))
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


SYSTEM_PROMPT = """
Ты — автоматизированный библиографический робот-валидатор. Твоя единственная цель — переводить сырые исторические, книжные и статейные данные в строго регламентированные строки ГОСТ Р 7.0.100-2018 (полная запись: source.citation_gost_2018_full) и ГОСТ 7.1-2003/краткая рабочая запись (source.citation_gost_2003_short).

Ты работаешь только на основе двух входов:
1. исходной сырой строки;
2. блока EVIDENCE, который содержит найденные Python-скриптом URL, сниппеты и фрагменты страниц.

КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать внутреннюю память модели для добавления страниц, годов, издательств, ISBN/ISSN/DOI, томов, номеров, мест издания или URL. Если факт отсутствует в исходной строке и блоке EVIDENCE, ставь null и добавляй предупреждение. Не превращай вероятное знание в установленный факт.

0. ТИПЫ ЗАПИСЕЙ
На вход ты получаешь ОДНУ сырую строку. В ней может быть монография, отдельный том многотомного издания, "склеенная" строка многотомника, статья, глава, материал конференции, газетная публикация, закон/устав/указ/официальный акт, электронный ресурс или дефектная запись.

Допустимые значения record_type:
- "monograph";
- "multivolume_part";
- "journal_article";
- "collection_article";
- "book_chapter";
- "conference_article";
- "newspaper_article";
- "legal_document";
- "electronic_resource";
- "unknown".

1. ПРАВИЛА ЖЕСТКОЙ ОЧИСТКИ ТЕКСТА И РЕГИСТРА
1.1. Удаляй внешние кавычки в начале и конце заглавий.
1.2. Любые должности, сословия, титулы, органы и ведомства внутри названия пиши со строчной буквы: воеводы, бурмистры, кассиры, управляющие, наркомы, коллегии, палаты, думы.
1.3. Заглавная буква остается только для первого слова названия, имен собственных и географических объектов: Оружейная палата, Царство Польское, Петр I.
1.4. В хронологических диапазонах и диапазонах страниц заменяй дефис на короткое тире без пробелов вокруг него: "1843–1934 гг.", "С. 347–352".
1.5. Не меняй подтвержденную источником орфографию, если это историческое официальное название или точная цитата заглавия.

2. ПРАВИЛА ДОКАЗАТЕЛЬНОСТИ
2.1. Любое заполненное библиографическое поле должно подтверждаться исходной строкой или блоком EVIDENCE.
2.2. Для каждого существенного исправления или добавления укажи короткое объяснение в warnings или source.warnings.
2.3. В source.evidence_refs и host.evidence_refs укажи номера evidence_id, на которые опирается запись. Если источников нет, массив должен быть пустым.
2.4. Если EVIDENCE содержит только поисковую выдачу без текста страниц, снизь confidence и добавь предупреждение "подтверждено только сниппетами".
2.5. Если точный общий объём книги/тома не найден, source.extent = null, а в ГОСТ-строке используй маркер "[объем не установлен]".
2.6. Если точные страницы статьи, главы или закона не найдены, source.pages = null, а в ГОСТ-строке используй единый маркер "С. [страницы уточняются]".
2.7. confidence = 1.0 только если ключевые реквизиты и страницы/объем подтверждены EVIDENCE; confidence = 0.5 если сработал маркер "[страницы уточняются]" или "[объем не установлен]"; confidence ниже 0.5 если найденные данные противоречивы.
2.8. Не добавляй текст вне JSON.

3. ФОРМУЛЬНЫЙ ШАБЛОН ДЛЯ ПОЛНОЙ ЗАПИСИ source.citation_gost_2018_full
3.1. Ты обязан собирать строку по формуле, где основные области разделяются знаком " — ":
[Область заглавия]. — [Область издания]. — [Область выходных данных]. — [Область физической характеристики]. — [Область серии]. — [Область примечания]. — [Область средства доступа].
3.2. Пропускай область только если она не применима или не подтверждена. Не оставляй двойные разделители.
3.3. Область средства доступа обязательна: самостоятельная книжная/томовая запись должна завершаться "— Текст : непосредственный."; аналитическая запись статьи/главы/закона с публикацией через // должна содержать "— Текст : непосредственный //" перед источником публикации.
3.4. Для книг с одним, двумя или тремя авторами полная запись начинается с первого автора, после "/" перечисляются все авторы в прямой форме.
3.5. Для книг с четырьмя авторами запись начинается с заглавия, после "/" перечисляются все четыре автора.
3.6. Для книг с пятью и более авторами запись начинается с заглавия, после "/" пиши первых трех авторов и "[и др.]".

4. ФОРМУЛЬНЫЙ ШАБЛОН ДЛЯ КРАТКОЙ ЗАПИСИ source.citation_gost_2003_short
4.1. Применяй жесткое селективное сжатие: удаляй полные названия ведомств-издателей, тип текста "Текст : непосредственный", длинные примечания и лишние исторические пояснения.
4.2. Для книг: [Автор]. [Заглавие]. — [Сокращенный город], [год]. — [объем].
4.3. Для статей: [Автор]. [Заглавие] // [Источник]. — [год]. — [том/номер]. — [страницы].
4.4. Для законов: [Заглавие] : [краткие реквизиты] // [Аббревиатура кодекса]. — [Год издания]. — [Сжатый том]. — [Страницы]. — [Сжатый сопубликованный источник].
4.5. Если страниц нет, используй строго "С. [страницы уточняются]".

5. СПЕЦИАЛЬНЫЙ РЕГЛАМЕНТ ДЛЯ ЗАКОНОВ ПСЗРИ/СУ/СЗ
5.1. record_type = "legal_document".
5.2. source.title: очищенное название закона/акта без внешних кавычек.
5.3. source.title_remainder: вид акта, орган, номер и дата, если они подтверждены исходной строкой или EVIDENCE.
5.4. Полная запись закона строится так:
[Название закона] : [вид акта, орган, № и дата]. — Текст : непосредственный // [Полное название кодекса]. [Собрание]. — [Город : издательство, год]. — [Том/год/отделение]. — [Страницы]. — [Примечание о сопубликованном источнике].
5.5. Перед знаком "//" точку после маркера "Текст : непосредственный" не ставь иначе, чем в составе самого маркера; используй форму "— Текст : непосредственный //".
5.6. После "//" для ПСЗРИ пиши полное название "Полное собрание законов Российской империи" и номер собрания: "Собрание 1-е", "Собрание 2-е", "Собрание 3-е".
5.7. Для ПСЗРИ 1-го собрания выходные данные: "Санкт-Петербург : [б. и.], 1830".
5.8. Для ПСЗРИ 2-го собрания выходные данные: "Санкт-Петербург : Типография II Отделения Собственной Его Императорского Величества Канцелярии, [год издания]".
5.9. Для СУ дореволюционного периода полное название: "Собрание узаконений и распоряжений правительства, издаваемое при Правительствующем Сенате"; выходные данные при необходимости: "Санкт-Петербург : Сенатская типография, [год]" или "Петроград : Сенатская типография, [год]" с 1914 года.
5.10. Если точные выходные данные/год издания не подтверждены EVIDENCE или исходной строкой, не подставляй их из памяти; ставь null в полях и предупреждение, а в ГОСТ-строке используй только подтвержденную часть.
5.11. Область физической характеристики для ПСЗРИ: "Т. [номер] : [год тома]" и при наличии "Отделение [номер]"; далее страницы. Если страниц нет: "С. [страницы уточняются]".
5.12. Если в сырой строке есть СУ/СЗ/Известия как перекрестный источник, добавляй его в конец полной записи как "— Прим.: Опубл. также в: ...".
5.13. Краткая запись закона строится так:
[Название закона] : [краткие реквизиты] // [ПСЗРИ. Собр. 1-е/2-е/3-е]. — [СПб./Пг./М., год издания если подтвержден]. — [Т. N, отд. N если есть]. — [С. ...]. — [То же: СУ/СЗ ...].
5.14. Название кодекса в краткой записи сжимай только до регламентированных аббревиатур: "ПСЗРИ. Собр. 1-е.", "ПСЗРИ. Собр. 2-е.", "ПСЗРИ. Собр. 3-е.", "СУ", "СУРП", "СЗ СССР".
5.15. Дублировать год тома в краткой записи запрещено: вместо "Т. 53 : 1878" пиши "Т. 53" или "Т. 53, отд. 2".
5.16. Перекрестные источники в краткой записи сжимай до маркера "— То же: СУ. 1878. № 208. Ст. 940.".
5.17. Не выдумывай страницы закона. Если EVIDENCE не содержит точного диапазона страниц, source.pages = null, а обе ГОСТ-строки содержат "С. [страницы уточняются]".

6. ПРАВИЛА ДЛЯ МНОГОТОМНИКОВ
6.1. Если входная строка описывает несколько томов с разными годами, издательствами или объёмами, запрещено собирать их в одну общую ГОСТ-строку.
6.2. Верни несколько объектов record_type="multivolume_part", по одному на каждый подтверждённый том.
6.3. В source.title сохраняй общее название серии/издания.
6.4. В source.title_remainder выноси конкретный том, например: "в 3 томах. Т. 1 : Центральная Африка".

7. ПРАВИЛА ДЛЯ СТАТЕЙ И ГЛАВ
7.1. Для статьи/главы объект source описывает саму статью или главу.
7.2. Объект host описывает источник публикации: выпуск журнала, сборник, книгу, материалы конференции, газету или сайт.
7.3. Не смешивай страницы статьи и объём источника: source.pages = диапазон страниц статьи; host.extent = общий объём сборника/книги/выпуска, только если подтверждён.
7.4. Для журнальных статей host.host_type = "journal_issue"; host.journal_title = название журнала; host.issue_year, host.volume_number, host.issue_number = год, том, номер/выпуск.
7.5. Для статей в сборниках и глав host.host_type = "collection" или "monograph"; host.title = название сборника/книги.

Схема каждого объекта в корневом массиве:
{
  "record_type": "monograph | multivolume_part | journal_article | collection_article | book_chapter | conference_article | newspaper_article | legal_document | electronic_resource | unknown",
  "source": {
    "raw_author_string": "автор/первый автор в форме 'Фамилия И. О.' или null",
    "title": "основное заглавие источника/статьи без внешних кавычек; для многотомников здесь сохраняется общее название серии; для законов — название закона",
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
    "pages": "страницы статьи/главы/закона, например 'С. 15–27', или null",
    "citation_gost_2018_full": "полная запись книги, конкретного тома, статьи, главы или закона по ГОСТ Р 7.0.100-2018",
    "citation_gost_2003_short": "краткая запись книги, конкретного тома, статьи, главы или закона по ГОСТ",
    "evidence_refs": ["E1"],
    "warnings": [],
    "confidence": 0.0
  },
  "host": {
    "host_type": "journal_issue | collection | monograph | conference_proceedings | newspaper_issue | legal_collection | website | unknown",
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
    "evidence_refs": ["E1"],
    "warnings": [],
    "confidence": 0.0
  },
  "keywords": ["3-5 ключевых слов"],
  "warnings": ["общие предупреждения"],
  "confidence": 0.0
}

Для монографий и отдельных томов host должен быть null. Для статей, глав, материалов конференций и законов с источником публикации host может быть объектом. Если тип записи неизвестен, всё равно верни объект со значением record_type="unknown", максимально заполненными подтверждёнными полями и предупреждениями.
""".strip()


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}


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
    return re.sub(r"\s+", " ", html.unescape(str(text))).strip()


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


def title_phrases(raw_input: str) -> list[str]:
    phrases = []
    for match in re.findall(r"[«\"]([^»\"]{8,220})[»\"]", raw_input):
        phrases.append(clean_text(match))
    before_slash = raw_input.split("/", 1)[0]
    if 8 <= len(before_slash) <= 220:
        phrases.append(clean_text(before_slash))
    return list(dict.fromkeys(phrases))


def legal_archive_queries(raw_input: str) -> list[str]:
    queries = []
    psz_match = re.search(r"ПСЗ[-–]\s*(\d+)\s*,?\s*т\.\s*(\d+)", raw_input, flags=re.I)
    number_match = re.search(r"№\s*(\d+)", raw_input)
    su_match = re.search(r"СУ[-–]\s*(\d{4})(?:\s*,\s*№\s*([\d/]+))?(?:.*?ст\.\s*([\d/]+))?", raw_input, flags=re.I)
    if psz_match and number_match:
        collection = f"ПСЗ-{psz_match.group(1)}"
        volume = f"т. {psz_match.group(2)}"
        number = f"№ {number_match.group(1)}"
        queries.extend(
            [
                f"{collection} {volume} {number} страницы",
                f"{collection} {volume} {number} С.",
                f"{collection} {volume} {number} Полное собрание законов",
            ]
        )
    if su_match:
        year = su_match.group(1)
        issue = f" № {su_match.group(2)}" if su_match.group(2) else ""
        article = f" ст. {su_match.group(3)}" if su_match.group(3) else ""
        queries.append(f"СУ {year}{issue}{article} Собрание узаконений")
    return [clean_text(query) for query in queries if clean_text(query)]


def build_search_queries(raw_input: str) -> list[str]:
    queries = []
    queries.extend(legal_archive_queries(raw_input))
    for phrase in title_phrases(raw_input):
        queries.append(f'"{phrase}"')
        if "ПСЗ" in raw_input or "СУ-" in raw_input or re.search(r"№\s*\d+", raw_input):
            number = re.search(r"№\s*(\d+)", raw_input)
            psz = re.search(r"ПСЗ[-–]\s*(\d+)\s*,?\s*т\.\s*(\d+)", raw_input, flags=re.I)
            if number:
                queries.append(f'"{phrase}" "№ {number.group(1)}"')
            if psz:
                queries.append(f'"{phrase}" "ПСЗ" "т. {psz.group(2)}"')
    if not queries:
        queries.append(raw_input)
    normalized = []
    for query in queries:
        query = clean_text(query.replace("«", "").replace("»", ""))
        if query and query not in normalized:
            normalized.append(query)
    return normalized[:4]


def google_result_url(href: str) -> str:
    if href.startswith("/url?"):
        parsed = urlparse(href)
        target = parse_qs(parsed.query).get("q", [""])[0]
        return unquote(target)
    return href


def search_google_html(query: str) -> list[dict[str, str]]:
    url = f"https://www.google.com/search?q={quote_plus(query)}&hl=ru&num=10"
    response = requests.get(url, headers=HEADERS, timeout=SEARCH_TIMEOUT)
    if response.status_code != 200:
        return [{"provider": "google", "query": query, "title": f"Google returned HTTP {response.status_code}", "url": url, "snippet": "", "error": str(response.status_code)}]
    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    for block in soup.select("div.g"):
        link = block.select_one("a[href]")
        if not link:
            continue
        title_el = block.select_one("h3")
        target_url = google_result_url(link.get("href", ""))
        if not target_url.startswith("http"):
            continue
        snippet_el = block.select_one(".VwiC3b, .IsZvec, .aCOpRe")
        title = clean_text(title_el.get_text(" ")) if title_el else ""
        snippet = clean_text(snippet_el.get_text(" ")) if snippet_el else ""
        if title or snippet:
            results.append({"provider": "google", "query": query, "title": title, "url": target_url, "snippet": snippet, "error": ""})
    if not results and ("captcha" in response.text.lower() or "unusual traffic" in response.text.lower()):
        return [{"provider": "google", "query": query, "title": "Google blocked the request", "url": url, "snippet": "CAPTCHA or unusual traffic page", "error": "blocked"}]
    return results


def search_duckduckgo_html(query: str) -> list[dict[str, str]]:
    url = f"https://duckduckgo.com/html/?q={quote_plus(query)}&kl=ru-ru"
    response = requests.get(url, headers=HEADERS, timeout=SEARCH_TIMEOUT)
    if response.status_code != 200:
        return [{"provider": "duckduckgo", "query": query, "title": f"DuckDuckGo returned HTTP {response.status_code}", "url": url, "snippet": "", "error": str(response.status_code)}]
    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    for block in soup.select(".result"):
        link = block.select_one(".result__a")
        if not link:
            continue
        title = clean_text(link.get_text(" "))
        target_url = link.get("href", "")
        snippet_el = block.select_one(".result__snippet")
        snippet = clean_text(snippet_el.get_text(" ")) if snippet_el else ""
        if target_url.startswith("//duckduckgo.com/l/?"):
            target_url = "https:" + target_url
        if "duckduckgo.com/l/?" in target_url:
            parsed = urlparse(target_url)
            target_url = unquote(parse_qs(parsed.query).get("uddg", [target_url])[0])
        if title or snippet:
            results.append({"provider": "duckduckgo", "query": query, "title": title, "url": target_url, "snippet": snippet, "error": ""})
    return results


def dedupe_results(results: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    deduped = []
    for result in results:
        key = result.get("url") or (result.get("provider"), result.get("title"), result.get("snippet"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped


def search_web(raw_input: str) -> list[dict[str, Any]]:
    all_results = []
    for query in build_search_queries(raw_input):
        try:
            all_results.extend(search_google_html(query))
        except Exception as exc:
            all_results.append({"provider": "google", "query": query, "title": "Google search failed", "url": "", "snippet": "", "error": str(exc)})
        if len([item for item in all_results if item.get("url", "").startswith("http") and not item.get("error")]) >= MAX_SEARCH_RESULTS:
            break
        try:
            all_results.extend(search_duckduckgo_html(query))
        except Exception as exc:
            all_results.append({"provider": "duckduckgo", "query": query, "title": "DuckDuckGo search failed", "url": "", "snippet": "", "error": str(exc)})
        time.sleep(1)
    return dedupe_results(all_results)[:MAX_SEARCH_RESULTS]


def fetch_page_text(url: str) -> tuple[str, str]:
    if not url.startswith(("http://", "https://")):
        return "", "not_http"
    try:
        response = requests.get(url, headers=HEADERS, timeout=PAGE_TIMEOUT, allow_redirects=True)
        content_type = response.headers.get("content-type", "")
        if response.status_code != 200:
            return "", f"http_{response.status_code}"
        if "pdf" in content_type.lower() or url.lower().endswith(".pdf"):
            return "", "pdf_not_fetched"
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        text = clean_text(soup.get_text(" "))
        return text[:MAX_PAGE_TEXT_CHARS], ""
    except Exception as exc:
        return "", str(exc)


def collect_evidence(raw_input: str) -> list[dict[str, Any]]:
    search_results = search_web(raw_input)
    evidence = []
    fetched = 0
    for index, result in enumerate(search_results, start=1):
        page_text = ""
        fetch_error = ""
        if fetched < MAX_FETCHED_PAGES and result.get("url", "").startswith("http") and not result.get("error"):
            page_text, fetch_error = fetch_page_text(result["url"])
            fetched += 1
            time.sleep(1)
        evidence.append(
            {
                "evidence_id": f"E{index}",
                "provider": result.get("provider", ""),
                "query": result.get("query", ""),
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "snippet": result.get("snippet", ""),
                "page_text": page_text,
                "search_error": result.get("error", ""),
                "fetch_error": fetch_error,
            }
        )
    return evidence


def evidence_for_prompt(evidence: list[dict[str, Any]]) -> str:
    blocks = []
    total = 0
    for item in evidence:
        block = "\n".join(
            [
                f"[{item['evidence_id']}] provider={item.get('provider', '')}",
                f"query: {item.get('query', '')}",
                f"title: {item.get('title', '')}",
                f"url: {item.get('url', '')}",
                f"snippet: {item.get('snippet', '')}",
                f"page_text: {item.get('page_text', '')}",
                f"errors: search={item.get('search_error', '')}; fetch={item.get('fetch_error', '')}",
            ]
        )
        if total + len(block) > MAX_EVIDENCE_CHARS:
            break
        blocks.append(block)
        total += len(block)
    return "\n\n".join(blocks) if blocks else "[EVIDENCE EMPTY]"


def build_config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.0,
        response_mime_type="application/json",
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


def normalize_refs(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


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
    normalized["evidence_refs"] = normalize_refs(source.get("evidence_refs"))
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
    normalized["evidence_refs"] = normalize_refs(host.get("evidence_refs"))
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


def verify_with_gemini_once(raw_input: str, evidence: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    payload = {
        "task": "verify_mixed_bibliography_record_from_explicit_web_evidence",
        "raw_input": raw_input,
        "language": "ru",
        "target_standard": "ГОСТ Р 7.0.100-2018",
        "evidence_policy": "Use only raw_input and EVIDENCE. Unknown facts must be null.",
        "EVIDENCE": evidence_for_prompt(evidence),
    }
    response = client.models.generate_content(
        model=MODEL_ID,
        contents=json.dumps(payload, ensure_ascii=False),
        config=build_config(),
    )
    text = response.text or ""
    parsed = parse_json_response(text)
    return normalize_records(parsed), text


def verify_with_retries(raw_input: str, evidence: list[dict[str, Any]], max_attempts: int = MAX_ATTEMPTS) -> tuple[list[dict[str, Any]], str, int, list[str]]:
    errors = []
    for attempt in range(1, max_attempts + 1):
        try:
            result, raw_text = verify_with_gemini_once(raw_input, evidence)
            return result, raw_text, attempt, errors
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


def result_items(record: dict[str, Any]) -> list[dict[str, Any]]:
    result = record.get("result") or []
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)] or [{}]
    if isinstance(result, dict):
        return [result]
    return [{}]


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
                "source_evidence_refs": value_for_tsv(source.get("evidence_refs", [])),
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
                "host_evidence_refs": value_for_tsv(host.get("evidence_refs", [])),
                "keywords": value_for_tsv(item.get("keywords", [])),
                "warnings": value_for_tsv(item.get("warnings", [])) or value_for_tsv(source.get("warnings", [])),
                "host_warnings": value_for_tsv(host.get("warnings", [])),
                "confidence": value_for_tsv(item.get("confidence")),
                "source_confidence": value_for_tsv(source.get("confidence")),
                "host_confidence": value_for_tsv(host.get("confidence")),
            }
        )
    return rows


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

    evidence_rows = []
    for record in output_records:
        for item in record.get("evidence", []):
            evidence_rows.append(
                {
                    "source_number": record.get("source_number", ""),
                    "evidence_id": item.get("evidence_id", ""),
                    "provider": item.get("provider", ""),
                    "query": item.get("query", ""),
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("snippet", ""),
                    "page_text_sample": (item.get("page_text", "") or "")[:1000],
                    "search_error": item.get("search_error", ""),
                    "fetch_error": item.get("fetch_error", ""),
                }
            )
    with (output_dir / "search_evidence.tsv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["source_number", "evidence_id", "provider", "query", "title", "url", "snippet", "page_text_sample", "search_error", "fetch_error"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(evidence_rows)

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
        "max_search_results": MAX_SEARCH_RESULTS,
        "max_fetched_pages": MAX_FETCHED_PAGES,
        "checkpoint_every": CHECKPOINT_EVERY,
        "save_after_every_record": SAVE_AFTER_EVERY_RECORD,
        "uses_gemini_google_search_tool": False,
    }


def zip_outputs(output_dir: Path, zip_base_path: Path) -> Path:
    if zip_base_path.with_suffix(".zip").exists():
        zip_base_path.with_suffix(".zip").unlink()
    return Path(shutil.make_archive(str(zip_base_path), "zip", output_dir))


def save_checkpoint(output_records: list[dict[str, Any]], manifest: dict[str, Any], checkpoint_dir: Path | None) -> None:
    write_outputs(OUTPUT_DIR, output_records, manifest)
    zip_outputs(OUTPUT_DIR, OUTPUT_DIR.parent / "bibliography_web_evidence_results")
    if checkpoint_dir:
        write_outputs(checkpoint_dir, output_records, manifest)
        zip_outputs(checkpoint_dir, checkpoint_dir / "bibliography_web_evidence_results")


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
done_by_source_number = {str(record.get("source_number")): record for record in checkpoint_records if record.get("status") in {"ok", "error"}}
output_records = []
started_at = now_stamp()
run_status = "ok"
fatal_error = ""

if done_by_source_number:
    print(f"Loaded {len(done_by_source_number)} checkpoint records. Completed source_number values will be skipped.")

try:
    for index, record in enumerate(records, start=1):
        source_number = str(record.get("source_number"))
        if source_number in done_by_source_number:
            restored = done_by_source_number[source_number]
            output_records.append(restored)
            print(f"{index}/{len(records)} source_number={source_number} restored")
            continue

        raw_input = record["raw_input"]
        print(f"{index}/{len(records)} source_number={source_number} searching")
        try:
            evidence = collect_evidence(raw_input)
            ok_evidence_count = sum(1 for item in evidence if item.get("url") and not item.get("search_error"))
            print(f"  evidence_items={len(evidence)} urls={ok_evidence_count}")
            result, raw_response, attempt_count, attempt_errors = verify_with_retries(raw_input, evidence)
            output_record = {
                **record,
                "record_id": "web-" + hashlib.sha1(raw_input.encode("utf-8")).hexdigest()[:16],
                "status": "ok",
                "error": "",
                "attempt_count": attempt_count,
                "attempt_errors": attempt_errors,
                "result": result,
                "evidence": evidence,
                "raw_model_response": raw_response,
            }
            print(f"{index}/{len(records)} source_number={source_number} ok")
        except Exception as exc:
            error_text = str(exc)
            output_record = {
                **record,
                "record_id": "web-" + hashlib.sha1(raw_input.encode("utf-8")).hexdigest()[:16],
                "status": "error",
                "error": error_text,
                "attempt_count": MAX_ATTEMPTS,
                "attempt_errors": [error_text],
                "result": [],
                "evidence": [],
                "raw_model_response": "",
            }
            print(f"{index}/{len(records)} source_number={source_number} error: {error_text[:200]}")
            if is_quota_error(error_text):
                run_status = "stopped_quota"
                fatal_error = error_text
                output_records.append(output_record)
                for skipped in records[index:]:
                    output_records.append(
                        {
                            **skipped,
                            "record_id": "web-" + hashlib.sha1(skipped["raw_input"].encode("utf-8")).hexdigest()[:16],
                            "status": "skipped",
                            "error": "Skipped because Gemini quota/rate limit stopped the run",
                            "attempt_count": 0,
                            "attempt_errors": [],
                            "result": [],
                            "evidence": [],
                            "raw_model_response": "",
                        }
                    )
                break

        output_records.append(output_record)
        manifest = build_manifest(RUN_ID, started_at, input_path, OUTPUT_DIR, output_records, checkpoint_dir, run_status)
        save_checkpoint(output_records, manifest, checkpoint_dir)
        if checkpoint_should_save(len(output_records)):
            print(f"  checkpoint saved: {len(output_records)} records")
        time.sleep(SLEEP_SECONDS)
finally:
    manifest = build_manifest(RUN_ID, started_at, input_path, OUTPUT_DIR, output_records, checkpoint_dir, run_status)
    if fatal_error:
        manifest["fatal_error"] = fatal_error
    save_checkpoint(output_records, manifest, checkpoint_dir)

zip_path = zip_outputs(OUTPUT_DIR, OUTPUT_DIR.parent / "bibliography_web_evidence_results")
print(f"Done. run_status={run_status}")
print(f"Local output: {OUTPUT_DIR}")
if checkpoint_dir:
    print(f"Drive checkpoint/output: {checkpoint_dir}")
print(f"Zip: {zip_path}")
files.download(str(zip_path))
