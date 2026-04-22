# project_ugnay — Design Plan

## Context

project_coordinates provides unified lat/lon for ~57K Philippine schools. The existing OSRM setup computes road distances for ~13K schools in NCR+CALABARZON. project_ugnay extends this nationwide: building a sparse school-to-school distance network, computing per-school accessibility metrics, and serving them through a visual platform for DepEd planners (and eventually LGUs, private sector, civil society).

The project name "ugnay" means "connection" in Filipino.

---

## Phase 1: Sparse Edge Computation

**Goal:** For every school with coordinates (~57K), compute road distances to all neighbors within a cutoff and store as a sparse edge table.

### 1.1 Project scaffolding

Create `/workspace/innovation-projects/project_ugnay/` with:

```
project_ugnay/
  modules/
    __init__.py
    gcs_utils.py              # Copied from paaral_eda, adapted for ugnay/ prefix
    coordinates.py             # Load + merge pub/prv coords from project_coordinates
    osrm_client.py             # Batched OSRM Table API caller (sparse output)
    sparse_edges.py            # Haversine pre-filter + OSRM orchestration per region
    inter_island.py            # Island group tagging, sea-separation flags
  notebooks/
    1.0-build-sparse-edges.ipynb
    1.1-validate-edges.ipynb
  scripts/
    run_region_batch.py        # CLI for long-running regional computation
  output/                      # Local staging before GCS upload
    edges/
  keys -> ../paaral_eda/keys   # Symlink
  documentation/
    plan.md
```

**Key files to reuse:**
- `paaral_eda/modules/gcs_utils.py` → copy + change bucket prefix to `ugnay/`
- `project_paaral/notebooks/2.2b` → adapt OSRM batching logic into `osrm_client.py`
- `project_coordinates/data/gold/*.parquet` → read-only input

### 1.2 Coordinates loader (`modules/coordinates.py`)

Load both parquets from project_coordinates, filter to valid coords, merge into unified DataFrame:

```
school_id, school_name, latitude, longitude, region, province, municipality,
barangay, sector, offers_es, offers_jhs, offers_shs, urban_rural,
enrollment_status, esc_participating, psgc_region, psgc_province, psgc_municity
```

~56,788 rows (47,874 public + 8,914 private with coords).

### 1.3 OSRM sparse edge computation (`modules/osrm_client.py`, `modules/sparse_edges.py`)

**Strategy: region-by-region with haversine pre-filter.**

For each of the 17 DepEd regions:
1. Select all schools in region (~1K-8K schools)
2. Haversine pre-filter: for each school, identify candidates within 30 km haversine
3. Send candidate pairs to OSRM Table API in batches of 500 sources
4. Keep edges where road distance ≤ 20 km (configurable cutoff)
5. Store as sparse Parquet

Cross-region boundary pass: for each adjacent region pair, extract schools within 30 km of the boundary and compute OSRM distances between them.

**Edge table schema** (per-region Parquet):

| Column | Type | Description |
|--------|------|-------------|
| source_id | str | Origin school_id |
| target_id | str | Destination school_id |
| road_distance_m | float32 | OSRM road distance in meters |
| haversine_distance_m | float32 | Great-circle distance |
| road_haversine_ratio | float32 | Detour factor |
| source_region | str | Region of source |
| is_cross_region | bool | Source and target in different regions |
| is_sea_separated | bool | Within haversine cutoff but OSRM returns no route |

**Estimates:**
- ~3-10M edges total (~50-200 neighbors per school within 20 km)
- ~90-300 MB total Parquet (17 regional files + 1 cross-region file)
- ~2 hours computation time

### 1.4 Inter-island handling (`modules/inter_island.py`)

- Province-to-island-group lookup dict (Luzon/Visayas/Mindanao)
- Tag edges where OSRM returns no route but haversine is within cutoff as `is_sea_separated=True`
- No sea routing attempted — the absence of a road route IS the signal

### 1.5 GCS upload

Upload to `gs://data_ecair_paaral/ugnay/v1/edges/` with manifest JSON recording parameters, PBF date, coordinate snapshot date.

### 1.6 Validation (`notebooks/1.1-validate-edges.ipynb`)

- Sample 20 school pairs, compare OSRM distances vs Google Maps
- Check edge count distribution per school (flag outliers)
- Verify cross-region edges exist for border municipalities
- Spot-check sea-separated flags in Visayas

---

## Phase 2: Metrics + Aggregations

**Goal:** Compute interpretable per-school accessibility metrics and admin-level summaries.

### 2.1 Per-school metrics (`modules/accessibility_metrics.py`)

From the edge table, compute for each school:

| Metric | Description | Interpretability |
|--------|-------------|-----------------|
| n_neighbors_5km | Schools within 5 km road | Direct count |
| n_neighbors_10km | Schools within 10 km | Direct count |
| n_neighbors_20km | Schools within 20 km | Direct count |
| n_private_5km / n_private_10km | Private schools at distance bands | Direct count |
| nearest_private_km | Road km to closest private school | "Nearest private school is X km away" |
| nearest_esc_km | Road km to closest ESC-participating school | "Nearest ESC school is X km away" |
| mean_road_haversine_ratio | Avg detour factor to neighbors | "Roads here are X times longer than straight-line" |
| private_desert | No private school within 10 km road | Boolean flag |
| esc_desert | No ESC school within 10 km road | Boolean flag |
| isolation_score | Weighted inverse of neighbor counts at 5/10/20 km | 0 (connected) to 1 (isolated) |

Output: `metrics/school_accessibility.parquet` (~57K rows, ~30 columns).

### 2.2 Admin-level aggregations (`modules/aggregation.py`)

Municipal, provincial, and regional summaries:

| Metric | Description |
|--------|-------------|
| n_public, n_private, n_esc | School counts |
| mean/max isolation_score | Isolation distribution |
| pct_private_desert | % of public schools with no private school within 10 km |
| pct_esc_desert | % with no ESC school within 10 km |
| mean/median nearest_private_km | Distance to nearest private |
| centroid_lat, centroid_lon | For map placement |

Output: 3 Parquet files in `aggregations/`.

### 2.3 GCS upload

All metrics + aggregations to `gs://data_ecair_paaral/ugnay/v1/metrics/` and `.../aggregations/`.

---

## Phase 3: Platform MVP

**Goal:** Visual web platform for DepEd planners to explore school connectivity.

### 3.1 Tech stack

- **Backend:** FastAPI (same as locator)
- **Frontend:** React 19 + deck.gl + Tailwind CSS (same foundation as locator, upgraded from Leaflet to deck.gl for performance with 57K points + edges)
- **Deployment:** Cloud Run, data baked into Docker image
- **Map layers:** deck.gl ScatterplotLayer (schools), ArcLayer (edges), GeoJsonLayer (admin boundaries for choropleth)

### 3.2 Data serving

- Precomputed parquets loaded at FastAPI startup (same pattern as locator)
- API endpoints:
  - `GET /api/schools` — filtered school list with metrics
  - `GET /api/schools/{id}/neighbors` — edges + neighbor details for selected school
  - `GET /api/aggregations?level=municipal` — choropleth data
  - `GET /api/filters` — cascading region/province/municipality
  - `GET /api/stats` — system-wide summary

### 3.3 Interaction model

- National view: choropleth by aggregated metric (isolation score, ESC desert %, etc.)
- Zoom in: individual school dots, colored/sized by metric
- Click school: show neighbor arcs, detail panel with metrics
- Metric selector: dropdown to switch choropleth variable
- Filter: region/province/municipality cascade

### 3.4 Platform directory structure

```
project_ugnay/
  platform/
    Dockerfile
    prepare_deploy.sh
    backend/
      main.py
      data_loader.py
      requirements.txt
    frontend/
      package.json
      vite.config.js
      src/
        App.jsx
        components/
          MapView.jsx
          SchoolPanel.jsx
          FilterBar.jsx
          MetricLegend.jsx
        hooks/
          useSchoolData.js
```

---

## Phase 4: Analytical Lenses (incremental)

Built on top of the Phase 3 platform as additional views/presets:

- **Isolated Schools:** Highlight private_desert + high isolation_score public schools
- **ESC Coverage Gaps:** Non-participating private schools near congested public schools
- **Potential ESC Partners:** Non-ESC private schools within 10 km of high-isolation public schools
- **Flow Overlay:** Integrate paaral flow arcs as optional layer (requires project_paaral data)

Each lens = a combination of metric filter + layer visibility preset. Flexible by design.

---

## Decisions Made

- **Distance cutoff:** Single 20 km road distance cutoff (30 km haversine pre-filter). School-level filtering (ES vs JHS vs SHS catchment) applied at query/display time, not at computation time.
- **Foot profile:** Deferred. Phase 1 uses car/road distances only. Walking profile can be added in a later phase once urban OSM footway coverage is assessed.
- **Missing private school coordinates:** Flagged as "needs geocoding" in the platform. Included in tables/stats but omitted from map and edge computation. Makes the 27% gap transparent to planners.
- **Municipal boundaries:** Dissolve from existing adm4 (barangay) shapefiles in `project_coordinates/data/gold/phl_admbnda_adm4_updated/` by PSGC municipality code.

---

## Verification

After each phase:
- **Phase 1:** Validate edge distances against Google Maps for sample pairs; check edge count distributions; confirm cross-region edges exist
- **Phase 2:** Spot-check isolation scores against known isolated areas (e.g., mountain provinces); verify aggregation math
- **Phase 3:** Load test with full dataset on Cloud Run; verify map renders at all zoom levels; test filter + click interactions
