#!/usr/bin/env python3
"""Build a static GitHub Pages site from processed ranking CSVs."""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
SITE = ROOT / "_site"

METRIC_CATALOG = [
    {"key": "scale", "label": "Publication scale", "description": "OpenAlex works from 2020-2024."},
    {"key": "top10_volume", "label": "Top 10% papers", "description": "Field/year-normalized top 10% cited works."},
    {"key": "top1_volume", "label": "Top 1% papers", "description": "Field/year-normalized top 1% cited works."},
    {"key": "top10_rate", "label": "Top 10% share", "description": "Top 10% works divided by total works."},
    {"key": "top1_rate", "label": "Top 1% share", "description": "Top 1% works divided by total works."},
    {"key": "h_index", "label": "Institution h-index", "description": "OpenAlex institution h-index."},
    {"key": "field_breadth", "label": "Field breadth", "description": "Shannon entropy across OpenAlex fields."},
    {"key": "active_fields", "label": "Active fields", "description": "Fields with meaningful publication volume."},
    {"key": "international_collab", "label": "International collaboration", "description": "Works with affiliations from more than one country."},
    {"key": "core_source", "label": "Core-source share", "description": "Works whose primary source is marked core by OpenAlex."},
    {"key": "open_access", "label": "Open access share", "description": "OpenAlex open-access works divided by total works."},
    {"key": "sdg", "label": "SDG-linked research", "description": "Works tagged to at least one UN Sustainable Development Goal."},
    {"key": "funder_diversity", "label": "Funder diversity", "description": "Distinct OpenAlex funders observed in work metadata."},
]


def read_csv(name: str) -> list[dict[str, str]]:
    with (PROCESSED / name).open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_data() -> dict[str, object]:
    research = read_csv("world_universities_research_top200.csv")
    comprehensive = read_csv("world_universities_academic_comprehensive_top200.csv")
    candidates = read_csv("candidate_pool.csv")
    metrics = read_csv("open_metrics.csv")
    metric_by_id = {row["openalex_id"]: row for row in metrics}

    def enrich(row: dict[str, str]) -> dict[str, object]:
        metric = metric_by_id.get(row["openalex_id"], {})
        aliases = []
        for name in [row.get("canonical_name", ""), row.get("matched_name", ""), row.get("source_names", "")]:
            for part in str(name).split("; "):
                part = part.strip()
                if part and part not in aliases and part != row["display_name"]:
                    aliases.append(part)
        try:
            metric_details = json.loads(row.get("metric_details_json") or metric.get("metric_details_json") or "[]")
        except json.JSONDecodeError:
            metric_details = []
        return {
            "rank": int(row["rank"]),
            "name": row["display_name"],
            "aliases": aliases,
            "country": row["country_code"],
            "score": float(row["score"]),
            "medical": row["has_medical_school_or_center"].lower() == "true",
            "openalex": row["openalex_id"],
            "ror": row["ror_id"],
            "publishedRankings": {
                "arwu2025": row["arwu_2025_rank"],
                "qs2027": row["qs_2027_rank"],
                "usnews2026_2027": row["usnews_2026_2027_rank"],
            },
            "metrics": {
                "works2020_2024": int(float(row["works_2020_2024"] or 0)),
                "hIndex": int(float(row["h_index"] or 0)),
                "fieldEntropy": round(float(row["field_entropy"] or 0), 3),
                "activeFields": int(float(row["active_fields"] or 0)),
                "openAccessShare": round(float(row["oa_share"] or 0), 4),
                "internationalCollabShare": round(float(row.get("international_collab_share") or 0), 4),
                "coreSourceShare": round(float(row.get("core_source_share") or 0), 4),
                "top10Share": round(float(row["top10_share"] or 0), 4),
                "top1Share": round(float(row["top1_share"] or 0), 4),
                "sdgShare": round(float(row.get("sdg_share") or 0), 4),
                "funderCount": int(float(row.get("funder_count") or 0)),
                "citationProxy": int(float(metric.get("cited_by_proxy", 0) or 0)),
            },
            "metricDetails": metric_details,
        }

    return {
        "generated": "2026-06-18",
        "summary": {
            "candidatePool": len(candidates),
            "arwu": sum(row["in_arwu_2025"].lower() == "true" for row in candidates),
            "qs": sum(row["in_qs_2027"].lower() == "true" for row in candidates),
            "usnews": sum(row["in_usnews_2026_2027"].lower() == "true" for row in candidates),
        },
        "method": {
            "window": "2020-2024",
            "workTypes": "article, review, book, book chapter",
            "normalization": "Each indicator is winsorized at the 2.5th and 97.5th percentiles within the candidate pool, then mapped to 0-100. Volume indicators use log1p before scaling.",
            "catalog": METRIC_CATALOG,
        },
        "rankings": {
            "research": [enrich(row) for row in research],
            "comprehensive": [enrich(row) for row in comprehensive],
        },
    }


def main() -> None:
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "assets").mkdir(parents=True)
    (SITE / "data").mkdir(parents=True)

    data = build_data()
    write(SITE / "data" / "rankings.json", json.dumps(data, ensure_ascii=False, indent=2))
    write(SITE / "index.html", INDEX_HTML)
    write(SITE / "assets" / "app.css", APP_CSS)
    write(SITE / "assets" / "app.js", APP_JS)


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Open Global University Rankings</title>
  <meta name="description" content="Open-data world university rankings scored with reproducible OpenAlex/ROR indicators.">
  <link rel="stylesheet" href="assets/app.css">
</head>
<body>
  <header class="site-header">
    <div>
      <p class="eyebrow">Open data · reproducible methods</p>
      <h1>Open Global University Rankings</h1>
      <p class="lede">Two independent world university rankings built from QS, US News, and ARWU candidate pools, then scored with OpenAlex/ROR indicators.</p>
    </div>
    <nav class="links">
      <a href="https://github.com/anzupop/open-global-university-rankings">GitHub</a>
      <a href="data/rankings.json">JSON</a>
    </nav>
  </header>

  <main>
    <section class="summary" id="summary"></section>

    <section class="method" id="method"></section>

    <section class="controls" aria-label="Ranking controls">
      <div class="tabs" role="tablist">
        <button class="tab active" data-ranking="research" type="button">Research</button>
        <button class="tab" data-ranking="comprehensive" type="button">Academic Comprehensive</button>
      </div>
      <div class="filters">
        <input id="search" type="search" placeholder="Search university or country">
        <select id="country">
          <option value="">All countries</option>
        </select>
      </div>
    </section>

    <section class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Rank</th>
            <th>University</th>
            <th>Country</th>
            <th>Score</th>
            <th>Works</th>
            <th>h-index</th>
            <th>Published Rankings</th>
          </tr>
        </thead>
        <tbody id="ranking-body">
          <tr class="loading-row"><td colspan="7">Loading rankings...</td></tr>
        </tbody>
      </table>
    </section>
  </main>

  <div class="modal-backdrop" id="score-modal" hidden>
    <section class="modal" role="dialog" aria-modal="true" aria-labelledby="score-modal-title">
      <button class="modal-close" id="score-modal-close" type="button" aria-label="Close score details">&times;</button>
      <p class="modal-kicker">Score Details</p>
      <h2 id="score-modal-title"></h2>
      <p class="modal-meta" id="score-modal-meta"></p>
      <div class="metrics-grid modal-metrics" id="score-modal-body"></div>
    </section>
  </div>

  <script src="assets/app.js"></script>
</body>
</html>
"""


APP_CSS = """:root {
  color-scheme: light;
  --bg: #f7f8fb;
  --ink: #172033;
  --muted: #667085;
  --line: #d8dee9;
  --panel: #ffffff;
  --accent: #0f766e;
  --accent-2: #4257c9;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

.site-header {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  padding: 48px clamp(20px, 5vw, 72px) 28px;
  background: #101828;
  color: #fff;
}

.eyebrow {
  margin: 0 0 10px;
  color: #9ee5dc;
  font-weight: 700;
  text-transform: uppercase;
  font-size: 12px;
}

h1 {
  margin: 0;
  font-size: clamp(34px, 5vw, 64px);
  line-height: 1.02;
  letter-spacing: 0;
}

.lede {
  max-width: 760px;
  color: #d0d5dd;
  font-size: 18px;
}

.links {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

a { color: var(--accent-2); }
.site-header a {
  color: #fff;
  border: 1px solid rgba(255,255,255,.35);
  padding: 8px 12px;
  text-decoration: none;
}

main { padding: 24px clamp(16px, 4vw, 56px) 56px; }

.summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(130px, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}

.stat, .method, .controls, .table-wrap {
  background: var(--panel);
  border: 1px solid var(--line);
}

.stat { padding: 14px 16px; }
.stat b { display: block; font-size: 26px; }
.stat span { color: var(--muted); }

.method {
  margin-bottom: 16px;
  padding: 18px;
}
.method h2 { margin: 0 0 8px; }
.method p { max-width: 980px; }
.method-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(180px, 1fr));
  gap: 10px 16px;
  margin-top: 12px;
}
.method-item b { display: block; }
.method-item span { color: var(--muted); font-size: 13px; }

.controls {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 14px;
  margin-bottom: 16px;
}

.tabs, .filters { display: flex; gap: 8px; flex-wrap: wrap; }
.tab, input, select {
  height: 38px;
  border: 1px solid var(--line);
  background: #fff;
  color: var(--ink);
  padding: 0 12px;
  font: inherit;
}
.tab.active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
input { min-width: 260px; }

.table-wrap { overflow: auto; }
table { border-collapse: collapse; width: 100%; min-width: 1120px; }
th, td {
  border-bottom: 1px solid var(--line);
  padding: 10px 12px;
  text-align: left;
  vertical-align: top;
}
th {
  position: sticky;
  top: 0;
  background: #eef2f7;
  font-size: 12px;
  text-transform: uppercase;
}
.rank { font-weight: 800; }
.uni { font-weight: 700; }
.uni-button {
  border: 0;
  background: transparent;
  color: var(--ink);
  cursor: pointer;
  font: inherit;
  font-weight: 700;
  padding: 0;
  text-align: left;
}
.uni-button:hover { color: var(--accent); text-decoration: underline; }
.uni-button:focus-visible { outline: 1px solid var(--accent); outline-offset: 2px; }
.sub { color: var(--muted); font-size: 12px; }
.aliases { margin-top: 2px; color: var(--muted); font-size: 12px; max-width: 420px; }
.pill {
  display: inline-block;
  margin: 2px 4px 2px 0;
  padding: 2px 6px;
  border: 1px solid var(--line);
  color: var(--muted);
  font-size: 12px;
}
.loading-row td { color: var(--muted); padding: 28px 12px; text-align: center; }
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(180px, 1fr));
  gap: 8px 14px;
}
.metric-line {
  border-top: 1px solid var(--line);
  padding-top: 8px;
}
.metric-line b { display: block; font-size: 13px; }
.metric-line span { color: var(--muted); font-size: 12px; }
.modal-open { overflow: hidden; }
.modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(16, 24, 40, .48);
  padding: 24px;
}
.modal-backdrop[hidden] { display: none; }
.modal {
  position: relative;
  width: min(1040px, 100%);
  max-height: min(86vh, 900px);
  overflow: auto;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: 0 24px 80px rgba(16, 24, 40, .24);
  padding: 22px;
}
.modal-close {
  position: absolute;
  top: 14px;
  right: 14px;
  width: 32px;
  height: 32px;
  border: 1px solid var(--line);
  background: #fff;
  color: var(--ink);
  cursor: pointer;
  font: inherit;
  font-size: 22px;
  line-height: 1;
}
.modal-close:focus-visible { outline: 1px solid var(--accent); outline-offset: 2px; }
.modal-kicker {
  margin: 0 0 6px;
  color: var(--accent);
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
}
.modal h2 { margin: 0; padding-right: 44px; }
.modal-meta { margin: 6px 0 0; color: var(--muted); }
.modal-metrics { margin-top: 18px; }

@media (max-width: 760px) {
  .site-header, .controls { flex-direction: column; }
  .summary { grid-template-columns: repeat(2, 1fr); }
  .method-grid, .metrics-grid { grid-template-columns: 1fr; }
  input { width: 100%; min-width: 0; }
}
"""


APP_JS = """let payload;
let ranking = "research";
let lastScoreButton;

const body = document.getElementById("ranking-body");
const search = document.getElementById("search");
const country = document.getElementById("country");
const modal = document.getElementById("score-modal");
const modalTitle = document.getElementById("score-modal-title");
const modalMeta = document.getElementById("score-modal-meta");
const modalBody = document.getElementById("score-modal-body");
const modalClose = document.getElementById("score-modal-close");

fetch("data/rankings.json")
  .then((r) => r.json())
  .then((data) => {
    payload = data;
    renderSummary(data.summary);
    renderMethod(data.method);
    populateCountries();
    render();
  })
  .catch(() => {
    body.innerHTML = `<tr class="loading-row"><td colspan="7">Unable to load rankings.</td></tr>`;
  });

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    button.classList.add("active");
    ranking = button.dataset.ranking;
    render();
  });
});

[search, country].forEach((el) => el.addEventListener("input", render));

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !modal.hidden) {
    closeScoreModal();
    return;
  }
  if (!modal.hidden) return;
  if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return;
  const target = event.target;
  if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
  event.preventDefault();
  search.focus();
  search.select();
});

body.addEventListener("click", (event) => {
  const button = event.target.closest("[data-score-details]");
  if (!button) return;
  const row = findCurrentRows().find((item) => item.openalex === button.dataset.scoreDetails);
  if (row) {
    lastScoreButton = button;
    openScoreModal(row);
  }
});

modal.addEventListener("click", (event) => {
  if (event.target === modal) closeScoreModal();
});

modalClose.addEventListener("click", closeScoreModal);

function renderSummary(summary) {
  document.getElementById("summary").innerHTML = [
    ["Candidate Pool", summary.candidatePool],
    ["ARWU 2025", summary.arwu],
    ["QS 2027", summary.qs],
    ["US News 2026-2027", summary.usnews],
  ].map(([label, value]) => `<div class="stat"><b>${value}</b><span>${label}</span></div>`).join("");
}

function renderMethod(method) {
  const catalog = (method && method.catalog ? method.catalog : []).slice(0, 6);
  document.getElementById("method").innerHTML = `
    <h2>Method</h2>
    <p>The three published rankings define only the candidate pool. Final scores use open OpenAlex/ROR indicators over ${escapeHtml(method.window)} works: field-normalized top 10% and top 1% papers, publication scale, h-index, field breadth, international collaboration, core-source share, open access, SDG-linked research, and funder diversity.</p>
    <p>${escapeHtml(method.normalization)}</p>
    <div class="method-grid">
      ${catalog.map((item) => `<div class="method-item"><b>${escapeHtml(item.label)}</b><span>${escapeHtml(item.description)}</span></div>`).join("")}
    </div>
    <p><a href="https://github.com/anzupop/open-global-university-rankings/blob/main/docs/methodology.md">Read the full methodology</a></p>
  `;
}

function populateCountries() {
  const countries = new Set();
  Object.values(payload.rankings).flat().forEach((row) => countries.add(row.country));
  [...countries].sort().forEach((code) => {
    const option = document.createElement("option");
    option.value = code;
    option.textContent = code;
    country.appendChild(option);
  });
}

function render() {
  if (!payload) return;
  const rows = findCurrentRows();
  body.innerHTML = rows.map(rowTemplate).join("");
}

function rowTemplate(row) {
  const rankings = Object.entries(row.publishedRankings || row.sources || {})
    .filter(([, value]) => value)
    .map(([key, value]) => `<span class="pill">${sourceLabel(key)} ${value}</span>`)
    .join("");
  const aliases = (row.aliases || []).map(escapeHtml).join(" · ");
  return `<tr>
    <td class="rank">${row.rank}</td>
    <td>
      <div class="uni"><button class="uni-button" type="button" data-score-details="${escapeHtml(row.openalex)}" title="Score Details">${escapeHtml(row.name)}</button> ${row.medical ? '<span class="pill">medical</span>' : ''}</div>
      ${aliases ? `<div class="aliases">${aliases}</div>` : ""}
      <div class="sub"><a href="${row.openalex}">OpenAlex</a>${row.ror ? ` · <a href="${row.ror}">ROR</a>` : ""}</div>
    </td>
    <td>${row.country}</td>
    <td>${row.score.toFixed(2)}</td>
    <td>${row.metrics.works2020_2024.toLocaleString()}</td>
    <td>${row.metrics.hIndex.toLocaleString()}</td>
    <td>${rankings}</td>
  </tr>`;
}

function openScoreModal(row) {
  modalTitle.textContent = row.name;
  const aliases = row.aliases && row.aliases.length ? ` · ${row.aliases.join(" · ")}` : "";
  modalMeta.textContent = `Rank ${row.rank} · ${row.country} · Score ${row.score.toFixed(2)}${aliases}`;
  modalBody.innerHTML = metricDetailsTemplate(row);
  modal.hidden = false;
  document.body.classList.add("modal-open");
  modalClose.focus();
}

function closeScoreModal() {
  modal.hidden = true;
  document.body.classList.remove("modal-open");
  if (lastScoreButton) lastScoreButton.focus();
}

function metricDetailsTemplate(row) {
  const activeWeight = ranking === "research" ? "research_weight" : "comprehensive_weight";
  const details = row.metricDetails && row.metricDetails.length ? row.metricDetails : fallbackMetricDetails(row);
  return details.map((item) => `
    <div class="metric-line">
      <b>${escapeHtml(item.label)} · ${formatMetricValue(item.value, item.format)}</b>
      <span>Score ${Number(item.score || 0).toFixed(2)} · Weight ${formatWeight(item[activeWeight])}</span>
      <span>${escapeHtml(item.description || "")}</span>
    </div>
  `).join("");
}

function findCurrentRows() {
  if (!payload) return [];
  const q = search.value.trim().toLowerCase();
  const c = country.value;
  return payload.rankings[ranking].filter((row) => {
    if (c && row.country !== c) return false;
    if (!q) return true;
    const haystack = [row.name, row.country, ...(row.aliases || [])].join(" ").toLowerCase();
    return haystack.includes(q);
  });
}

function fallbackMetricDetails(row) {
  return [
    ["Publication scale", row.metrics.works2020_2024, "integer", "OpenAlex works from 2020-2024."],
    ["Institution h-index", row.metrics.hIndex, "integer", "OpenAlex institution h-index."],
    ["Field breadth", row.metrics.fieldEntropy, "decimal", "Shannon entropy across OpenAlex fields."],
    ["Open access share", row.metrics.openAccessShare, "percent", "OpenAlex open-access works divided by total works."],
  ].map(([label, value, format, description]) => ({ label, value, format, description, score: 0, research_weight: 0, comprehensive_weight: 0 }));
}

function formatMetricValue(value, format) {
  const number = Number(value || 0);
  if (format === "percent") return `${(number * 100).toFixed(1)}%`;
  if (format === "decimal") return number.toFixed(3);
  return Math.round(number).toLocaleString();
}

function formatWeight(value) {
  return `${(Number(value || 0) * 100).toFixed(0)}%`;
}

function sourceLabel(key) {
  return {
    arwu2025: "ARWU",
    qs2027: "QS",
    usnews2026_2027: "US News",
  }[key] || key;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
"""


if __name__ == "__main__":
    main()
