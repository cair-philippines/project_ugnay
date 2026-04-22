# Phase 1: Sparse Edge Network

## Purpose

Phase 1 computes road distances between all ~56K Philippine schools with reliable coordinates, producing a sparse edge table of school pairs within 20 km driving distance. This is the foundational dataset for all downstream accessibility analysis and platform visualization.

## Pipeline

**Script:** `scripts/run_region_batch.py`
**Modules:** `coordinates.py`, `osrm_client.py`, `sparse_edges.py`, `inter_island.py`, `gcs_utils.py`

```bash
# Full pipeline
python scripts/run_region_batch.py --all --cross-region --finalize

# Check status
python scripts/run_region_batch.py --list
```

### Steps

1. **Load coordinates** — merges public + private school coordinates from `project_coordinates`. Excludes private schools with `coord_status = "suspect"` (placeholder/bogus coordinates).
2. **Within-region edges** — for each of the 18 DepEd regions, computes OSRM driving distances between all school pairs within 30 km haversine, keeps edges ≤ 20 km road distance.
3. **Cross-region edges** — for adjacent region pairs, finds boundary-zone schools and computes OSRM distances between them.
4. **Finalize** — combines all edge files, tags sea-separated pairs, assigns `osrm_status` to each school, generates manifest, optionally uploads to GCS.

### Parameters

| Parameter | Value |
|-----------|-------|
| Road distance cutoff | 20 km |
| Haversine pre-filter | 30 km |
| OSRM profile | car (driving) |
| Max coordinates per request | 2,000 |
| Max sources per batch | 500 |
| Retry batch sizes | 100 → 20 → 1 |

## Outputs

All files are in `output/edges/`.

### Edge Table — `all_edges.parquet`

The primary output. Each row is a directed edge from one school to another within 20 km driving distance.

| Column | Type | Description |
|--------|------|-------------|
| `source_id` | str | Origin school_id |
| `target_id` | str | Destination school_id |
| `road_distance_m` | float32 | OSRM driving distance in meters |
| `haversine_distance_m` | float32 | Great-circle distance in meters |
| `road_haversine_ratio` | float32 | Detour factor (road ÷ haversine). Higher = more winding road. |
| `source_region` | str | DepEd region of source school |
| `is_cross_region` | bool | True if source and target are in different regions |
| `is_sea_separated` | bool | True if within haversine cutoff but no road route exists |

**Size:** ~9.9M rows, 128 MB

### Coordinate Snapshot — `schools_unified_snapshot.parquet`

Snapshot of all schools used in the computation, with two added columns.

Inherits all columns from `project_coordinates` (school_id, school_name, latitude, longitude, region, province, municipality, sector, offers_es/jhs/shs, etc.) plus:

| Column | Type | Values |
|--------|------|--------|
| `osrm_status` | str | `computed` — OSRM successfully routed this school |
| | | `osrm_failed` — OSRM failed even at single-coordinate retry |
| | | `not_attempted` — school was never a source in any successful request |
| `island_group` | str | `Luzon`, `Visayas`, or `Mindanao` |

**Size:** 56,018 rows

### Per-Region Files

| File | Description |
|------|-------------|
| `region_*.parquet` (×18) | Edge table for a single region |
| `region_*_status.json` (×18) | OSRM succeeded/failed school_id lists for that region |
| `cross_region_pairs.parquet` | Boundary edges between adjacent regions |

### Manifest — `_manifest.json`

Records parameters, statistics, and file listing for reproducibility.

## Key Statistics (as of 2026-03-25)

| Metric | Value |
|--------|-------|
| Schools in pipeline | 56,018 (47,874 public + 8,144 private) |
| Private schools excluded (suspect coords) | 770 |
| Total edges | 9,880,568 |
| Within-region edges | 8,615,356 |
| Cross-region edges | 1,265,212 |
| Connected schools | 55,428 (99.0%) |
| Isolated schools | 590 (1.0%) |
| — Genuinely isolated (computed, 0 edges) | 162 |
| — Not attempted (batching artifact) | 426 |
| — OSRM failed (unroutable coordinate) | 2 |
| Median road distance | 13.2 km |
| Mean road distance | 12.5 km |
| Median detour factor | 1.38× |
| Median edges per school | 88 |

## How to Use

### Load the edge table

```python
import pandas as pd

df_edges = pd.read_parquet("output/edges/all_edges.parquet")
df_schools = pd.read_parquet("output/edges/schools_unified_snapshot.parquet")
```

### Find neighbors of a school

```python
school_id = "136434"
neighbors = df_edges[df_edges["source_id"] == school_id].sort_values("road_distance_m")
print(f"{len(neighbors)} neighbors within 20 km")
print(neighbors[["target_id", "road_distance_m"]].head(10))
```

### Filter by distance band

```python
within_5km = df_edges[df_edges["road_distance_m"] <= 5000]
within_10km = df_edges[df_edges["road_distance_m"] <= 10000]
```

### Identify isolated schools

```python
connected_ids = set(df_edges["source_id"]) | set(df_edges["target_id"])
all_ids = set(df_schools["school_id"])
isolated = df_schools[~df_schools["school_id"].isin(connected_ids)]

# Distinguish genuine isolation from data gaps
genuine = isolated[isolated["osrm_status"] == "computed"]
data_gap = isolated[isolated["osrm_status"] != "computed"]
```

### Filter to feeder school pairs (ES → JHS)

```python
es_schools = set(df_schools[df_schools["offers_es"] == True]["school_id"])
jhs_schools = set(df_schools[df_schools["offers_jhs"] == True]["school_id"])
feeder_edges = df_edges[
    df_edges["source_id"].isin(es_schools) & df_edges["target_id"].isin(jhs_schools)
]
```

## Dependencies

- **project_coordinates** — provides `public_school_coordinates.parquet` and `private_school_coordinates.parquet`
- **OSRM** — Docker service at `http://osrm:5000` with Philippines PBF preprocessed against car.lua profile

## Related Documentation

- [OSRM Edge Computation: Technical Notes](osrm_edge_computation.md) — troubleshooting, error handling, retry logic
- [Design Plan](plan.md) — overall project_ugnay architecture across all phases
