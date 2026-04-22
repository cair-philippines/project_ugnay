# Phase 3: Platform MVP

## Purpose

Phase 3 delivers a visual web platform for exploring school connectivity, isolation, and feeder accessibility across the Philippines. The primary audience is DepEd planners; the platform is designed for eventual public access to promote transparency and coordination with LGUs, private sector, and civil society.

## Architecture

```
platform/
  backend/
    main.py              FastAPI app (API + SPA serving)
    data_loader.py       Loads Phase 1+2 outputs at startup
    requirements.txt     Python dependencies
  frontend/
    index.html           Entry point
    package.json         React 19 + deck.gl + Tailwind
    vite.config.js       Dev proxy to backend
    src/
      main.jsx           React root
      index.css          Tailwind imports
      App.jsx            Top-level layout + state
      hooks/
        useSchoolData.js API client hook
      components/
        MapView.jsx      deck.gl map (3 layers)
        FilterBar.jsx    Cascading region/province/municipality
        SchoolPanel.jsx  Detail panel with metrics + neighbors
        MetricLegend.jsx Color scale + sector legend
        StatsBar.jsx     System-wide counts
  Dockerfile             Multi-stage build for Cloud Run
  prepare_deploy.sh      Stage data files for Docker build
```

### Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | FastAPI + uvicorn |
| Frontend | React 19 + Vite 6 |
| Map | deck.gl 9 + react-map-gl + MapLibre GL |
| Styling | Tailwind CSS 4 |
| Basemap | CARTO Positron (free, no API key) |
| Serialization | orjson (fast JSON) |
| Deployment | Docker multi-stage → Cloud Run |

### Data Flow

```
Phase 1+2 outputs (parquet + GeoJSON)
  ↓ prepare_deploy.sh copies to platform/data/
  ↓ Docker build bakes data into image
  ↓ FastAPI loads at startup into memory
  ↓ React fetches via /api/* endpoints
  ↓ deck.gl renders on map
```

No runtime dependencies on OSRM, GCS, or external services. The platform is a static consumer of precomputed data.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/stats` | GET | System-wide summary (total schools, deserts by type) |
| `/api/filters?region=&province=` | GET | Cascading dropdown options |
| `/api/schools?region=&province=&municipality=&sector=&limit=&offset=` | GET | Paginated school list with metrics |
| `/api/schools/{school_id}` | GET | Single school detail (all metrics) |
| `/api/schools/{school_id}/neighbors?max_km=` | GET | Edges + neighbor details for selected school |
| `/api/aggregations?level=&region=&province=` | GET | Municipal/provincial/regional aggregated metrics |
| `/api/boundaries/{level}` | GET | GeoJSON boundaries (municipal or provincial) |
| `/*` (non-API) | GET | React SPA (index.html) |

## Map Layers

The map renders three deck.gl layers:

### 1. GeoJsonLayer — Choropleth

Municipal (or provincial) boundaries colored by the selected metric. The user selects from 8 metrics via dropdown:

| Metric | Label | Source |
|--------|-------|--------|
| `isolation_score` | Isolation Score | Per-school (aggregated) |
| `feeder_isolation_score` | Feeder Isolation | Per-school (aggregated) |
| `pct_private_desert` | Private Desert % | Municipal aggregation |
| `pct_esc_desert` | ESC Desert % | Municipal aggregation |
| `pct_jhs_desert` | JHS Desert % | Municipal aggregation |
| `pct_shs_desert` | SHS Desert % | Municipal aggregation |
| `mean_nearest_private_km` | Avg Nearest Private | Municipal aggregation |
| `mean_nearest_esc_km` | Avg Nearest ESC | Municipal aggregation |

Color scale: green (low/good) → yellow → red (high/bad).

### 2. ScatterplotLayer — Schools

Individual school dots colored by sector:
- Blue: public schools
- Orange: private schools
- Red: selected school (enlarged)

Visible at all zoom levels. Tooltips show school name, sector, municipality, isolation score, and neighbor count.

### 3. ArcLayer — Neighbor Connections

Appears when a school is selected. Arcs from the selected school to all neighbors within the distance cutoff. Arc color indicates distance: green (close) → red (far).

## School Detail Panel

A 384px right-side panel that appears when a school is clicked. Contains:

**Header:** School name, ID, sector, location

**Offerings:** ES / JHS / SHS badges

**General Connectivity:**
- Isolation score
- Neighbor counts at 5/10/20 km
- Nearest private and ESC school distances
- Road detour factor

**Desert Flags:** Private desert, ESC desert, JHS desert, SHS desert — color-coded badges (green = no, red = yes)

**Feeder Connectivity:**
- Feeder isolation score
- For ES schools: nearest JHS, JHS count within 10 km, self-feeds badge
- For JHS schools: nearest SHS, SHS count within 10 km, self-feeds badge

**Neighbors:** Scrollable list of up to 50 nearest neighbors with name, sector, and distance

## Municipal Boundaries

Dissolved from project_coordinates' adm4 (barangay) shapefiles by `ADM3_PCODE` (municipality code).

| File | Features | Size | Source |
|------|----------|------|--------|
| `municipal_boundaries.geojson` | 1,642 | 6.8 MB | Dissolved from 42,022 barangay polygons |
| `provincial_boundaries.geojson` | 118 | 4.3 MB | Dissolved from municipal boundaries |

Geometries are simplified at tolerance 0.001° (~110m) to reduce file size from 613 MB to 6.8 MB while keeping shapes recognizable.

**Script:** `scripts/dissolve_municipal_boundaries.py`

## Running Locally

### Backend (dev mode)

```bash
# From platform/backend/
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

The backend looks for data at `../../output/` relative to `backend/`. No data staging needed for dev.

### Frontend (dev mode)

```bash
# From platform/frontend/
npm install
npm run dev
```

Vite proxies `/api/*` to `http://localhost:8000`. Open `http://localhost:5173`.

### Docker (production)

```bash
# From platform/
./prepare_deploy.sh    # Stage data files
docker build -t ugnay-platform .
docker run -p 8080:8080 ugnay-platform
```

Open `http://localhost:8080`.

### Cloud Run

```bash
# Build and push
gcloud builds submit --tag gcr.io/ecair-paaral-project/ugnay-platform
gcloud run deploy ugnay-platform \
  --image gcr.io/ecair-paaral-project/ugnay-platform \
  --region asia-southeast1 \
  --memory 2Gi \
  --allow-unauthenticated
```

Memory recommendation: 2Gi. The app loads ~10M edges (~200 MB in memory as DataFrame) plus ~56K school records and GeoJSON boundaries.

## Data Dependencies

The platform reads from `platform/data/` (Docker) or `output/` (dev):

| File | Phase | Size | Loaded as |
|------|-------|------|-----------|
| `edges/all_edges.parquet` | Phase 1 | 128 MB | DataFrame (for per-school neighbor queries) |
| `edges/schools_unified_snapshot.parquet` | Phase 1 | ~5 MB | DataFrame → list of dicts |
| `metrics/school_accessibility.parquet` | Phase 2 | 5.6 MB | DataFrame → list of dicts |
| `aggregations/municipal_summary.parquet` | Phase 2 | ~100 KB | list of dicts |
| `aggregations/provincial_summary.parquet` | Phase 2 | ~10 KB | list of dicts |
| `aggregations/regional_summary.parquet` | Phase 2 | ~2 KB | list of dicts |
| `boundaries/municipal_boundaries.geojson` | Phase 3 | 6.8 MB | GeoJSON dict |
| `boundaries/provincial_boundaries.geojson` | Phase 3 | 4.3 MB | GeoJSON dict |

## Choropleth-Aggregation Matching

The choropleth layer matches GeoJSON features to aggregation data by constructing a key from `ADM1_EN|ADM2_EN|ADM3_EN` (region|province|municipality). This relies on the boundary feature properties matching the aggregation table's region/province/municipality strings. If names don't match (e.g., casing differences, abbreviations), the feature renders as gray (no data).

This is a known coupling point. If mismatches appear, the fix is to normalize names in the aggregation module or add PSGC code matching to the boundary features.

## Related Documentation

- [Phase 1: Sparse Edge Network](phase_1_sparse_edge_network.md)
- [Phase 2: Accessibility Metrics](phase_2_accessibility_metrics.md)
- [Design Plan](plan.md)
