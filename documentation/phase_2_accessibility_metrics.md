# Phase 2: Accessibility Metrics and Aggregations

## Purpose

Phase 2 transforms the sparse edge table from Phase 1 into interpretable per-school accessibility metrics and administrative-level summaries. These outputs answer questions like "how isolated is this school?", "where are private school deserts?", and "which municipalities have the worst ESC coverage?" — and they power the choropleth layers in the Phase 3 platform.

## Pipeline

**Script:** `scripts/run_metrics.py`
**Modules:** `accessibility_metrics.py`, `aggregation.py`

```bash
# Compute metrics + aggregations
python scripts/run_metrics.py --no-upload

# With GCS upload
python scripts/run_metrics.py
```

**Prerequisite:** Phase 1 must be complete (`output/edges/all_edges.parquet`).
**Runtime:** ~55 seconds for metrics, <1 second for aggregations.

## Outputs

### Per-School Metrics — `output/metrics/school_accessibility.parquet`

One row per school (56,018 rows). Inherits school attributes from the coordinate snapshot, plus computed metrics.

#### Inherited columns

| Column | Description |
|--------|-------------|
| `school_id` | Primary key |
| `school_name` | School name |
| `latitude`, `longitude` | Coordinates |
| `region`, `province`, `municipality` | Administrative location |
| `sector` | `public` or `private` |
| `offers_es`, `offers_jhs`, `offers_shs` | Curricular offerings |
| `urban_rural` | Urban/Rural classification |
| `enrollment_status` | Active or no_enrollment_reported |
| `osrm_status` | `computed`, `osrm_failed`, or `not_attempted` |
| `island_group` | `Luzon`, `Visayas`, or `Mindanao` |

#### Computed metrics

| Column | Type | Description | Interpretability |
|--------|------|-------------|-----------------|
| `n_neighbors_5km` | int | Schools within 5 km driving | "X schools are within a short commute" |
| `n_neighbors_10km` | int | Schools within 10 km driving | Direct count |
| `n_neighbors_20km` | int | Schools within 20 km driving | Direct count |
| `n_public_5km` | int | Public schools within 5 km | Direct count |
| `n_public_10km` | int | Public schools within 10 km | Direct count |
| `n_public_20km` | int | Public schools within 20 km | Direct count |
| `n_private_5km` | int | Private schools within 5 km | Direct count |
| `n_private_10km` | int | Private schools within 10 km | Direct count |
| `n_private_20km` | int | Private schools within 20 km | Direct count |
| `nearest_private_km` | float | Road distance to closest private school | "Nearest private school is X km away" |
| `nearest_private_id` | str | School ID of nearest private school | For drill-down |
| `nearest_public_km` | float | Road distance to closest public school | Same interpretation |
| `nearest_esc_km` | float | Road distance to closest ESC-participating private school | "Nearest ESC school is X km away" |
| `nearest_esc_id` | str | School ID of nearest ESC school | For drill-down |
| `mean_road_haversine_ratio` | float | Average detour factor to all neighbors | "Roads here are X times longer than straight-line" |
| `private_desert` | bool | No private school within 10 km driving | Binary flag |
| `esc_desert` | bool | No ESC-participating school within 10 km driving | Binary flag |
| `isolation_score` | float | Composite score from 0 (connected) to 1 (isolated) | Gradient for choropleth |

#### Level-aware feeder metrics

These capture educationally meaningful isolation: can a school's learners feed forward to the next level?

| Column | Type | Description | Interpretability |
|--------|------|-------------|-----------------|
| `self_feeds_jhs` | bool | School offers both ES and JHS | "Graduates stay in the same school for JHS" |
| `nearest_jhs_km` | float | Road km to nearest JHS-offering school (0 if self-feeds) | "Nearest JHS is X km away" |
| `nearest_jhs_id` | str | School ID of nearest JHS school | For drill-down |
| `n_jhs_5km` | int | JHS-offering schools within 5 km | Direct count |
| `n_jhs_10km` | int | JHS-offering schools within 10 km | Direct count |
| `n_jhs_20km` | int | JHS-offering schools within 20 km | Direct count |
| `jhs_desert` | bool | ES school with no JHS within 10 km and doesn't self-feed | "Grade 6 graduates have no JHS nearby" |
| `self_feeds_shs` | bool | School offers both JHS and SHS | "Graduates stay for SHS" |
| `nearest_shs_km` | float | Road km to nearest SHS-offering school (0 if self-feeds) | "Nearest SHS is X km away" |
| `nearest_shs_id` | str | School ID of nearest SHS school | For drill-down |
| `n_shs_5km` | int | SHS-offering schools within 5 km | Direct count |
| `n_shs_10km` | int | SHS-offering schools within 10 km | Direct count |
| `n_shs_20km` | int | SHS-offering schools within 20 km | Direct count |
| `shs_desert` | bool | JHS school with no SHS within 10 km and doesn't self-feed | "Grade 10 graduates have no SHS nearby" |
| `feeder_isolation_score` | float | Feeder-aware isolation, 0 (well-connected) to 1 (no destination) | "How hard is it for graduates to continue?" |

**Applicability:** Level-aware metrics are only meaningful for schools that offer the source level. `jhs_desert` is only True for schools that offer ES. `shs_desert` is only True for schools that offer JHS. Schools that don't offer the relevant level have `NaN` for nearest distances and `False` for desert flags (not applicable, not "well-connected").

#### Isolation score formula

```
isolation_score = 0.5 × (1 / (1 + n_neighbors_5km))
               + 0.3 × (1 / (1 + n_neighbors_10km))
               + 0.2 × (1 / (1 + n_neighbors_20km))
```

Weights reflect that nearby connectivity matters more than distant connectivity. A school with 0 neighbors at all bands scores 1.0. A school with 100 neighbors at 5 km scores ~0.005.

#### Feeder isolation score formula

```
For ES schools:  es_score = 0.5/(1+n_jhs_5km) + 0.3/(1+n_jhs_10km) + 0.2/(1+n_jhs_20km)
For JHS schools: jhs_score = 0.5/(1+n_shs_5km) + 0.3/(1+n_shs_10km) + 0.2/(1+n_shs_20km)
```

Self-feeding schools add 1 to all counts (equivalent to having a destination at distance 0). Schools offering both ES and JHS get the average of es_score and jhs_score. SHS-only or no-offering schools score 0.0 (not applicable).

#### Special values

- `nearest_private_km = inf`: No private school within 20 km (the edge table cutoff). The actual nearest may be beyond 20 km.
- `nearest_esc_km = inf`: No ESC school within 20 km.
- `isolation_score = 1.0`: School has zero edges (isolated or not_attempted by OSRM).

### Administrative Aggregations

Three summary tables, one per administrative level. All aggregate **public school** metrics (private school isolation is less meaningful for planning).

#### Municipal — `output/aggregations/municipal_summary.parquet` (1,752 rows)

| Column | Type | Description |
|--------|------|-------------|
| `region`, `province`, `municipality` | str | Administrative unit |
| `n_schools` | int | Total schools (public + private) |
| `n_public`, `n_private`, `n_esc` | int | School counts by type |
| `mean_isolation_score` | float | Mean isolation score of public schools |
| `max_isolation_score` | float | Most isolated public school |
| `median_isolation_score` | float | Median isolation of public schools |
| `pct_private_desert` | float | % of public schools with no private school within 10 km |
| `pct_esc_desert` | float | % of public schools with no ESC school within 10 km |
| `mean_nearest_private_km` | float | Average distance to nearest private school |
| `median_nearest_private_km` | float | Median distance to nearest private school |
| `mean_nearest_esc_km` | float | Average distance to nearest ESC school |
| `median_nearest_esc_km` | float | Median distance to nearest ESC school |
| `mean_neighbors_5km` | float | Mean neighbor count at 5 km |
| `mean_neighbors_10km` | float | Mean neighbor count at 10 km |
| `mean_neighbors_20km` | float | Mean neighbor count at 20 km |
| `mean_road_haversine_ratio` | float | Mean road detour factor |
| `centroid_lat`, `centroid_lon` | float | Mean coordinates (for map placement) |
| `level` | str | Always `"municipal"` |

#### Provincial — `output/aggregations/provincial_summary.parquet` (139 rows)

Same schema, grouped by region + province.

#### Regional — `output/aggregations/regional_summary.parquet` (18 rows)

Same schema, grouped by region.

## Key Statistics (as of 2026-03-25)

### General connectivity

| Metric | Value |
|--------|-------|
| Schools with metrics | 56,018 |
| Mean isolation score | 0.1186 |
| Median isolation score | 0.0707 |
| Private deserts | 16,943 (30.2%) |
| ESC deserts | 22,495 (40.2%) |
| Median nearest private school | 4.0 km |
| Median nearest ESC school | 5.2 km |

### Level-aware feeder metrics (public schools)

| Metric | Value |
|--------|-------|
| Public ES schools | 38,908 |
| ES-only (need external JHS) | 36,530 |
| Self-feeds ES→JHS | 2,378 |
| JHS desert (ES schools with no JHS within 10 km) | 922 (2.4%) |
| ES-only nearest JHS (median) | 2.2 km |
| ES-only with no JHS within 20 km | 148 |
| Public JHS schools | 10,456 |
| JHS without SHS (need external SHS) | 2,882 |
| Self-feeds JHS→SHS | 7,574 |
| SHS desert (JHS schools with no SHS within 10 km) | 254 (2.4%) |
| JHS w/o SHS nearest SHS (median) | 3.9 km |
| JHS w/o SHS with no SHS within 20 km | 30 |
| Mean feeder isolation score | 0.2757 |
| Median feeder isolation score | 0.2354 |

### JHS desert rate by region (highest)

| Region | ES schools | JHS desert | % |
|--------|-----------|------------|---|
| BARMM | 2,135 | 125 | 5.9% |
| CAR | 1,531 | 89 | 5.8% |
| MIMAROPA | 1,896 | 86 | 4.5% |
| Region II | 2,202 | 90 | 4.1% |
| NCR | 519 | 0 | 0.0% |

### Administrative aggregations

| Level | Units |
|-------|-------|
| Municipal | 1,752 |
| Provincial | 139 |
| Regional | 18 |

## How to Use

### Load metrics

```python
import pandas as pd

df = pd.read_parquet("output/metrics/school_accessibility.parquet")
df_muni = pd.read_parquet("output/aggregations/municipal_summary.parquet")
```

### Find private school deserts

```python
# Public schools with no private school within 10 km
deserts = df[(df["sector"] == "public") & (df["private_desert"] == True)]
print(f"{len(deserts):,} public schools are in private deserts")

# Municipalities where ALL public schools are in private deserts
full_deserts = df_muni[df_muni["pct_private_desert"] == 100]
print(f"{len(full_deserts):,} municipalities are complete private deserts")
```

### Find ESC coverage gaps

```python
# Public schools far from any ESC school
far_from_esc = df[(df["sector"] == "public") & (df["nearest_esc_km"] > 10)]
print(f"{len(far_from_esc):,} public schools are >10 km from nearest ESC school")
```

### Rank municipalities for intervention

```python
# Worst municipalities by ESC desert rate (min 10 public schools)
priority = df_muni[df_muni["n_public"] >= 10].nlargest(20, "pct_esc_desert")
print(priority[["region", "municipality", "n_public", "pct_esc_desert", "mean_nearest_esc_km"]])
```

### Filter by school level for feeder analysis

```python
# ES-only schools in private deserts (no JHS/SHS options nearby)
es_deserts = df[
    (df["offers_es"] == True) &
    (df["offers_jhs"] != True) &
    (df["private_desert"] == True)
]
```

## Dependencies

- **Phase 1 outputs** — `output/edges/all_edges.parquet` and `schools_unified_snapshot.parquet`
- No OSRM needed — this phase is pure computation on the edge table

## Related Documentation

- [Phase 1: Sparse Edge Network](phase_1_sparse_edge_network.md) — how the edge table was built
- [Design Plan](plan.md) — overall project_ugnay architecture
