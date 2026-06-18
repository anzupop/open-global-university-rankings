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

Publication window: 2020-2024. Included OpenAlex work types: `article,review,book,book-chapter`. Works are counted through `authorships.institutions.lineage`, so child institutions and known institutional lineage are included.

The current generated version uses work-level OpenAlex API aggregates where possible:

- Total works: all included works in the publication window.
- Top 10% and top 1% papers: OpenAlex `citation_normalized_percentile.is_in_top_10_percent` and `citation_normalized_percentile.is_in_top_1_percent`.
- International collaboration: `countries_distinct_count:>1`.
- Core-source share: `primary_location.source.is_core:true`.
- Open access share and OA status breakdown: `open_access.oa_status`.
- Field breadth: Shannon entropy over `primary_topic.field.id`, plus active-field count.
- SDG-linked research: works grouped by `sustainable_development_goals.id`.
- Funder diversity: distinct `funders.id` groups observed in the work metadata.
- Long-run influence: OpenAlex institution `summary_stats.h_index`.

Indicators are winsorized at the 2.5th and 97.5th percentiles within the candidate pool, then mapped to 0-100. Volume indicators use `log1p` before winsorization. Each row in the web table exposes the raw metric value, normalized score, and weight.

## Indicators and weights

- Publication scale: OpenAlex works from 2020-2024. Research weight 10%; Academic comprehensive weight 18%.
- Top 10% papers: Works in OpenAlex's field/year-normalized top 10% citation percentile. Research weight 20%; Academic comprehensive weight 14%.
- Top 1% papers: Works in OpenAlex's field/year-normalized top 1% citation percentile. Research weight 16%; Academic comprehensive weight 10%.
- Top 10% share: Top 10% papers divided by total works. Research weight 12%; Academic comprehensive weight 8%.
- Top 1% share: Top 1% papers divided by total works. Research weight 10%; Academic comprehensive weight 6%.
- Institution h-index: OpenAlex institution h-index. Research weight 12%; Academic comprehensive weight 10%.
- Field breadth: Shannon entropy over OpenAlex primary-topic fields. Research weight 6%; Academic comprehensive weight 16%.
- Active fields: OpenAlex fields with meaningful publication volume. Research weight 3%; Academic comprehensive weight 8%.
- International collaboration: Works with affiliations from more than one country. Research weight 8%; Academic comprehensive weight 8%.
- Core-source share: Works whose primary source is marked core by OpenAlex. Research weight 6%; Academic comprehensive weight 5%.
- Open access share: OpenAlex open-access works divided by total works. Research weight 3%; Academic comprehensive weight 3%.
- SDG-linked research: Works tagged to at least one UN Sustainable Development Goal. Research weight 2%; Academic comprehensive weight 2%.
- Funder diversity: Distinct funders observed in OpenAlex work metadata. Research weight 2%; Academic comprehensive weight 2%.

## Current caveats

- US News is wired to the `search?format=json&page=N` endpoint; in this environment it required first loading `https://www.usnews.com/` in a browser session, then caching the JSON pages.
- OpenAlex SDG and funder metadata are useful open indicators but are not complete for all fields and countries.
- OpenAlex coverage and institution lineage are transparent and reproducible, but not identical to Web of Science, Scopus, Google Scholar, QS, ARWU, or US News bibliometric universes.
