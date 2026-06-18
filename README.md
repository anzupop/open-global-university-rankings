# Open-Data World University Rankings

This project builds two independent world university rankings from open data.

The commercial rankings are used only to define the candidate pool. Ranking
positions and scores from QS, ARWU, and US News are not used in final scoring.

## Current Status

- ARWU 2025 top 200 candidate pool, with broader published-rank references parsed from the ShanghaiRanking Nuxt payload.
- QS 2027 top 200 candidate pool, with broader published-rank references parsed from the official QS Excel file.
- US News 2026-2027 top 200 candidate pool, with broader published-rank references from cached JSON search pages when available.
- Candidate pool currently generated from deduplicated OpenAlex institutions in ARWU + QS + US News.

## Outputs

- `data/processed/candidate_pool.csv`
- `data/processed/open_metrics.csv`
- `data/processed/world_universities_research_top200.csv`
- `data/processed/world_universities_academic_comprehensive_top200.csv`
- `docs/methodology.md`

## Rebuild

```powershell
python .\scripts\build_rankings.py --candidate-pool --match --metrics --score
```

If the US News endpoint is blocked in a future run, first open `https://www.usnews.com/`
in a browser session, then cache the JSON pages under `data/raw/usnews_search_page_N.json`;
the script will reuse those files. As a fallback, fill
`data/manual_usnews_2026_2027_top200.csv` with columns `rank,name,country`.

## Notes

Google Scholar is deliberately excluded from scoring because it lacks a stable
public API, institutional pages depend on public profile coverage, and bulk
automated extraction is not a good reproducible pipeline.
