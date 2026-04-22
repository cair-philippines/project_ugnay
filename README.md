# project_ugnay — Philippine School Connectivity Network

**"Ugnay"** means *connection* in Filipino.

A nationwide school-to-school road distance network for ~56K Philippine schools, with per-school accessibility metrics. Built on top of the unified coordinates from [project_coordinates](../project_coordinates).

## Problem

DepEd has coordinates for ~56K schools, but no way to answer basic connectivity questions at scale:

- Which public schools have no private school within a reasonable road distance?
- Which areas are ESC deserts — communities where no ESC-participating school is accessible?
- Which schools are genuinely isolated by geography, versus those that just appear remote on a flat map?

Straight-line (haversine) distances mislead in archipelagic terrain — a school 3 km away across a mountain or strait may be 30 km by road. A road-based network is required.

## Solution

Two phases, both complete:

### Phase 1 — Sparse Edge Network

Computes road distances between every pair of schools within a 20 km road cutoff using the OSRM routing engine. Processes all 18 DepEd regions independently with a 30 km haversine pre-filter (KDTree) to avoid sending unnecessary pairs to OSRM, then stitches cross-region boundary edges.

**Output:** 9.88M directed edges across 55,864 schools (128 MB Parquet).

### Phase 2 — Accessibility Metrics

Derives per-school accessibility metrics from the edge table: neighbor counts at distance bands, nearest private/ESC/JHS/SHS school, desert flags, and an isolation score. Aggregates to municipal, provincial, and regional summaries.

**Output:** `school_accessibility.parquet` (55,864 rows, 30+ columns) + 3 admin-level summary files.

## Key Findings

| Metric | Value |
|---|---|
| Schools with road connectivity | 99% (55,157 / 55,864) |
| Isolated schools | 707 (geography or OSM gap) |
| Private deserts (no private school within 10 km road) | 30.8% of schools |
| ESC deserts (no ESC school within 10 km road) | 40.6% of schools |
| Median distance to nearest private school | 4.0 km |
| Median distance to nearest ESC school | 5.2 km |

## Data Sources

| Source | Description |
|---|---|
| `project_coordinates/data/gold/public_school_coordinates.parquet` | 47,607 public schools with validated coordinates |
| `project_coordinates/data/gold/private_school_coordinates.parquet` | 8,257 private schools with reliable coordinates |
| OSRM (car profile) | Road routing engine backed by OpenStreetMap Philippines |

Schools are excluded if their `coord_rejection_reason` indicates a bogus coordinate (placeholder default, coordinate cluster, outside all land polygons, etc.). See [project_coordinates](../project_coordinates) for the full coordinate quality pipeline.

## Output

### Edges (`output/edges/`)

| File | Description |
|---|---|
| `all_edges.parquet` | 9.88M directed edges, combined across all regions |
| `region_*.parquet` | Per-region edge files (18 files) |
| `cross_region_pairs.parquet` | Edges crossing regional boundaries |
| `schools_unified_snapshot.parquet` | Coordinate snapshot used for this run |
| `_manifest.json` | Run parameters, statistics, and file listing |

**Edge schema:**

| Column | Type | Description |
|---|---|---|
| `source_id` | str | Origin school ID |
| `target_id` | str | Destination school ID |
| `road_distance_m` | float32 | OSRM road distance in meters |
| `haversine_distance_m` | float32 | Great-circle distance in meters |
| `road_haversine_ratio` | float32 | Detour factor (road / haversine) |
| `source_region` | str | DepEd region of the source school |
| `is_cross_region` | bool | Source and target in different regions |
| `is_sea_separated` | bool | Within haversine cutoff but no road route |

### Metrics (`output/metrics/`)

`school_accessibility.parquet` — one row per school, 30+ columns including:

- `n_neighbors_5km`, `n_neighbors_10km`, `n_neighbors_20km` — school count at distance bands
- `nearest_private_km`, `nearest_esc_km` — road distance to nearest private / ESC school
- `private_desert`, `esc_desert` — boolean flags (no private/ESC within 10 km road)
- `nearest_jhs_km`, `jhs_desert`, `nearest_shs_km`, `shs_desert` — feeder-level connectivity
- `isolation_score` — weighted inverse of neighbor density (0 = connected, 1 = isolated)
- `feeder_isolation_score` — isolation accounting for school level (ES → JHS → SHS)
- `osrm_status` — `computed` / `osrm_failed` / `not_attempted`

### Aggregations (`output/aggregations/`)

| File | Rows | Description |
|---|---|---|
| `municipal_summary.parquet` | 1,707 | Per-municipality accessibility summary |
| `provincial_summary.parquet` | 127 | Per-province summary |
| `regional_summary.parquet` | 18 | Per-region summary |

### Dense Matrix (`output/dense_matrix/`)

| File | Description |
|---|---|
| `school_distance_matrix.npy` | Dense N×N distance matrix (float32, meters) |
| `school_distance_matrix_index.json` | School ID → matrix index mapping |

Built from the sparse edge table for downstream consumers (e.g., project_paaral) that require O(1) pair lookup. Only covers school pairs within the 20 km road cutoff; all other pairs are 0.

All outputs are also uploaded to GCS at `gs://data_ecair_paaral/ugnay/v1/`. Public copies of the edges and metrics are available on [Google Drive](https://drive.google.com/drive/folders/1JMkZT5PXGptlMNY3qLQEDl9ndi1JdZjM?usp=sharing).

## Usage

### Prerequisites

Run inside the `experiments-innovations-lab` Docker container. The container maps the host `innovation-projects/` directory to `/workspace/` internally.

```bash
# Start container (from innovation-projects/)
docker compose up -d experiments-innovations-lab

# Run scripts inside container
docker exec -w /workspace/project_ugnay experiments-innovations-lab python scripts/<script>.py
```

Python dependencies: `pandas`, `pyarrow`, `numpy`, `scipy`, `geopandas`, `requests`, `gcsfs`.

### Phase 1 — Compute sparse edges

```bash
# Full run (all 18 regions + cross-region edges + finalize + GCS upload)
docker exec -w /workspace/project_ugnay experiments-innovations-lab \
  python scripts/run_region_batch.py --all --cross-region --finalize

# Specific regions only
docker exec -w /workspace/project_ugnay experiments-innovations-lab \
  python scripts/run_region_batch.py "Region IV-A" "NCR"

# Force re-run even if output files already exist
docker exec -w /workspace/project_ugnay experiments-innovations-lab \
  python scripts/run_region_batch.py --all --cross-region --finalize --force
```

Estimated runtime: ~30 minutes for a full run.

### Phase 2 — Compute metrics

```bash
docker exec -w /workspace/project_ugnay experiments-innovations-lab \
  python scripts/run_metrics.py

# Skip GCS upload
docker exec -w /workspace/project_ugnay experiments-innovations-lab \
  python scripts/run_metrics.py --no-upload
```

### Validate edges

```bash
docker exec -w /workspace/project_ugnay experiments-innovations-lab \
  python scripts/validate_edges.py
```

Runs 5 checks: road ≥ haversine, distance symmetry, triangle inequality, distribution stability, and road/haversine ratio distribution.

### Build dense matrix

```bash
docker exec -w /workspace/project_ugnay experiments-innovations-lab \
  python scripts/build_dense_matrix.py
```

### Distance lookup (programmatic)

```python
from modules.distance_lookup import DistanceLookup

dist = DistanceLookup.from_parquet("output/edges/all_edges.parquet")

# Single pair (returns meters, or None if no edge)
km = dist.get("136718", "320102")

# All neighbors within radius
neighbors = dist.get_neighbors("136718", max_m=5000)

# Batch lookup
distances = dist.get_many([("136718", "320102"), ("136718", "130001")])
```

## Project Structure

```
project_ugnay/
├── modules/
│   ├── coordinates.py           # Load & merge public/private school coords from project_coordinates
│   ├── osrm_client.py           # Batched OSRM Table API with sub-batching and retry logic
│   ├── sparse_edges.py          # Region-by-region edge computation with KDTree haversine pre-filter
│   ├── inter_island.py          # Island group tagging (Luzon/Visayas/Mindanao) + sea-separation
│   ├── accessibility_metrics.py # Per-school metrics: neighbor counts, desert flags, isolation score
│   ├── aggregation.py           # Admin-level (municipal/provincial/regional) aggregations
│   ├── distance_lookup.py       # O(1) distance lookup from sparse edge table
│   └── gcs_utils.py             # GCS bucket paths and upload utilities
├── scripts/
│   ├── run_region_batch.py      # Phase 1 CLI entry point
│   ├── run_metrics.py           # Phase 2 CLI entry point
│   ├── validate_edges.py        # Edge validation suite
│   ├── dissolve_municipal_boundaries.py  # Generate dissolved admin boundary GeoJSON
│   └── build_dense_matrix.py    # Build dense N×N .npy matrix from sparse edges
├── notebooks/
│   ├── 1.0-reference-pipeline-walkthrough.ipynb
│   ├── 1.1-validate-edges.ipynb
│   └── 2.0-validate-metrics.ipynb
├── output/
│   ├── edges/                   # Phase 1 outputs (~253 MB)
│   ├── metrics/                 # Phase 2 per-school metrics (~5.5 MB)
│   ├── aggregations/            # Phase 2 admin summaries (~344 KB)
│   └── dense_matrix/            # Dense distance matrix (~546 MB)
├── documentation/
│   ├── plan.md                  # Overall design and phase roadmap
│   ├── phase_1_sparse_edge_network.md
│   ├── phase_2_accessibility_metrics.md
│   └── osrm_edge_computation.md # OSRM troubleshooting and technical notes
└── keys -> ../paaral_eda/keys   # Symlink to GCS service account credentials
```

## Documentation

- **[Plan](documentation/plan.md)** — full design, decisions, and verification checkpoints
- **[Phase 1: Sparse Edge Network](documentation/phase_1_sparse_edge_network.md)** — pipeline design, OSRM batching strategy, edge schema, and statistics
- **[Phase 2: Accessibility Metrics](documentation/phase_2_accessibility_metrics.md)** — metric definitions, aggregation logic, and key findings
- **[OSRM Edge Computation Notes](documentation/osrm_edge_computation.md)** — URL length limits, sub-batching fix, retry logic, and known gaps in OSM coverage

## Known Limitations

- **OSRM car profile only.** Walking distances are more relevant for elementary school catchments but deferred due to OSM footway coverage gaps.
- **No sea routing.** OSRM does not model ferry routes. Island schools with no road connection appear isolated; this is the correct signal for inter-island separation, but ferry-accessible schools are indistinguishable from genuinely isolated ones.
- **589 schools not attempted.** A small number of schools appear only in OSRM batches that failed at the sub-batch level and were not retried at the individual level. These are likely schools whose coordinates snap to disconnected road segments.
- **2 schools OSRM-failed.** Failed even at single-coordinate retry — coordinates cannot snap to any OSM road.
- **20 km cutoff.** Pairs beyond 20 km road distance are not in the edge table and return no distance (not "infinite" — simply absent). Downstream consumers should treat absence as "no edge computed, not necessarily unreachable."

## See Also

- **[project_coordinates](../project_coordinates)** — the unified school coordinate pipeline that feeds this project
- **[project_paaral](../project_paaral)** — student flow modeling using this distance network

## AI Disclosure

This project was developed with substantial assistance from **Claude** (Anthropic), used as a collaborative coding and technical writing partner throughout the project lifecycle. AI was used for:

- **Architecture design** — iterating on the sparse-vs-dense tradeoff, haversine pre-filter strategy, regional batching approach, and cross-region boundary handling
- **Code implementation** — writing all Python modules and CLI scripts
- **OSRM troubleshooting** — diagnosing URL length limits (HTTP 400 on large regions), designing the sub-batching fix, and implementing retry logic (500 → 100 → 20 → 1)
- **Data quality** — identifying bogus placeholder coordinates in the private school dataset and propagating the upstream fix into the coordinates filter
- **Metric design** — defining isolation score, feeder-level metrics, and desert flag thresholds
- **Validation** — authoring the edge validation suite and interpreting known failure modes
- **Documentation** — drafting all phase design documents, technical notes, and this README

All design decisions, domain context (DepEd administrative structure, ESC program mechanics, island geography), and data interpretation were directed by the human author. The AI did not have independent access to external systems or make unsupervised decisions about data handling.
