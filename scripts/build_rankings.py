#!/usr/bin/env python3
"""Build open-data world university rankings.

This script uses the commercial rankings only to define the candidate pool.
Final scores are computed from open, programmatically queryable data.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import re
import statistics
import sys
import time
import unicodedata
import urllib.parse
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 open-data-ranking/0.1"
)

ARWU_URL = "https://www.shanghairanking.com/rankings/arwu/2025"
QS_URL = "https://www.topuniversities.com/world-university-rankings?items_per_page=200"
QS_XLSX_URL = (
    "https://insights.qs.com/hubfs/Rankings%20Excel%20Reports/"
    "2027%20QS%20World%20University%20Rankings%201.1%20%28For%20qs.com%29.xlsx"
)
USNEWS_URL = "https://www.usnews.com/education/best-global-universities/rankings"
USNEWS_SEARCH_URL = "https://www.usnews.com/education/best-global-universities/search?format=json&page={page}"

OPENALEX_BASE = "https://api.openalex.org"
ROR_BASE = "https://api.ror.org/v2/organizations"

WORK_TYPES = "article,review,book,book-chapter"
OPENALEX_WORK_TYPE_FILTER = WORK_TYPES.replace(",", "|")
PER_PAGE = 200
SLEEP_SECONDS = 0.12
OPENALEX_REQUEST_INTERVAL_SECONDS = float(os.environ.get("OPENALEX_REQUEST_INTERVAL_SECONDS", "0.2"))
HTTP_MAX_ATTEMPTS = int(os.environ.get("OPENALEX_HTTP_MAX_ATTEMPTS", "8"))
HTTP_RETRY_STATUSES = {429, 500, 502, 503, 504}
CANDIDATE_RANK_LIMIT = 200
SOURCE_RANK_FIELDS = {
    "ARWU 2025": "arwu_2025_rank",
    "QS 2027": "qs_2027_rank",
    "US News 2026-2027": "usnews_2026_2027_rank",
}
SOURCE_FLAGS = {
    "ARWU 2025": "in_arwu_2025",
    "QS 2027": "in_qs_2027",
    "US News 2026-2027": "in_usnews_2026_2027",
}
SOURCE_RANK_FIELDNAMES = list(SOURCE_RANK_FIELDS.values())
SOURCE_FLAG_FIELDNAMES = list(SOURCE_FLAGS.values())
AMBIGUOUS_RANK = "__AMBIGUOUS__"
_last_openalex_request = 0.0


@dataclass(frozen=True)
class MetricWindow:
    key: str
    label: str
    start: int
    end: int
    kind: str
    note: str = ""

    @property
    def year_label(self) -> str:
        return str(self.start) if self.start == self.end else f"{self.start}-{self.end}"

    @property
    def is_annual(self) -> bool:
        return self.kind == "annual"


WINDOWS = [
    MetricWindow("2021_2025", "5-year 2021-2025", 2021, 2025, "five_year"),
    MetricWindow("2020_2024", "5-year 2020-2024", 2020, 2024, "five_year"),
    *[MetricWindow(str(year), f"Annual snapshot {year}", year, year, "annual") for year in range(2020, 2026)],
]
DEFAULT_WINDOW_KEY = "2021_2025"
LEGACY_WINDOW_KEY = "2020_2024"
METRIC_SPECS = [
    {
        "key": "scale",
        "score": "scale_score",
        "source": "works",
        "label": "Publication scale",
        "description": "OpenAlex works in the selected publication window.",
        "format": "integer",
        "log": True,
        "research_weight": 0.10,
        "comprehensive_weight": 0.18,
    },
    {
        "key": "top10_volume",
        "score": "top10_volume_score",
        "source": "top10_count",
        "label": "Top 10% papers",
        "description": "Works in OpenAlex's field/year-normalized top 10% citation percentile.",
        "format": "integer",
        "log": True,
        "research_weight": 0.20,
        "comprehensive_weight": 0.14,
    },
    {
        "key": "top1_volume",
        "score": "top1_volume_score",
        "source": "top1_count",
        "label": "Top 1% papers",
        "description": "Works in OpenAlex's field/year-normalized top 1% citation percentile.",
        "format": "integer",
        "log": True,
        "research_weight": 0.16,
        "comprehensive_weight": 0.10,
    },
    {
        "key": "top10_rate",
        "score": "top10_rate_score",
        "source": "top10_share",
        "label": "Top 10% share",
        "description": "Top 10% papers divided by total works.",
        "format": "percent",
        "log": False,
        "research_weight": 0.12,
        "comprehensive_weight": 0.08,
    },
    {
        "key": "top1_rate",
        "score": "top1_rate_score",
        "source": "top1_share",
        "label": "Top 1% share",
        "description": "Top 1% papers divided by total works.",
        "format": "percent",
        "log": False,
        "research_weight": 0.10,
        "comprehensive_weight": 0.06,
    },
    {
        "key": "h_index",
        "score": "h_index_score",
        "source": "h_index",
        "label": "Institution h-index (all-time)",
        "description": "OpenAlex institution all-time h-index; this does not vary by publication window.",
        "format": "integer",
        "log": True,
        "research_weight": 0.12,
        "comprehensive_weight": 0.10,
    },
    {
        "key": "field_breadth",
        "score": "field_breadth_score",
        "source": "field_entropy",
        "label": "Field breadth",
        "description": "Shannon entropy over OpenAlex primary-topic fields.",
        "format": "decimal",
        "log": False,
        "research_weight": 0.06,
        "comprehensive_weight": 0.16,
    },
    {
        "key": "active_fields",
        "score": "active_fields_score",
        "source": "active_fields",
        "label": "Active fields",
        "description": "OpenAlex fields with meaningful publication volume.",
        "format": "integer",
        "log": False,
        "research_weight": 0.03,
        "comprehensive_weight": 0.08,
    },
    {
        "key": "international_collab",
        "score": "international_collab_score",
        "source": "international_collab_share",
        "label": "International collaboration",
        "description": "Works with affiliations from more than one country.",
        "format": "percent",
        "log": False,
        "research_weight": 0.08,
        "comprehensive_weight": 0.08,
    },
    {
        "key": "core_source",
        "score": "core_source_score",
        "source": "core_source_share",
        "label": "Core-source share",
        "description": "Works whose primary source is marked core by OpenAlex.",
        "format": "percent",
        "log": False,
        "research_weight": 0.06,
        "comprehensive_weight": 0.05,
    },
    {
        "key": "open_access",
        "score": "open_access_score",
        "source": "oa_share",
        "label": "Open access share",
        "description": "OpenAlex open-access works divided by total works.",
        "format": "percent",
        "log": False,
        "research_weight": 0.03,
        "comprehensive_weight": 0.03,
    },
    {
        "key": "sdg",
        "score": "sdg_score",
        "source": "sdg_share",
        "label": "SDG-linked research",
        "description": "Works tagged to at least one UN Sustainable Development Goal.",
        "format": "percent",
        "log": False,
        "research_weight": 0.02,
        "comprehensive_weight": 0.02,
    },
    {
        "key": "funder_diversity",
        "score": "funder_diversity_score",
        "source": "funder_count",
        "label": "Funder diversity",
        "description": "Distinct funders observed in OpenAlex work metadata.",
        "format": "integer",
        "log": True,
        "research_weight": 0.02,
        "comprehensive_weight": 0.02,
    },
]


def ensure_dirs() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)


def throttle_openalex(url: str) -> None:
    global _last_openalex_request
    if not url.startswith(OPENALEX_BASE):
        return
    elapsed = time.monotonic() - _last_openalex_request
    if elapsed < OPENALEX_REQUEST_INTERVAL_SECONDS:
        time.sleep(OPENALEX_REQUEST_INTERVAL_SECONDS - elapsed)
    _last_openalex_request = time.monotonic()


def retry_delay(exc: urllib.error.HTTPError, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After")
    if retry_after:
        try:
            return min(300.0, max(1.0, float(retry_after)))
        except ValueError:
            pass
    if exc.code == 429:
        return min(180.0, 10.0 * (attempt + 1))
    return min(60.0, 2.0 * (attempt + 1))


def http_get(url: str, *, timeout: int = 60, accept: str | None = None) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(HTTP_MAX_ATTEMPTS):
        throttle_openalex(url)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in HTTP_RETRY_STATUSES or attempt >= HTTP_MAX_ATTEMPTS - 1:
                raise
            delay = retry_delay(exc, attempt)
            print(
                f"WARNING: HTTP {exc.code} for {url}; retrying in {delay:.0f}s "
                f"({attempt + 1}/{HTTP_MAX_ATTEMPTS})",
                file=sys.stderr,
            )
            time.sleep(delay)
        except urllib.error.URLError as exc:
            if attempt >= HTTP_MAX_ATTEMPTS - 1:
                raise
            delay = min(60.0, 2.0 * (attempt + 1))
            print(
                f"WARNING: request failed for {url}: {exc}; retrying in {delay:.0f}s "
                f"({attempt + 1}/{HTTP_MAX_ATTEMPTS})",
                file=sys.stderr,
            )
            time.sleep(delay)
    raise RuntimeError(f"HTTP request failed after {HTTP_MAX_ATTEMPTS} attempts: {url}")


def cached_get(url: str, cache_name: str, *, timeout: int = 60, refresh: bool = False) -> str:
    ensure_dirs()
    path = RAW / cache_name
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8")
    data = http_get(url, timeout=timeout)
    text = data.decode("utf-8", errors="replace")
    path.write_text(text, encoding="utf-8")
    return text


def api_json(url: str, cache_name: str | None = None, *, refresh: bool = False, timeout: int = 60) -> Any:
    if cache_name:
        path = RAW / cache_name
        if path.exists() and not refresh:
            return json.loads(path.read_text(encoding="utf-8"))
    data = http_get(url, timeout=timeout, accept="application/json")
    text = data.decode("utf-8", errors="replace")
    if cache_name:
        (RAW / cache_name).write_text(text, encoding="utf-8")
    return json.loads(text)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def download_binary(url: str, path: Path, *, refresh: bool = False, timeout: int = 120) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not refresh:
        return
    path.write_bytes(http_get(url, timeout=timeout))


def read_xlsx_first_sheet(path: Path) -> list[list[str]]:
    """Read a simple XLSX worksheet using the standard library."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", ns):
                texts = [t.text or "" for t in si.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")]
                shared.append("".join(texts))
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))

    def col_idx(cell_ref: str) -> int:
        letters = re.match(r"([A-Z]+)", cell_ref).group(1)  # type: ignore[union-attr]
        idx = 0
        for ch in letters:
            idx = idx * 26 + ord(ch) - ord("A") + 1
        return idx - 1

    def cell_value(cell: ET.Element) -> str:
        v = cell.find("m:v", ns)
        if v is None:
            inline = cell.find("m:is", ns)
            if inline is not None:
                return "".join(t.text or "" for t in inline.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"))
            return ""
        text = v.text or ""
        if cell.get("t") == "s":
            return shared[int(text)]
        return text

    rows: list[list[str]] = []
    for row in sheet.findall(".//m:row", ns):
        values: list[str] = []
        for cell in row.findall("m:c", ns):
            idx = col_idx(cell.get("r", "A1"))
            while len(values) <= idx:
                values.append("")
            values[idx] = clean_text(cell_value(cell))
        rows.append(values)
    return rows


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def safe_log_text(value: Any) -> str:
    return clean_text(value).encode("ascii", "replace").decode("ascii")


def rank_upper_bound(rank: str) -> int | None:
    rank = clean_text(rank).replace("=", "").replace("#", "")
    if not rank:
        return None
    nums = [int(x) for x in re.findall(r"\d+", rank)]
    if not nums:
        return None
    return max(nums)


def norm_key(name: str) -> str:
    name = name.lower()
    name = name.replace("&", " and ")
    name = re.sub(r"\([^)]*\)", " ", name)
    name = re.sub(r"\b(the|university|college|school|institute|of|and|for|at)\b", " ", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def ascii_fold(value: str) -> str:
    return unicodedata.normalize("NFKD", clean_text(value)).encode("ascii", "ignore").decode("ascii")


def strict_rank_key(name: str) -> str:
    name = ascii_fold(name).lower()
    name = name.replace("&", " and ").replace("--", " ")
    name = re.sub(r"\([^)]*\)", " ", name)
    name = re.sub(r"\bthe\b", " ", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def rank_match_keys(name: str) -> set[str]:
    keys = {strict_rank_key(name)}
    loose = norm_key(ascii_fold(name))
    if len(loose.replace(" ", "")) >= 5:
        keys.add(loose)
    return {key for key in keys if key}


def better_rank(existing: str, candidate: str) -> str:
    if not existing:
        return candidate
    if not candidate:
        return existing
    existing_bound = rank_upper_bound(existing) or 999999
    candidate_bound = rank_upper_bound(candidate) or 999999
    return candidate if candidate_bound < existing_bound else existing


@dataclass
class SourceEntry:
    name: str
    country: str
    source: str
    source_rank: str


def parse_nuxt_payload(payload: str) -> list[dict[str, Any]]:
    """Extract object literals from Nuxt JSONP by executing it in Node.

    The payload uses minified JS variables, not pure JSON. Rather than parse JS
    by hand, run it in a sandboxed Node snippet that captures the JSONP payload.
    """
    js_path = RAW / "arwu_2025_payload.js"
    out_path = RAW / "arwu_2025_payload.json"
    js_path.write_text(
        "let captured = null;\n"
        "function __NUXT_JSONP__(route, payload) { captured = {route, payload}; }\n"
        + payload
        + "\nconsole.log(JSON.stringify(captured));\n",
        encoding="utf-8",
    )
    import subprocess

    result = subprocess.run(
        ["node", str(js_path)],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    out_path.write_text(result.stdout, encoding="utf-8")
    captured = json.loads(result.stdout)
    objects: list[dict[str, Any]] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            if "univNameEn" in x and "ranking" in x:
                objects.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for item in x:
                walk(item)

    walk(captured)
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for obj in objects:
        key = (clean_text(obj.get("univNameEn")), clean_text(obj.get("ranking")))
        if key not in seen:
            seen.add(key)
            deduped.append(obj)
    return deduped


def fetch_arwu_all(refresh: bool = False, rank_limit: int | None = None) -> list[SourceEntry]:
    html_text = cached_get(ARWU_URL, "arwu_2025.html", timeout=60, refresh=refresh)
    m = re.search(r'href="([^"]+/rankings/arwu/2025/payload\.js)"', html_text)
    if not m:
        raise RuntimeError("Could not find ARWU Nuxt payload URL")
    payload_url = urllib.parse.urljoin(ARWU_URL, html.unescape(m.group(1)))
    payload = cached_get(payload_url, "arwu_2025_payload_original.js", timeout=60, refresh=refresh)
    rows = parse_nuxt_payload(payload)
    entries: list[SourceEntry] = []
    for row in rows:
        name = clean_text(row.get("univNameEn"))
        rank = clean_text(row.get("ranking"))
        country = clean_text(row.get("region"))
        upper = rank_upper_bound(rank)
        if name and upper and (rank_limit is None or upper <= rank_limit):
            entries.append(SourceEntry(name=name, country=country, source="ARWU 2025", source_rank=rank))
    entries.sort(key=lambda e: (rank_upper_bound(e.source_rank) or 9999, e.name))
    return entries


def fetch_arwu(refresh: bool = False) -> list[SourceEntry]:
    return fetch_arwu_all(refresh=refresh, rank_limit=CANDIDATE_RANK_LIMIT)


def parse_qs_from_html(html_text: str, rank_limit: int | None = CANDIDATE_RANK_LIMIT) -> list[SourceEntry]:
    entries: list[SourceEntry] = []
    # Some Drupal responses include a JSON blob in data attributes. Keep this
    # lenient because QS changes the front-end often.
    candidates: list[Any] = []
    for m in re.finditer(r"(\{[^{}]*(?:rank|title|uni|country)[^{}]*\})", html_text, flags=re.I):
        raw = html.unescape(m.group(1))
        try:
            candidates.append(json.loads(raw))
        except Exception:
            continue
    for obj in candidates:
        name = clean_text(obj.get("title") or obj.get("name") or obj.get("uni") or obj.get("institution"))
        rank = clean_text(obj.get("rank_display") or obj.get("rank") or obj.get("overall_rank"))
        country = clean_text(obj.get("country") or obj.get("location"))
        upper = rank_upper_bound(rank)
        if name and upper and (rank_limit is None or upper <= rank_limit):
            entries.append(SourceEntry(name=name, country=country, source="QS 2027", source_rank=rank))
    return entries


def fetch_qs_all(refresh: bool = False, rank_limit: int | None = None) -> list[SourceEntry]:
    entries: list[SourceEntry] = []
    xlsx_path = RAW / "qs_2027.xlsx"
    try:
        download_binary(QS_XLSX_URL, xlsx_path, refresh=refresh, timeout=120)
        rows = read_xlsx_first_sheet(xlsx_path)
        headers = rows[2]
        for values in rows[3:]:
            row = {headers[i]: values[i] if i < len(values) else "" for i in range(len(headers))}
            rank = row.get("Rank", "")
            upper = rank_upper_bound(rank)
            if upper and (rank_limit is None or upper <= rank_limit):
                entries.append(
                    SourceEntry(
                        name=clean_text(row.get("Name")),
                        country=clean_text(row.get("Country/Territory")),
                        source="QS 2027",
                        source_rank=clean_text(rank),
                    )
                )
    except Exception as exc:
        print(f"WARNING: QS Excel fetch/parse failed: {exc}", file=sys.stderr)
    if not entries:
        html_text = cached_get(QS_URL, "qs_2027.html", timeout=60, refresh=refresh)
        entries = parse_qs_from_html(html_text, rank_limit=rank_limit)
    manual_path = ROOT / "data" / "manual_qs_2027_top200.csv"
    if manual_path.exists():
        for row in read_csv(manual_path):
            rank = row.get("rank", "")
            upper = rank_upper_bound(rank)
            if upper and (rank_limit is None or upper <= rank_limit):
                entries.append(
                    SourceEntry(
                        name=clean_text(row.get("name")),
                        country=clean_text(row.get("country")),
                        source="QS 2027",
                        source_rank=clean_text(rank),
                    )
                )
    entries = dedupe_source_entries(entries)
    entries.sort(key=lambda e: (rank_upper_bound(e.source_rank) or 9999, e.name))
    return entries


def fetch_qs(refresh: bool = False) -> list[SourceEntry]:
    return fetch_qs_all(refresh=refresh, rank_limit=CANDIDATE_RANK_LIMIT)


def fetch_usnews_all(refresh: bool = False, rank_limit: int | None = None) -> list[SourceEntry]:
    manual_path = ROOT / "data" / "manual_usnews_2026_2027_top200.csv"
    entries: list[SourceEntry] = []
    if manual_path.exists():
        for row in read_csv(manual_path):
            rank = row.get("rank", "")
            upper = rank_upper_bound(rank)
            if upper and (rank_limit is None or upper <= rank_limit):
                entries.append(
                    SourceEntry(
                        name=clean_text(row.get("name")),
                        country=clean_text(row.get("country")),
                        source="US News 2026-2027",
                        source_rank=clean_text(rank),
                    )
                )
    if not entries:
        entries = fetch_usnews_search_api(refresh=refresh, rank_limit=rank_limit)
    if not entries and refresh:
        try:
            html_text = cached_get(USNEWS_URL, "usnews_2026_2027.html", timeout=30, refresh=refresh)
            for m in re.finditer(
                r'(?P<rank>#?\d{1,3})\s*</[^>]+>\s*<[^>]+>\s*(?P<name>[A-Z][^<]{3,120})',
                html_text,
            ):
                rank = clean_text(m.group("rank"))
                name = clean_text(m.group("name"))
                upper = rank_upper_bound(rank)
                if upper and (rank_limit is None or upper <= rank_limit):
                    entries.append(SourceEntry(name=name, country="", source="US News 2026-2027", source_rank=rank))
        except Exception as exc:
            print(f"WARNING: US News fetch failed: {exc}", file=sys.stderr)
    if not entries:
        print(
            "WARNING: US News candidate entries skipped; search JSON endpoint did not return data "
            "and data/manual_usnews_2026_2027_top200.csv has no rows.",
            file=sys.stderr,
        )
    entries = dedupe_source_entries(entries)
    entries.sort(key=lambda e: (rank_upper_bound(e.source_rank) or 9999, e.name))
    return entries


def fetch_usnews(refresh: bool = False) -> list[SourceEntry]:
    return fetch_usnews_all(refresh=refresh, rank_limit=CANDIDATE_RANK_LIMIT)


def cached_usnews_page_numbers() -> list[int]:
    pages: list[int] = []
    for path in RAW.glob("usnews_search_page_*.json"):
        m = re.search(r"usnews_search_page_(\d+)\.json$", path.name)
        if m:
            pages.append(int(m.group(1)))
    return sorted(pages)


def fetch_usnews_search_api(
    refresh: bool = False,
    max_pages: int | None = None,
    rank_limit: int | None = CANDIDATE_RANK_LIMIT,
) -> list[SourceEntry]:
    """Fetch US News global university search JSON pages.

    The endpoint is known to expose `items`, `total_pages`, and paginated ranking
    rows. Some networks block or stall www.usnews.com, so this function is
    intentionally cache-friendly and tolerant of field-name variation.
    """
    entries: list[SourceEntry] = []
    total_pages = max_pages or 300
    cached_pages = cached_usnews_page_numbers() if not refresh and max_pages is None else []
    if cached_pages:
        total_pages = max(cached_pages)
    for page in range(1, total_pages + 1):
        cache_name = f"usnews_search_page_{page}.json"
        if cached_pages and page not in cached_pages:
            break
        cache_path = RAW / cache_name
        used_cache = cache_path.exists() and not refresh
        try:
            data = api_json(
                USNEWS_SEARCH_URL.format(page=page),
                cache_name=cache_name,
                refresh=refresh,
                timeout=30,
            )
        except Exception as exc:
            if page == 1:
                print(f"WARNING: US News search JSON fetch failed: {exc}", file=sys.stderr)
            break
        if page == 1:
            try:
                detected_pages = int(data.get("total_pages", total_pages) or total_pages)
                total_pages = min(detected_pages, max_pages) if max_pages else detected_pages
            except Exception:
                total_pages = max_pages or total_pages
        items = data.get("items") or []
        if not items:
            break
        for item in items:
            parsed = parse_usnews_item(item)
            if not parsed:
                continue
            rank, name, country = parsed
            upper = rank_upper_bound(rank)
            if upper and (rank_limit is None or upper <= rank_limit):
                entries.append(SourceEntry(name=name, country=country, source="US News 2026-2027", source_rank=rank))
        if rank_limit and entries and max(rank_upper_bound(e.source_rank) or 0 for e in entries) >= rank_limit:
            break
        if not used_cache:
            time.sleep(0.5)
    return entries


def parse_usnews_item(item: dict[str, Any]) -> tuple[str, str, str] | None:
    name = clean_text(
        item.get("name")
        or item.get("institution_name")
        or item.get("school_name")
        or item.get("title")
        or item.get("display_name")
    )
    country = clean_text(
        item.get("country")
        or item.get("country_name")
        or item.get("countryName")
        or item.get("location")
        or item.get("region")
    )
    rank = clean_text(
        item.get("rank")
        or item.get("global_rank")
        or item.get("ranking")
        or item.get("display_rank")
        or item.get("rank_display")
    )
    if not rank:
        ranks = item.get("ranks") or item.get("ranking_data") or {}
        if isinstance(ranks, dict):
            rank = clean_text(
                ranks.get("global")
                or ranks.get("best_global_universities")
                or ranks.get("rank")
                or ranks.get("display")
            )
        elif isinstance(ranks, list):
            best = ""
            for entry in ranks:
                if not isinstance(entry, dict):
                    continue
                label = clean_text(entry.get("label")).lower()
                value = clean_text(entry.get("value") or entry.get("rank") or entry.get("display"))
                if value and ("best global universities" in label or not best):
                    best = value
                    if "best global universities" in label:
                        break
            rank = best
        elif isinstance(ranks, list):
            for r in ranks:
                if not isinstance(r, dict):
                    continue
                label = clean_text(r.get("label")).lower()
                if not label or "best global universities" in label or "global" in label:
                    rank = clean_text(r.get("value") or r.get("rank") or r.get("display"))
                    if rank:
                        break
    if not name:
        # US News often nests school information.
        school = item.get("school") or item.get("institution") or {}
        if isinstance(school, dict):
            name = clean_text(school.get("name") or school.get("display_name") or school.get("title"))
            country = country or clean_text(school.get("country") or school.get("country_name"))
    if name and rank_upper_bound(rank):
        return rank, name, country
    return None


def dedupe_source_entries(entries: Iterable[SourceEntry]) -> list[SourceEntry]:
    seen: set[tuple[str, str]] = set()
    out: list[SourceEntry] = []
    for e in entries:
        key = (e.source, norm_key(e.name))
        if key not in seen and e.name:
            seen.add(key)
            out.append(e)
    return out


def committed_reference_entries() -> list[SourceEntry]:
    entries: list[SourceEntry] = []
    paths = [
        PROCESSED / "candidate_pool.csv",
        PROCESSED / "world_universities_research_top200.csv",
        PROCESSED / "world_universities_academic_comprehensive_top200.csv",
    ]
    for path in paths:
        if not path.exists():
            continue
        for row in read_csv(path):
            names = [
                row.get("canonical_name", ""),
                row.get("matched_name", ""),
                row.get("display_name", ""),
            ]
            names.extend(x for x in clean_text(row.get("source_names", "")).split("; ") if x)
            for name in names:
                name = clean_text(name)
                if not name:
                    continue
                for source, field in SOURCE_RANK_FIELDS.items():
                    rank = clean_text(row.get(field, ""))
                    if rank:
                        entries.append(SourceEntry(name=name, country=row.get("country_code", ""), source=source, source_rank=rank))
    return dedupe_source_entries(entries)


def build_reference_rank_index(refresh: bool = False, allow_network: bool = False) -> dict[str, dict[str, str]]:
    """Build a conservative name index for display-only published ranks."""
    source_entries = committed_reference_entries()
    if allow_network:
        source_entries.extend(
            fetch_arwu_all(refresh=refresh, rank_limit=None)
            + fetch_qs_all(refresh=refresh, rank_limit=None)
            + fetch_usnews_all(refresh=refresh, rank_limit=None)
        )
    index: dict[str, dict[str, str]] = {}
    owner: dict[tuple[str, str], str] = {}
    for entry in source_entries:
        field = SOURCE_RANK_FIELDS[entry.source]
        strict_key = strict_rank_key(entry.name)
        if strict_key:
            rec = index.setdefault(strict_key, {})
            rec[field] = better_rank(rec.get(field, ""), entry.source_rank)
            owner[(field, strict_key)] = strict_key
        for key in rank_match_keys(entry.name) - {strict_key}:
            owner_key = (field, key)
            if owner_key in owner and owner[owner_key] != strict_key:
                index.setdefault(key, {})[field] = AMBIGUOUS_RANK
                continue
            owner[owner_key] = strict_key
            rec = index.setdefault(key, {})
            if rec.get(field) == AMBIGUOUS_RANK:
                continue
            rec[field] = better_rank(rec.get(field, ""), entry.source_rank)
    return index


def enrich_reference_ranks(
    rows: list[dict[str, Any]],
    refresh: bool = False,
    index: dict[str, dict[str, str]] | None = None,
    allow_network: bool = False,
) -> list[dict[str, Any]]:
    if index is None:
        index = build_reference_rank_index(refresh=refresh, allow_network=allow_network)
    enriched: list[dict[str, Any]] = []
    for row in rows:
        out = dict(row)
        names = [
            out.get("canonical_name", ""),
            out.get("matched_name", ""),
            out.get("display_name", ""),
        ]
        names.extend(x for x in clean_text(out.get("source_names", "")).split("; ") if x)
        for field in SOURCE_RANK_FIELDNAMES:
            if out.get(field):
                continue
            for name in names:
                for key in rank_match_keys(name):
                    rank = index.get(key, {}).get(field, "")
                    if rank and rank != AMBIGUOUS_RANK:
                        out[field] = rank
                        break
                if out.get(field):
                    break
        enriched.append(out)
    return enriched


def build_candidate_pool(refresh: bool = False) -> list[dict[str, Any]]:
    source_entries = fetch_arwu(refresh=refresh) + fetch_qs(refresh=refresh) + fetch_usnews(refresh=refresh)
    by_key: dict[str, dict[str, Any]] = {}
    for e in source_entries:
        key = norm_key(e.name)
        if key not in by_key:
            by_key[key] = {
                "canonical_name": e.name,
                "country_hint": e.country,
                "source_names": set(),
                "source_ranks": {},
                "in_arwu_2025": False,
                "in_qs_2027": False,
                "in_usnews_2026_2027": False,
            }
        rec = by_key[key]
        if not rec["country_hint"] and e.country:
            rec["country_hint"] = e.country
        rec["source_names"].add(e.name)
        rec["source_ranks"][e.source] = e.source_rank
        if e.source == "ARWU 2025":
            rec["in_arwu_2025"] = True
        elif e.source == "QS 2027":
            rec["in_qs_2027"] = True
        elif e.source == "US News 2026-2027":
            rec["in_usnews_2026_2027"] = True

    rows: list[dict[str, Any]] = []
    for rec in by_key.values():
        rows.append(
            {
                "canonical_name": rec["canonical_name"],
                "country_hint": rec["country_hint"],
                "source_names": "; ".join(sorted(rec["source_names"])),
                "arwu_2025_rank": rec["source_ranks"].get("ARWU 2025", ""),
                "qs_2027_rank": rec["source_ranks"].get("QS 2027", ""),
                "usnews_2026_2027_rank": rec["source_ranks"].get("US News 2026-2027", ""),
                "in_arwu_2025": rec["in_arwu_2025"],
                "in_qs_2027": rec["in_qs_2027"],
                "in_usnews_2026_2027": rec["in_usnews_2026_2027"],
            }
        )
    rows.sort(key=lambda r: (not r["in_arwu_2025"], r["canonical_name"]))
    write_csv(
        PROCESSED / "candidate_pool_unmatched.csv",
        rows,
        [
            "canonical_name",
            "country_hint",
            "source_names",
            "arwu_2025_rank",
            "qs_2027_rank",
            "usnews_2026_2027_rank",
            "in_arwu_2025",
            "in_qs_2027",
            "in_usnews_2026_2027",
        ],
    )
    return rows


def search_openalex_institution(name: str, country: str = "", refresh: bool = False) -> dict[str, Any] | None:
    q = urllib.parse.quote(name)
    url = f"{OPENALEX_BASE}/institutions?search={q}&per-page=5"
    cache_name = f"openalex_search_{re.sub(r'[^a-zA-Z0-9]+', '_', name)[:80]}.json"
    try:
        data = api_json(url, cache_name=cache_name, refresh=refresh)
    except Exception as exc:
        print(f"WARNING: OpenAlex search failed for {name}: {exc}", file=sys.stderr)
        return None
    results = data.get("results", [])
    if not results:
        return None
    country_code = ""
    if country:
        country_code = country_name_to_code(country)
    education = [r for r in results if r.get("type") == "education"]
    pool = education or results
    if country_code:
        same_country = [r for r in pool if r.get("country_code") == country_code]
        if same_country:
            pool = same_country
    return pool[0]


COUNTRY_CODES = {
    "United States": "US",
    "United Kingdom": "GB",
    "China Mainland": "CN",
    "China": "CN",
    "Hong Kong": "HK",
    "Canada": "CA",
    "Australia": "AU",
    "Japan": "JP",
    "Germany": "DE",
    "France": "FR",
    "Switzerland": "CH",
    "Singapore": "SG",
    "Netherlands": "NL",
    "Sweden": "SE",
    "Denmark": "DK",
    "Norway": "NO",
    "Finland": "FI",
    "Belgium": "BE",
    "Italy": "IT",
    "Spain": "ES",
    "South Korea": "KR",
    "Korea": "KR",
    "Israel": "IL",
    "Brazil": "BR",
    "Saudi Arabia": "SA",
    "India": "IN",
    "Austria": "AT",
    "Ireland": "IE",
    "New Zealand": "NZ",
    "Taiwan": "TW",
}


def country_name_to_code(country: str) -> str:
    country = clean_text(country)
    return COUNTRY_CODES.get(country, "")


def match_candidates(refresh: bool = False) -> list[dict[str, Any]]:
    candidate_path = PROCESSED / "candidate_pool_unmatched.csv"
    if not candidate_path.exists():
        build_candidate_pool(refresh=refresh)
    rows = read_csv(candidate_path)
    out: list[dict[str, Any]] = []
    out_path = PROCESSED / "candidate_pool.csv"
    existing: dict[str, dict[str, str]] = {}
    if out_path.exists() and not refresh:
        for old in read_csv(out_path):
            if old.get("openalex_id") or old.get("match_status") == "unmatched":
                existing[norm_key(old.get("canonical_name", ""))] = old
    manual_path = ROOT / "data" / "manual_institution_matches.csv"
    manual: dict[str, dict[str, str]] = {}
    if manual_path.exists():
        for row in read_csv(manual_path):
            manual[norm_key(row.get("canonical_name", ""))] = row
    reference_index = build_reference_rank_index(refresh=refresh, allow_network=True)

    for i, row in enumerate(rows, start=1):
        name = row["canonical_name"]
        if norm_key(name) in existing:
            out.append(existing[norm_key(name)])
            continue
        m = manual.get(norm_key(name))
        inst: dict[str, Any] | None = None
        if m and m.get("openalex_id"):
            url = f"{OPENALEX_BASE}/institutions/{m['openalex_id'].split('/')[-1]}"
            try:
                inst = api_json(url, cache_name=f"openalex_inst_{m['openalex_id'].split('/')[-1]}.json", refresh=refresh)
            except Exception as exc:
                print(f"WARNING: manual OpenAlex ID failed for {name}: {exc}", file=sys.stderr)
        if inst is None:
            inst = search_openalex_institution(name, row.get("country_hint", ""), refresh=refresh)
        if not inst:
            out.append({**row, "openalex_id": "", "ror_id": "", "matched_name": "", "country_code": "", "match_status": "unmatched"})
        else:
            out.append(
                {
                    **row,
                    "openalex_id": inst.get("id", ""),
                    "ror_id": inst.get("ror", ""),
                    "matched_name": inst.get("display_name", ""),
                    "country_code": inst.get("country_code", ""),
                    "match_status": "manual" if m else "auto",
                }
            )
        if i % 25 == 0:
            print(f"matched {i}/{len(rows)}")
            partial = enrich_reference_ranks(out, refresh=refresh, index=reference_index, allow_network=True)
            write_csv(
                out_path,
                partial,
                [
                    "canonical_name",
                    "matched_name",
                    "country_hint",
                    "country_code",
                    "openalex_id",
                    "ror_id",
                    "match_status",
                    "source_names",
                    *SOURCE_RANK_FIELDNAMES,
                    *SOURCE_FLAG_FIELDNAMES,
                ],
            )
        time.sleep(SLEEP_SECONDS)
    out = merge_matched_duplicates(out)
    out = enrich_reference_ranks(out, refresh=refresh, index=reference_index, allow_network=True)
    write_csv(
        out_path,
        out,
        [
            "canonical_name",
            "matched_name",
            "country_hint",
            "country_code",
            "openalex_id",
            "ror_id",
            "match_status",
            "source_names",
            *SOURCE_RANK_FIELDNAMES,
            *SOURCE_FLAG_FIELDNAMES,
        ],
    )
    return out


def merge_matched_duplicates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    no_id: list[dict[str, Any]] = []
    for row in rows:
        oid = row.get("openalex_id", "")
        if not oid:
            no_id.append(row)
            continue
        if oid not in by_id:
            by_id[oid] = dict(row)
            continue
        base = by_id[oid]
        names = sorted(set(filter(None, (base.get("source_names", "") + "; " + row.get("source_names", "")).split("; "))))
        base["source_names"] = "; ".join(names)
        for src in SOURCE_RANK_FIELDNAMES:
            if not base.get(src) and row.get(src):
                base[src] = row[src]
        for flag in SOURCE_FLAG_FIELDNAMES:
            base[flag] = str(base.get(flag)).lower() == "true" or str(row.get(flag)).lower() == "true"
        if len(row.get("canonical_name", "")) > len(base.get("canonical_name", "")):
            # Prefer the fuller source name for display, but keep source_names for provenance.
            base["canonical_name"] = row["canonical_name"]
        if not base.get("country_hint") and row.get("country_hint"):
            base["country_hint"] = row["country_hint"]
    merged = list(by_id.values()) + no_id
    merged.sort(key=lambda r: (r.get("country_code", ""), r.get("canonical_name", "")))
    return merged


def window_by_key(key: str) -> MetricWindow:
    for window in WINDOWS:
        if window.key == key:
            return window
    raise ValueError(f"Unknown window key: {key}")


def iter_windows(selected: str | None = None) -> list[MetricWindow]:
    if not selected:
        return list(WINDOWS)
    keys = [part.strip() for part in selected.split(",") if part.strip()]
    return [window_by_key(key) for key in keys]


def metrics_filename(window: MetricWindow) -> str:
    return f"open_metrics_{window.key}.csv"


def research_filename(window: MetricWindow) -> str:
    return f"world_universities_research_top200_{window.key}.csv"


def comprehensive_filename(window: MetricWindow) -> str:
    return f"world_universities_academic_comprehensive_top200_{window.key}.csv"


def window_cache_name(prefix: str, window: MetricWindow, iid: str) -> str:
    return f"{prefix}_{window.key}_{iid}.json"


def compute_metrics_light(refresh: bool = False, window: MetricWindow | None = None) -> list[dict[str, Any]]:
    window = window or window_by_key(LEGACY_WINDOW_KEY)
    candidate_path = PROCESSED / "candidate_pool.csv"
    if not candidate_path.exists():
        match_candidates(refresh=refresh)
    candidates = read_csv(candidate_path)
    candidates = enrich_reference_ranks(candidates, refresh=refresh)
    out_path = PROCESSED / metrics_filename(window)
    rows: list[dict[str, Any]] = []
    existing: dict[str, dict[str, str]] = {}
    if out_path.exists() and not refresh:
        for old in read_csv(out_path):
            existing[old.get("openalex_id", "")] = old
    for i, row in enumerate(candidates, start=1):
        oid = row.get("openalex_id", "")
        if not oid:
            continue
        if oid in existing:
            cached = dict(existing[oid])
            for field in SOURCE_RANK_FIELDNAMES:
                cached[field] = row.get(field, cached.get(field, ""))
            rows.append(cached)
            continue
        try:
            inst = get_institution_object(oid, refresh=refresh)
        except Exception as exc:
            print(f"WARNING: institution fetch failed for {row.get('canonical_name')}: {exc}", file=sys.stderr)
            continue
        counts = inst.get("counts_by_year") or []
        window_counts = [c for c in counts if window.start <= int(c.get("year", 0) or 0) <= window.end]
        works = sum(int(c.get("works_count", 0) or 0) for c in window_counts)
        oa = sum(int(c.get("oa_works_count", 0) or 0) for c in window_counts)
        citations = sum(int(c.get("cited_by_count", 0) or 0) for c in window_counts)
        topics = inst.get("topics") or []
        field_totals: dict[str, int] = {}
        medicine_topic_count = 0
        for topic in topics:
            count = int(topic.get("count", 0) or 0)
            field = ((topic.get("field") or {}).get("display_name") or "").strip()
            if field:
                field_totals[field] = field_totals.get(field, 0) + count
            topic_text = " ".join(
                clean_text(x)
                for x in [
                    topic.get("display_name"),
                    (topic.get("field") or {}).get("display_name"),
                    (topic.get("subfield") or {}).get("display_name"),
                ]
            ).lower()
            if any(t in topic_text for t in ["medicine", "health", "clinical", "neuroscience", "immunology", "pharmacology"]):
                medicine_topic_count += count
        field_counts = list(field_totals.values())
        entropy = shannon_entropy(field_counts)
        active_fields = len([c for c in field_counts if c >= max(100, sum(field_counts) * 0.03)])
        summary = inst.get("summary_stats") or {}
        h_index = int(summary.get("h_index", 0) or 0)
        mean_2yr = float(summary.get("2yr_mean_citedness", 0) or 0)
        topic_share = inst.get("topic_share") or []
        # Fallback collaboration proxy: high breadth across top topics and OpenAlex's
        # own 2-year mean citedness tend to reward international research networks.
        collab_proxy = min(1.0, (entropy / math.log(26)) * 0.6 + min(mean_2yr / 8.0, 1.0) * 0.4) if entropy else 0.0
        has_medical = detect_medical_school(row, inst) or medicine_topic_count >= max(1000, sum(field_counts) * 0.25)
        rows.append(
            {
                **row,
                "display_name": clean_text(row.get("matched_name")) or clean_text(row.get("canonical_name")),
                "window_key": window.key,
                "window_label": window.label,
                "window_start": window.start,
                "window_end": window.end,
                "works": works,
                "cited_by_proxy": citations,
                "top10_count": citations,
                "top1_count": h_index,
                "top10_share": safe_div(citations, works),
                "top1_share": mean_2yr,
                "h_index": h_index,
                "oa_share": safe_div(oa, works),
                "oa_count": oa,
                "oa_gold_count": "",
                "oa_hybrid_count": "",
                "oa_green_count": "",
                "oa_bronze_count": "",
                "oa_diamond_count": "",
                "closed_count": "",
                "international_collab_proxy_share": collab_proxy,
                "international_collab_count": "",
                "international_collab_share": collab_proxy,
                "core_source_count": "",
                "core_source_share": "",
                "sdg_count": "",
                "sdg_share": "",
                "funder_count": "",
                "field_entropy": entropy,
                "active_fields": active_fields,
                "has_medical_school_or_center": has_medical,
            }
        )
        if i % 25 == 0:
            print(f"light metrics {i}/{len(candidates)}")
            write_csv(out_path, rows, metrics_fieldnames())
    write_csv(out_path, rows, metrics_fieldnames())
    return rows


def openalex_count(
    filters: str,
    group_by: str | None = None,
    cache_name: str | None = None,
    refresh: bool = False,
    per_page: int = 1,
) -> Any:
    params = {"filter": filters, "per-page": str(per_page)}
    if group_by:
        params["group_by"] = group_by
    url = f"{OPENALEX_BASE}/works?{urllib.parse.urlencode(params)}"
    data = api_json(url, cache_name=cache_name, refresh=refresh, timeout=90)
    if group_by:
        return data.get("group_by", [])
    return data.get("meta", {}).get("count", 0)


def get_institution_object(openalex_id: str, refresh: bool = False) -> dict[str, Any]:
    iid = openalex_id.rstrip("/").split("/")[-1]
    return api_json(f"{OPENALEX_BASE}/institutions/{iid}", cache_name=f"openalex_inst_{iid}.json", refresh=refresh)


def build_base_filter(openalex_id: str, window: MetricWindow) -> str:
    iid = openalex_id.rstrip("/").split("/")[-1]
    return (
        f"authorships.institutions.lineage:{iid},"
        f"from_publication_date:{window.start}-01-01,"
        f"to_publication_date:{window.end}-12-31,"
        f"type:{OPENALEX_WORK_TYPE_FILTER}"
    )


def group_count(groups: list[dict[str, Any]], key: str) -> int:
    for group in groups:
        if str(group.get("key", "")) == key:
            return int(group.get("count", 0) or 0)
    return 0


def count_any_group(groups: list[dict[str, Any]]) -> int:
    return sum(int(group.get("count", 0) or 0) for group in groups if group.get("key"))


def metrics_fieldnames() -> list[str]:
    return [
        "canonical_name",
        "display_name",
        "matched_name",
        "source_names",
        "country_code",
        "openalex_id",
        "ror_id",
        "window_key",
        "window_label",
        "window_start",
        "window_end",
        "works",
        "cited_by_proxy",
        "top10_count",
        "top1_count",
        "top10_share",
        "top1_share",
        "h_index",
        "field_entropy",
        "active_fields",
        "international_collab_count",
        "international_collab_share",
        "core_source_count",
        "core_source_share",
        "oa_count",
        "oa_share",
        "oa_gold_count",
        "oa_hybrid_count",
        "oa_green_count",
        "oa_bronze_count",
        "oa_diamond_count",
        "closed_count",
        "sdg_count",
        "sdg_share",
        "funder_count",
        "has_medical_school_or_center",
        *SOURCE_RANK_FIELDNAMES,
    ]


def enrich_candidate_metadata(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidate_path = PROCESSED / "candidate_pool.csv"
    if not candidate_path.exists():
        return rows
    by_id = {row.get("openalex_id", ""): row for row in read_csv(candidate_path) if row.get("openalex_id")}
    enriched: list[dict[str, Any]] = []
    for row in rows:
        out = dict(row)
        candidate = by_id.get(str(out.get("openalex_id", "")), {})
        if candidate:
            for field in ["source_names", "canonical_name", "matched_name", "country_code", "ror_id"]:
                if not out.get(field) and candidate.get(field):
                    out[field] = candidate[field]
            for field in SOURCE_RANK_FIELDNAMES:
                if not out.get(field) and candidate.get(field):
                    out[field] = candidate[field]
        enriched.append(out)
    return enriched


def compute_metrics(
    refresh: bool = False,
    limit: int | None = None,
    force: bool = False,
    window: MetricWindow | None = None,
) -> list[dict[str, Any]]:
    window = window or window_by_key(LEGACY_WINDOW_KEY)
    candidate_path = PROCESSED / "candidate_pool.csv"
    if not candidate_path.exists():
        match_candidates(refresh=refresh)
    candidates = read_csv(candidate_path)
    candidates = enrich_reference_ranks(candidates, refresh=refresh)
    if limit:
        candidates = candidates[:limit]
    expected_rows = sum(1 for row in candidates if row.get("openalex_id"))
    out_path = PROCESSED / metrics_filename(window)
    existing: dict[str, dict[str, str]] = {}
    if out_path.exists() and not refresh and not force:
        for old in read_csv(out_path):
            if old.get("openalex_id") and old.get("core_source_share"):
                existing[old["openalex_id"]] = old
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for i, row in enumerate(candidates, start=1):
        oid = row.get("openalex_id", "")
        if not oid:
            continue
        if oid in existing:
            cached = dict(existing[oid])
            for field in SOURCE_RANK_FIELDNAMES:
                cached[field] = row.get(field, cached.get(field, ""))
            cached["source_names"] = row.get("source_names", cached.get("source_names", ""))
            rows.append(cached)
            continue
        iid = oid.rstrip("/").split("/")[-1]
        base = build_base_filter(oid, window)
        try:
            inst = get_institution_object(oid, refresh=refresh)
            works = openalex_count(base, cache_name=window_cache_name("works_count", window, iid), refresh=refresh)
            top10 = openalex_count(
                base + ",citation_normalized_percentile.is_in_top_10_percent:true",
                cache_name=window_cache_name("top10_count", window, iid),
                refresh=refresh,
            )
            top1 = openalex_count(
                base + ",citation_normalized_percentile.is_in_top_1_percent:true",
                cache_name=window_cache_name("top1_count", window, iid),
                refresh=refresh,
            )
            international = openalex_count(
                base + ",countries_distinct_count:>1",
                cache_name=window_cache_name("intl_count", window, iid),
                refresh=refresh,
            )
            core_source = openalex_count(
                base + ",primary_location.source.is_core:true",
                cache_name=window_cache_name("core_source_count", window, iid),
                refresh=refresh,
            )
            oa_groups = openalex_count(
                base,
                group_by="open_access.oa_status",
                cache_name=window_cache_name("oa_status_groups", window, iid),
                refresh=refresh,
                per_page=20,
            )
            fields = openalex_count(
                base,
                group_by="primary_topic.field.id",
                cache_name=window_cache_name("field_groups", window, iid),
                refresh=refresh,
                per_page=200,
            )
            field_counts = [int(g.get("count", 0) or 0) for g in fields if g.get("key")]
            entropy = shannon_entropy(field_counts)
            active_fields = sum(1 for c in field_counts if c >= max(100, works * 0.01))
            sdg_groups = openalex_count(
                base,
                group_by="sustainable_development_goals.id",
                cache_name=window_cache_name("sdg_groups", window, iid),
                refresh=refresh,
                per_page=200,
            )
            funder_groups = openalex_count(
                base,
                group_by="funders.id",
                cache_name=window_cache_name("funder_groups", window, iid),
                refresh=refresh,
                per_page=200,
            )
            summary = inst.get("summary_stats") or {}
            h_index = int(summary.get("h_index", 0) or 0)
            has_medical = detect_medical_school(row, inst)
            oa_gold = group_count(oa_groups, "gold")
            oa_hybrid = group_count(oa_groups, "hybrid")
            oa_green = group_count(oa_groups, "green")
            oa_bronze = group_count(oa_groups, "bronze")
            oa_diamond = group_count(oa_groups, "diamond")
            closed = group_count(oa_groups, "closed")
            oa = max(0, works - closed)
            sdg_count = count_any_group(sdg_groups)
            funder_count = len([g for g in funder_groups if g.get("key")])
            rows.append(
                {
                    **row,
                    "display_name": clean_text(row.get("matched_name")) or clean_text(row.get("canonical_name")),
                    "window_key": window.key,
                    "window_label": window.label,
                    "window_start": window.start,
                    "window_end": window.end,
                    "works": works,
                    "cited_by_proxy": top10,
                    "top10_count": top10,
                    "top1_count": top1,
                    "top10_share": safe_div(top10, works),
                    "top1_share": safe_div(top1, works),
                    "h_index": h_index,
                    "oa_count": oa,
                    "oa_share": safe_div(oa, works),
                    "oa_gold_count": oa_gold,
                    "oa_hybrid_count": oa_hybrid,
                    "oa_green_count": oa_green,
                    "oa_bronze_count": oa_bronze,
                    "oa_diamond_count": oa_diamond,
                    "closed_count": closed,
                    "international_collab_count": international,
                    "international_collab_share": safe_div(international, works),
                    "international_collab_proxy_share": safe_div(international, works),
                    "core_source_count": core_source,
                    "core_source_share": safe_div(core_source, works),
                    "sdg_count": sdg_count,
                    "sdg_share": safe_div(sdg_count, works),
                    "funder_count": funder_count,
                    "field_entropy": entropy,
                    "active_fields": active_fields,
                    "has_medical_school_or_center": has_medical,
                }
            )
        except Exception as exc:
            name = safe_log_text(row.get("canonical_name"))
            failures.append(f"{name}: {exc}")
            print(f"WARNING: metrics failed for {name}: {exc}", file=sys.stderr)
        print(f"metrics {window.key} {i}/{len(candidates)} {safe_log_text(row.get('canonical_name'))}")
        if i % 10 == 0:
            write_csv(out_path, rows, metrics_fieldnames())
        time.sleep(SLEEP_SECONDS)
    write_csv(out_path, rows, metrics_fieldnames())
    if len(rows) != expected_rows:
        sample = "; ".join(failures[:8])
        raise RuntimeError(
            f"metrics window {window.key} incomplete: wrote {len(rows)} rows, expected {expected_rows}. "
            f"Failures: {sample}"
        )
    return rows


def detect_medical_school(row: dict[str, Any], inst: dict[str, Any]) -> bool:
    text = " ".join(
        clean_text(x)
        for x in [
            row.get("canonical_name"),
            row.get("matched_name"),
            inst.get("display_name"),
            inst.get("homepage_url"),
        ]
    ).lower()
    medical_terms = [
        "medical",
        "medicine",
        "health",
        "hospital",
        "klinikum",
        "clinic",
        "karolinska",
        "ucsf",
        "mayo",
    ]
    if any(term in text for term in medical_terms):
        return True
    name = clean_text(row.get("canonical_name")).lower()
    known_with_medicine = {
        "harvard university",
        "stanford university",
        "johns hopkins university",
        "university of pennsylvania",
        "university of california, san francisco",
        "university of washington",
        "university of toronto",
        "university of oxford",
        "university of cambridge",
        "university college london",
        "imperial college london",
        "yale university",
        "columbia university",
        "cornell university",
        "duke university",
        "university of michigan-ann arbor",
        "university of california, los angeles",
        "university of california, san diego",
    }
    return name in known_with_medicine


def shannon_entropy(counts: list[int]) -> float:
    total = sum(counts)
    if total <= 0:
        return 0.0
    return -sum((c / total) * math.log(c / total) for c in counts if c > 0)


def safe_div(a: Any, b: Any) -> float:
    try:
        b = float(b)
        if b == 0:
            return 0.0
        return float(a) / b
    except Exception:
        return 0.0


def to_float(row: dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key, 0) or 0)
    except Exception:
        return 0.0


def winsorized_scores(rows: list[dict[str, Any]], key: str, *, log: bool = False) -> dict[int, float]:
    values: list[float] = []
    for row in rows:
        v = to_float(row, key)
        if log:
            v = math.log1p(max(0, v))
        values.append(v)
    if not values:
        return {}
    lo = percentile(values, 2.5)
    hi = percentile(values, 97.5)
    if hi <= lo:
        return {i: 50.0 for i in range(len(rows))}
    scores: dict[int, float] = {}
    for i, v in enumerate(values):
        v = min(max(v, lo), hi)
        scores[i] = (v - lo) / (hi - lo) * 100
    return scores


def percentile(values: list[float], p: float) -> float:
    xs = sorted(values)
    if not xs:
        return 0.0
    k = (len(xs) - 1) * p / 100.0
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return xs[int(k)]
    return xs[f] * (c - k) + xs[c] * (k - f)


def score_rankings(window: MetricWindow | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    window = window or window_by_key(LEGACY_WINDOW_KEY)
    metrics_path = PROCESSED / metrics_filename(window)
    if not metrics_path.exists() and window.key == LEGACY_WINDOW_KEY:
        metrics_path = PROCESSED / "open_metrics.csv"
    rows = read_csv(metrics_path)
    for row in rows:
        if not row.get("works") and row.get("works_2020_2024"):
            row["works"] = row["works_2020_2024"]
            row["window_key"] = window.key
            row["window_label"] = window.label
            row["window_start"] = window.start
            row["window_end"] = window.end
    rows = enrich_candidate_metadata(rows)
    rows = enrich_reference_ranks(rows)
    score_cols: dict[str, dict[int, float]] = {}
    for spec in METRIC_SPECS:
        score_cols[spec["score"]] = winsorized_scores(rows, spec["source"], log=bool(spec["log"]))

    scored: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        enriched = dict(row)
        details: list[dict[str, Any]] = []
        research_score = 0.0
        comprehensive_score = 0.0
        for spec in METRIC_SPECS:
            score = score_cols[spec["score"]].get(i, 0.0)
            enriched[spec["score"]] = score
            research_score += float(spec["research_weight"]) * score
            comprehensive_score += float(spec["comprehensive_weight"]) * score
            details.append(
                {
                    "key": spec["key"],
                    "label": spec["label"],
                    "description": spec["description"],
                    "source": spec["source"],
                    "format": spec["format"],
                    "value": row.get(str(spec["source"]), ""),
                    "score": f"{score:.2f}",
                    "research_weight": f"{float(spec['research_weight']):.4f}",
                    "comprehensive_weight": f"{float(spec['comprehensive_weight']):.4f}",
                }
            )
        enriched["research_score"] = research_score
        enriched["academic_comprehensive_score"] = comprehensive_score
        enriched["metric_details_json"] = json.dumps(details, ensure_ascii=False, separators=(",", ":"))
        scored.append(enriched)

    research = sorted(
        scored,
        key=lambda r: (
            -r["research_score"],
            -r["top10_volume_score"],
            -r["top1_volume_score"],
            r["canonical_name"],
        ),
    )
    comprehensive = sorted(
        scored,
        key=lambda r: (
            -r["academic_comprehensive_score"],
            -r["top10_volume_score"],
            -r["top1_volume_score"],
            r["canonical_name"],
        ),
    )
    research = add_rank(research, "research_score")[:200]
    comprehensive = add_rank(comprehensive, "academic_comprehensive_score")[:200]
    out_fields = [
        "rank",
        "display_name",
        "canonical_name",
        "matched_name",
        "source_names",
        "country_code",
        "score",
        "window_key",
        "window_label",
        "window_start",
        "window_end",
        "works",
        "top10_count",
        "top1_count",
        "top10_share",
        "top1_share",
        "h_index",
        "field_entropy",
        "active_fields",
        "international_collab_count",
        "international_collab_share",
        "core_source_count",
        "core_source_share",
        "oa_count",
        "oa_share",
        "oa_gold_count",
        "oa_hybrid_count",
        "oa_green_count",
        "oa_bronze_count",
        "oa_diamond_count",
        "closed_count",
        "sdg_count",
        "sdg_share",
        "funder_count",
        *[spec["score"] for spec in METRIC_SPECS],
        "metric_details_json",
        "has_medical_school_or_center",
        "openalex_id",
        "ror_id",
        *SOURCE_RANK_FIELDNAMES,
    ]
    write_csv(PROCESSED / research_filename(window), research, out_fields)
    write_csv(PROCESSED / comprehensive_filename(window), comprehensive, out_fields)
    if window.key == LEGACY_WINDOW_KEY:
        write_csv(PROCESSED / "open_metrics.csv", rows, metrics_fieldnames())
        write_csv(PROCESSED / "world_universities_research_top200.csv", research, out_fields)
        write_csv(PROCESSED / "world_universities_academic_comprehensive_top200.csv", comprehensive, out_fields)
    return research, comprehensive


def add_rank(rows: list[dict[str, Any]], score_key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rank, row in enumerate(rows, start=1):
        r = dict(row)
        r["rank"] = rank
        r["score"] = f"{float(r[score_key]):.2f}"
        for key in [
            "top10_share",
            "top1_share",
            "oa_share",
            "international_collab_share",
            "international_collab_proxy_share",
            "core_source_share",
            "sdg_share",
            "field_entropy",
            *[spec["score"] for spec in METRIC_SPECS],
        ]:
            if key in r:
                r[key] = f"{to_float(r, key):.6f}"
        out.append(r)
    return out


def write_methodology() -> None:
    metric_lines = "\n".join(
        f"- {spec['label']}: {spec['description']} "
        f"Research weight {float(spec['research_weight']) * 100:.0f}%; "
        f"Academic comprehensive weight {float(spec['comprehensive_weight']) * 100:.0f}%."
        for spec in METRIC_SPECS
    )
    text = f"""# Open-Data World University Ranking Methodology

Generated on 2026-06-18.

## Candidate pool

The candidate pool is intended to be the union of the top 200 institutions in:

- US News Best Global Universities 2026-2027
- QS World University Rankings 2027
- ARWU 2025

Commercial ranking positions and scores are used only for candidate-pool inclusion and display provenance. They are not used in final scoring.

Current generated files use the ARWU 2025 top 200, QS 2027 top 200, and US News 2026-2027 top 200 to define the candidate pool. The `Published Rankings` display column is then filled from the fullest available ARWU/QS/US News source files or cached JSON pages, so a candidate can show ranks from rankings where it appears outside the top 200.

US News was retrieved from the search JSON endpoint (`/education/best-global-universities/search?format=json&page=N`) after first establishing a browser session with `www.usnews.com`. The script reuses any cached `data/raw/usnews_search_page_N.json` files for display-only published ranks; missing pages simply leave unmatched US News reference badges blank. If the endpoint is blocked in a future run, cache more pages via browser or add `data/manual_usnews_2026_2027_top200.csv` with columns `rank,name,country`, then rerun:

```powershell
python .\\scripts\\build_rankings.py --candidate-pool --match --metrics --score --window 2020_2024
```

## Institution unit

Institutions are matched to ROR/OpenAlex IDs. Affiliated medical schools are not split from their parent universities. Independent medical universities or independent campuses are kept as separate institutions if they appear as such in the source candidate pool. The output includes a `has_medical_school_or_center` flag.

## Open data indicators

Final scores use OpenAlex/ROR data only. Google Scholar is excluded because it has no stable public API, institutional pages are affected by profile coverage, and bulk automated access is not a suitable reproducible pipeline.

Generated publication windows:

- Default stable view: 5-year 2021-2025.
- Legacy stable view: 5-year 2020-2024.
- Annual trend snapshots: 2020, 2021, 2022, 2023, 2024, and 2025.

Included OpenAlex work types: `{WORK_TYPES}`. Works are counted through `authorships.institutions.lineage`, so child institutions and known institutional lineage are included.

Annual snapshots are intended as trend and momentum views. They are more volatile than five-year views, and the latest annual snapshot can be affected by OpenAlex indexing lag.

The current generated version uses work-level OpenAlex API aggregates where possible:

- Total works: all included works in the publication window.
- Top 10% and top 1% papers: OpenAlex `citation_normalized_percentile.is_in_top_10_percent` and `citation_normalized_percentile.is_in_top_1_percent`.
- International collaboration: `countries_distinct_count:>1`.
- Core-source share: `primary_location.source.is_core:true`.
- Open access share and OA status breakdown: `open_access.oa_status`.
- Field breadth: Shannon entropy over `primary_topic.field.id`, plus active-field count.
- SDG-linked research: works grouped by `sustainable_development_goals.id`.
- Funder diversity: distinct `funders.id` groups observed in the work metadata.
- Long-run influence: OpenAlex institution `summary_stats.h_index`. This h-index is all-time and does not vary by publication window.

Indicators are winsorized at the 2.5th and 97.5th percentiles within the candidate pool, then mapped to 0-100. Volume indicators use `log1p` before winsorization. Each row in the web table exposes the raw metric value, normalized score, and weight.

## Indicators and weights

{metric_lines}

## Current caveats

- US News is wired to the `search?format=json&page=N` endpoint; in this environment it required first loading `https://www.usnews.com/` in a browser session, then caching the JSON pages.
- OpenAlex SDG and funder metadata are useful open indicators but are not complete for all fields and countries.
- OpenAlex coverage and institution lineage are transparent and reproducible, but not identical to Web of Science, Scopus, Google Scholar, QS, ARWU, or US News bibliometric universes.
"""
    (ROOT / "docs" / "methodology.md").write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--candidate-pool", action="store_true")
    parser.add_argument("--match", action="store_true")
    parser.add_argument("--metrics", action="store_true")
    parser.add_argument("--metrics-heavy", action="store_true")
    parser.add_argument("--force-metrics", action="store_true")
    parser.add_argument("--score", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--window",
        default=None,
        help="Comma-separated window keys to build, e.g. 2021_2025,2025. Defaults to all configured windows.",
    )
    args = parser.parse_args()

    ensure_dirs()
    selected_windows = iter_windows(args.window)
    if args.candidate_pool:
        rows = build_candidate_pool(refresh=args.refresh)
        print(f"candidate rows: {len(rows)}")
    if args.match:
        rows = match_candidates(refresh=args.refresh)
        print(f"matched rows: {len(rows)}")
    if args.metrics:
        for window in selected_windows:
            rows = compute_metrics_light(refresh=args.refresh, window=window)
            print(f"metric rows {window.key}: {len(rows)}")
    if args.metrics_heavy:
        for window in selected_windows:
            rows = compute_metrics(refresh=args.refresh, limit=args.limit, force=args.force_metrics, window=window)
            print(f"metric rows {window.key}: {len(rows)}")
    if args.score:
        for window in selected_windows:
            research, comprehensive = score_rankings(window=window)
            print(f"research rows {window.key}: {len(research)} comprehensive rows: {len(comprehensive)}")
    write_methodology()


if __name__ == "__main__":
    main()
