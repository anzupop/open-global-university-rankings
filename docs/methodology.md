# Open-Data World University Ranking Methodology

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
python .\scripts\build_rankings.py --candidate-pool --match --metrics --score
```

## Institution unit

Institutions are matched to ROR/OpenAlex IDs. Affiliated medical schools are not split from their parent universities. Independent medical universities or independent campuses are kept as separate institutions if they appear as such in the source candidate pool. The output includes a `has_medical_school_or_center` flag.

## Open data indicators

Final scores use OpenAlex/ROR data only. Google Scholar is excluded because it has no stable public API, institutional pages are affected by profile coverage, and bulk automated access is not a suitable reproducible pipeline.

Publication window: 2020-2024. Included OpenAlex work types: `article,review,book,book-chapter`.

This generated version uses institution-level OpenAlex fields:

- Works and open-access works from `counts_by_year` for 2020-2024.
- Citation proxy from `counts_by_year.cited_by_count` for 2020-2024.
- Long-run influence from `summary_stats.h_index`.
- Recent citation intensity from `summary_stats.2yr_mean_citedness`.
- Field breadth from the institution `topics` field distribution.

Indicators are winsorized at the 2.5th and 97.5th percentiles within the candidate pool, then mapped to 0-100. Volume indicators use `log1p`.

## Weights

Research ranking:

- Citation/influence volume proxy: 30%
- Citation intensity proxy: 20%
- h-index: 20%
- Publication scale: 15%
- Field breadth: 10%
- International collaboration proxy: 3%
- Open access share: 2%

Academic comprehensive ranking:

- Publication scale: 20%
- Field breadth: 20%
- Citation/influence volume proxy: 20%
- Citation intensity proxy: 15%
- h-index: 15%
- International collaboration proxy: 6%
- Open access share: 4%

## Current caveats

- US News is wired to the `search?format=json&page=N` endpoint; in this environment it required first loading `https://www.usnews.com/` in a browser session, then caching the JSON pages.
- The current international collaboration field is a transparent proxy from topic breadth and recent citation intensity until replaced with an exact multi-country-affiliation query or CWTS Leiden Open Edition indicator.
- Exact top 1% / top 10% field-normalized paper counts are not used in the generated lightweight version; the script keeps a heavier query path for later enhancement.
- OpenAlex coverage and institution lineage are transparent and reproducible, but not identical to Web of Science, Scopus, Google Scholar, QS, ARWU, or US News bibliometric universes.
