# Collection Completeness — local MVP

Split-screen map + statistics product for Streetwave: shows a local authority **where and
when** its area has been surveyed by bin-lorry-mounted measurement kit. This screen is about
**data collection only** — it never shows mobile signal quality, and never uses the word
"coverage" alone in the UI.

**Live demo:** https://angushay91-svg.github.io/collection-completeness/
**Demo council:** Dorset Council (`E06000059`). All collection history is **synthetic**
(seeded, reproducible) — real Streetwave data is internal. A real-data CSV
(`h3_10,last_collected_at,test_count`) can be dropped in via `--real-csv`.

## Layout

```
pipeline/   Python 3.11+: download, build, synthesize, rollup  (duckdb, h3 v4, shapely, pyproj)
  build.py  CLI: python pipeline/build.py --council dorset --stage all
  synth.py  bin-round simulator + demand pins (seed 42)
  sources.py pinned dataset URLs + licences + retrieval dates
  tests/    locked-metric acceptance tests (pytest)
app/        Nuxt 3 + TypeScript + Tailwind + MapLibre GL JS
  public/data/  pipeline outputs (gitignored, regenerable)
```

## Run it

```
make data    # download sources (≈1.6GB), build everything into app/public/data/dorset/
make test    # pytest on the locked metric definitions
make dev     # Nuxt dev server on http://localhost:3000
make demo    # data + dev
```

## Sources (all open data, OGL v3)

| Dataset | Vintage used | Notes |
|---|---|---|
| OS Open Roads | April 2026 GeoPackage | attribute names verified: `road_classification`, `road_classification_number`, `trunk_road`, … |
| OS Open UPRN | May 2026 CSV | no AddressBase anywhere |
| ONS LAD boundaries | December 2024 BFC | |
| ONS Wards | December 2024 BFC (52 in Dorset) | |
| ONS Parishes & NCP areas | **December 2025** BFC (`PARNCP_DEC_2025_EW_BFC`, 259 units) — the Dec-2024 service exposes no LAD field, so the newer vintage is used | |

## Locked metric definitions (§5.2 of the spec — unit-tested)

- Collected = ≥1 pass in the trailing 365 days. Test counts displayed, never gating.
- Headline % = distinct road cells collected <12m ÷ distinct road cells. **Falls as data ages** — intentional.
- Age bands 0–12 / 12–24 / >24 / never (never = hatch + vermillion, non-colour encoded).
- Class mapping: Motorway→Motorway, A Road→A, B Road→B, Classified Unnumbered→C, rest→Residential & minor.
- Premises % = UPRNs within 250m (EPSG:27700, straight-line) of a fresh road-cell centroid.
- Rollups always computed from the full cell table (averaging-trap regression test included).

## Deviations from spec (all deliberate, all visible)

- **`fleet_scale` calibration (default 2.0)** in `synth.py`: at the spec's literal fleet size
  (4 depots × 8–14 weekly routes of 25–60km) only ~27% of Dorset's ~7,100km network is
  physically reachable; 2.0 lands the synthetic headline near real-world completeness (~40%).
- **25% of synthetic routes retire partway** through the 30-month history — without this the
  12–24m and >24m bands are almost empty (weekly recurrence keeps everything fresh).
- Dorset-scale reality: **46,450 res-10 road cells** (spec estimated ~300k). Performance tier 2
  (res-8 at low zoom + per-ward res-10 viewport loading) keeps initial load ≈2.4MB ≤ 8MB budget.
  PMTiles not needed.
- Non-road collected cells: none exist in the synthetic build, so the toggle ships disabled.
- Playwright/axe CI deferred (pipeline pytest suite complete; manual checks done) — agreed scope.

## Demo script (90 seconds)

Open the council view → the dark-blue spine of towns is fresh; rural west is hatched
vermillion ("never"). Switch Boundaries → Wards → the choropleth shows **Blackmore Vale**
faded (its routes stopped 12 months ago — the rolling window working as intended). Click it
to drill → age profile shows the 12–24m bulge. Toggle **Trunk roads only** → the **A35** row
shows ~48% (deliberately part-routed). Toggle **Demand** → outstanding resident pins cluster
in the never-collected areas → "that's your next survey run." Export the ward CSV from the
league table.
