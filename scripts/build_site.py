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
        return {
            "rank": int(row["rank"]),
            "name": row["display_name"],
            "country": row["country_code"],
            "score": float(row["score"]),
            "medical": row["has_medical_school_or_center"].lower() == "true",
            "openalex": row["openalex_id"],
            "ror": row["ror_id"],
            "sources": {
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
                "citationProxy": int(float(metric.get("cited_by_proxy", 0) or 0)),
            },
        }

    return {
        "generated": "2026-06-18",
        "summary": {
            "candidatePool": len(candidates),
            "arwu": sum(row["in_arwu_2025"].lower() == "true" for row in candidates),
            "qs": sum(row["in_qs_2027"].lower() == "true" for row in candidates),
            "usnews": sum(row["in_usnews_2026_2027"].lower() == "true" for row in candidates),
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
        <label class="check"><input id="medical" type="checkbox"> Medical flag</label>
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
            <th>Sources</th>
          </tr>
        </thead>
        <tbody id="ranking-body"></tbody>
      </table>
    </section>

    <section class="method">
      <h2>Method</h2>
      <p>The source rankings define only the candidate pool. Final scores use open OpenAlex/ROR data: 2020-2024 works, citation proxy, h-index, topic breadth, open access share, and a transparent collaboration proxy.</p>
      <p>Affiliated medical schools are not ranked separately; universities with medical schools or medical centers are flagged in the table.</p>
      <p><a href="https://github.com/anzupop/open-global-university-rankings/blob/main/docs/methodology.md">Read the full methodology</a></p>
    </section>
  </main>

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
.check { display: inline-flex; align-items: center; gap: 6px; color: var(--muted); }

.table-wrap { overflow: auto; }
table { border-collapse: collapse; width: 100%; min-width: 980px; }
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
.sub { color: var(--muted); font-size: 12px; }
.pill {
  display: inline-block;
  margin: 2px 4px 2px 0;
  padding: 2px 6px;
  border: 1px solid var(--line);
  color: var(--muted);
  font-size: 12px;
}
.method { margin-top: 18px; padding: 18px; max-width: 980px; }
.method h2 { margin-top: 0; }

@media (max-width: 760px) {
  .site-header, .controls { flex-direction: column; }
  .summary { grid-template-columns: repeat(2, 1fr); }
  input { width: 100%; min-width: 0; }
}
"""


APP_JS = """let payload;
let ranking = "research";

const body = document.getElementById("ranking-body");
const search = document.getElementById("search");
const country = document.getElementById("country");
const medical = document.getElementById("medical");

fetch("data/rankings.json")
  .then((r) => r.json())
  .then((data) => {
    payload = data;
    renderSummary(data.summary);
    populateCountries();
    render();
  });

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    button.classList.add("active");
    ranking = button.dataset.ranking;
    render();
  });
});

[search, country, medical].forEach((el) => el.addEventListener("input", render));

function renderSummary(summary) {
  document.getElementById("summary").innerHTML = [
    ["Candidate Pool", summary.candidatePool],
    ["ARWU 2025", summary.arwu],
    ["QS 2027", summary.qs],
    ["US News 2026-2027", summary.usnews],
  ].map(([label, value]) => `<div class="stat"><b>${value}</b><span>${label}</span></div>`).join("");
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
  const q = search.value.trim().toLowerCase();
  const c = country.value;
  const onlyMedical = medical.checked;
  const rows = payload.rankings[ranking].filter((row) => {
    if (c && row.country !== c) return false;
    if (onlyMedical && !row.medical) return false;
    if (!q) return true;
    return row.name.toLowerCase().includes(q) || row.country.toLowerCase().includes(q);
  });
  body.innerHTML = rows.map(rowTemplate).join("");
}

function rowTemplate(row) {
  const sources = Object.entries(row.sources)
    .filter(([, value]) => value)
    .map(([key, value]) => `<span class="pill">${sourceLabel(key)} ${value}</span>`)
    .join("");
  return `<tr>
    <td class="rank">${row.rank}</td>
    <td>
      <div class="uni">${escapeHtml(row.name)} ${row.medical ? '<span class="pill">medical</span>' : ''}</div>
      <div class="sub"><a href="${row.openalex}">OpenAlex</a>${row.ror ? ` · <a href="${row.ror}">ROR</a>` : ""}</div>
    </td>
    <td>${row.country}</td>
    <td>${row.score.toFixed(2)}</td>
    <td>${row.metrics.works2020_2024.toLocaleString()}</td>
    <td>${row.metrics.hIndex.toLocaleString()}</td>
    <td>${sources}</td>
  </tr>`;
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
