# OSRM Edge Computation: Technical Notes

## Overview

This document covers the end-to-end process of computing the sparse school-to-school road distance network for ~57K Philippine schools using OSRM. It records what worked, what failed, the root causes, the fixes applied, and the known limitations of the resulting data.

**Date:** 2026-03-24
**Pipeline:** `scripts/run_region_batch.py --all --cross-region --finalize`
**Total computation time:** 32.5 minutes
**OSRM version:** v6.0.0 (Docker: `ghcr.io/project-osrm/osrm-backend:v6.0.0`)
**OSM data:** `philippines-latest.osm.pbf` from Geofabrik
**Routing profile:** car (driving)

---

## 1. Pipeline Design

### Architecture

The pipeline computes road distances between all schools within 20 km of each other, organized by DepEd region:

1. Load unified coordinates from `project_coordinates` (56,788 schools with valid lat/lon)
2. For each of the 18 regions, compute within-region edges via OSRM Table API
3. For each pair of adjacent regions, compute cross-region boundary edges
4. Combine all edges, tag metadata, generate manifest
5. Save per-region Parquet files + combined `all_edges.parquet`

### OSRM Table API

The OSRM Table API (`/table/v1/driving/`) accepts a list of coordinates and returns an N×M distance matrix. Coordinates are passed in the URL path as semicolon-delimited `lon,lat` pairs. The `sources` and `destinations` parameters (passed as query params) specify which coordinates are origins vs destinations.

### Batching Strategy

Schools are batched as sources (max 500 per request). For small regions (≤2,000 schools), all coordinates are included in every request and only the `sources` parameter varies. For larger regions, both sources and destinations are sub-batched so that each request contains at most 2,000 total coordinates.

---

## 2. Initial Run: What Failed

### Symptom

Region VIII (Eastern Visayas, 4,353 schools) and Region XII (SOCCSKSARGEN, 2,411 schools) returned **0 edges**. Every OSRM batch for these regions failed with HTTP 400 (Bad Request).

### Root Cause: URL Length Limit

The initial implementation sent ALL region coordinates in every request, using the `sources` parameter to batch which schools were origins. For Region VIII, this meant 4,353 coordinate pairs (~130 KB) in the URL path of every request. OSRM has an internal URL length limit and rejected the request entirely with HTTP 400.

Region IV-A (5,533 schools) worked despite being larger because the initial run used `MAX_COORDS_PER_REQUEST = 2500` — but this threshold was set after the first failure. The real issue was that any region above ~2,000 coordinates triggered the limit when combined with the `sources` query parameter overhead.

### Why Other Regions Worked

Regions with fewer than ~2,000 schools (most of them) had URLs short enough to pass OSRM's limit. NCR (1,856 schools) was borderline but succeeded. The largest successful region in the initial design was Region IV-A, which happened to work at the 2,500 threshold but failed at the original uncapped size.

---

## 3. Fix 1: Sub-Batching Sources and Destinations

### Change

Modified `osrm_client.py` to detect when a region has more than `MAX_COORDS_PER_REQUEST` (2,000) total coordinates. In that case, instead of sending all coordinates in every request, the module builds sub-batches of both sources AND destinations:

- Source batch: up to 500 schools
- Destination batch: up to 1,500 schools (2,000 - 500)
- Each request contains only the union of its source and destination coordinates
- Original indices are mapped to positions in the sub-coordinate set

### Result

Region VIII: 0 → 171,519 edges
Region XII: 0 → 66,212 edges
Isolated schools: 5,799 → 1,402

### Remaining Problem

Some sub-batches still failed with HTTP 400. The pattern: every sub-batch containing `dest[0..]` (the first destination batch) failed, while later destination batches succeeded. This meant some source-destination pairs were never computed — not because the sources were bad, but because a problematic coordinate in the first destination batch poisoned those requests.

---

## 4. Fix 2: Retry with Progressive Sub-Batching

### Change

Added retry logic to `osrm_client.py`. When a batch fails, it retries with progressively smaller batch sizes:

1. First attempt: 500 sources (or full sub-batch)
2. Retry 1: 100 sources
3. Retry 2: 20 sources
4. Retry 3: 1 source (single-school request)

If a single-school request still fails, that school is recorded as `osrm_failed`.

### Status Tracking

Each region run now saves a `_status.json` file alongside its edge Parquet, containing:

```json
{
  "region": "Region VIII",
  "succeeded": ["school_id_1", "school_id_2", ...],
  "failed": ["school_id_x", ...],
  "n_edges": 214466,
  "elapsed_s": 210
}
```

During finalization, these status files are aggregated into an `osrm_status` column on the coordinate snapshot:

| Status | Meaning |
|--------|---------|
| `computed` | OSRM successfully processed this school as a source in at least one batch |
| `osrm_failed` | OSRM failed even at batch size 1 — the coordinate cannot snap to any road segment in the OSM data |
| `not_attempted` | The school was never included as a source in any OSRM request (edge case in batching) |

### Result

Region VIII: 171,519 → 214,466 edges
Region XII: 66,212 → 81,554 edges
Isolated schools: 1,402 → 594

---

## 5. Final Results

### Edge Table

| Metric | Value |
|--------|-------|
| Total edges | 10,764,780 |
| Within-region | 8,973,995 |
| Cross-region | 1,790,785 |
| File size | 137.7 MB |
| Mean edges/school | 191.6 |
| Median edges/school | 88 |

### OSRM Status

| Status | Count | % |
|--------|-------|---|
| `computed` | 56,360 | 99.2% |
| `not_attempted` | 426 | 0.8% |
| `osrm_failed` | 2 | 0.004% |

### Isolated Schools (594 total)

An "isolated" school has zero edges in the edge table. This can mean:

1. **Genuinely remote** (`osrm_status = computed`): OSRM processed the school but found no other school within 20 km road distance. These are real findings — mountain schools, small island schools, remote barangays.

2. **Data gap** (`osrm_status = not_attempted`): The school was never included as a source due to batching edge cases. Its distances were never computed. This is a pipeline gap, not a geographic finding.

3. **Unroutable** (`osrm_status = osrm_failed`): The school's coordinates don't snap to any road segment in the OSM data. Either the coordinates are wrong, or the road isn't mapped in OSM.

To distinguish these in analysis:

```python
df = pd.read_parquet("output/edges/schools_unified_snapshot.parquet")
genuinely_isolated = df[(df["osrm_status"] == "computed") & ~df["school_id"].isin(connected_ids)]
data_gap = df[df["osrm_status"].isin(["not_attempted", "osrm_failed"])]
```

---

## 6. Known Limitations

### 6.1 Partial Coverage in Region VIII and XII

These two regions have the highest isolated school rates (10.9% and 14.3% respectively in the initial run, reduced but still elevated after fixes). The root cause is a combination of:

- **OSM road coverage gaps**: Eastern Visayas and SOCCSKSARGEN have less complete road mapping in OpenStreetMap compared to Luzon regions
- **Island fragmentation**: Region VIII spans multiple islands (Leyte, Samar, Biliran, etc.) with limited road bridges between them
- **Poisoned batches**: A single coordinate that can't snap to the road network causes OSRM to reject the entire batch. The retry logic mitigates this but doesn't eliminate it — if the bad coordinate is in the destination batch, all source batches paired with that destination batch fail

### 6.2 The 426 `not_attempted` Schools

These schools have valid coordinates but were never included as a source in any successful OSRM request. They may still appear as *destinations* in other schools' edge computations (i.e., they might have incoming edges but no outgoing edges). This is a batching artifact, not a geographic limitation. A future improvement would be to identify these schools after the main run and compute their edges in a cleanup pass.

### 6.3 No Sea Routing

OSRM routes on road networks only. Schools on different islands with no road bridge between them will have no edge, even if a regular ferry connects them. The `is_sea_separated` column was designed to flag these cases, but it currently reads 0 because the pipeline only stores edges where OSRM returns a valid distance — sea-separated pairs simply don't appear.

The isolation of island schools is itself analytically meaningful and does not require sea routing to detect.

### 6.4 OSM Data Freshness

The road network comes from Geofabrik's `philippines-latest.osm.pbf`, which is a snapshot. New roads, bridges, or highway projects completed after the download date are not reflected. The PBF download date should be checked against the `_manifest.json` for reproducibility.

### 6.5 Car Profile Only

The pipeline uses OSRM's `car.lua` profile, which routes for motor vehicles. Walking distances (relevant for elementary school catchments) would require preprocessing the PBF against `foot.lua` — deferred to a future phase.

---

## 7. Configuration Reference

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `ROAD_CUTOFF_M` | 20,000 (20 km) | Captures virtually all realistic school commuting distances in the Philippines |
| `HAVERSINE_CUTOFF_KM` | 30.0 | Pre-filter generously above road cutoff to account for road detour factors up to 1.5x |
| `MAX_COORDS_PER_REQUEST` | 2,000 | Safe upper bound for OSRM URL length; tested empirically |
| `MAX_SOURCES_PER_BATCH` | 500 | Matches project_paaral's proven batch size |
| `RETRY_SIZES` | [100, 20, 1] | Progressive sub-batching to isolate problematic coordinates |

---

## 8. File Outputs

All outputs are in `output/edges/`:

| File | Description |
|------|-------------|
| `region_*.parquet` | Per-region sparse edge tables (18 files) |
| `region_*_status.json` | Per-region OSRM status: succeeded + failed school_id lists |
| `cross_region_pairs.parquet` | Cross-region boundary edges |
| `all_edges.parquet` | Combined edge table (all regions + cross-region) |
| `schools_unified_snapshot.parquet` | Coordinate snapshot with `osrm_status` column |
| `_manifest.json` | Parameters, statistics, file listing |

### Edge Table Schema

| Column | Type | Description |
|--------|------|-------------|
| `source_id` | str | Origin school_id |
| `target_id` | str | Destination school_id |
| `road_distance_m` | float32 | OSRM driving distance in meters |
| `haversine_distance_m` | float32 | Great-circle distance in meters |
| `road_haversine_ratio` | float32 | Detour factor (road / haversine) |
| `source_region` | str | DepEd region of source school |
| `is_cross_region` | bool | True if source and target are in different regions |
| `is_sea_separated` | bool | True if OSRM returned no route but haversine is within cutoff |

### Coordinate Snapshot Added Column

| Column | Type | Values |
|--------|------|--------|
| `osrm_status` | str | `computed`, `osrm_failed`, `not_attempted` |

---

## 9. Reproducing the Run

```bash
# Start OSRM (from innovation-projects root)
docker compose --profile routing up -d

# Full pipeline (inside experiments-innovations-lab container)
python scripts/run_region_batch.py --all --cross-region --finalize

# Re-run a specific region
python scripts/run_region_batch.py "Region VIII" --force

# Check status
python scripts/run_region_batch.py --list

# Finalize without re-running regions (e.g., after manual fixes)
python scripts/run_region_batch.py --finalize

# Skip GCS upload
python scripts/run_region_batch.py --finalize --no-upload
```
