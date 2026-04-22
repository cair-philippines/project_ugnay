# Prompt: Point-in-Land Coordinate Validation for project_coordinates

Use this prompt in a project_coordinates conversation to design and implement spatial validation that catches coordinates which are within Philippine bounds but geographically wrong (over water, or in the wrong municipality).

---

## Context

project_coordinates' public school pipeline validates coordinates with a Philippine bounding box (lat 4.5–21.5, lon 116–127) and the private pipeline adds Pass 4 for placeholder/cluster detection. However, both pipelines miss a class of errors: **coordinates that are within national bounds but clearly wrong for the school's administrative location** — placing schools over water or hundreds of kilometers from their declared municipality.

The private school pipeline was recently updated with Pass 4 (suspect coordinate detection) for placeholders. This prompt addresses a different problem: spatially misplaced coordinates in **both public and private** school data.

## The Problem We Found

While building the project_ugnay platform (nationwide school connectivity map), we found schools whose dots appear over the sea or far from their declared municipality. These passed all existing validation checks because their coordinates are valid, non-null, within PH bounds, and not swapped.

### Public school cases

| school_id | school_name | Municipality (declared) | Coordinates | Expected lon | coord_source |
|-----------|-------------|------------------------|-------------|-------------|--------------|
| 306052 | LAMON NATIONAL HIGH SCHOOL | Goa, Camarines Sur | (14.99, 119.30) | ~123.5 | nsbi_2324 |
| 136992 | New Paradise Elementary School | San Luis, Agusan del Sur | (14.07, 119.26) | ~126 | nsbi_2324 |
| 305986 | City of Bogo Senior High School | City of Bogo, Cebu | (13.11, 116.37) | ~124 | nsbi_2324 |
| 302765 | Don Pablo A. Lorenzo Memorial SHS | Zamboanga City | (13.09, 117.94) | ~122 | nsbi_2324 |
| 500383 | Ungus-Ungus Primary School | Mangaldan, Pangasinan | (4.86, 119.41) | ~120.4 | monitoring_validated |
| 133238 | Pagayawan Elementary School | Balindong, Lanao del Sur | (13.40, 118.52) | ~124 | nsbi_2324 |
| 133746 | Montay PS | Malabang, Lanao del Sur | (5.90, 119.20) | ~124 | nsbi_2324 |
| 134404 | Laud Alu PS | Lugus, Sulu | (9.09, 118.53) | ~120 | nsbi_2324 |

### Private school case

| school_id | school_name | Municipality | Coordinates | coord_status |
|-----------|-------------|-------------|-------------|-------------|
| 410585 | Abraham and Ysak School (AAS), Inc. | City of Imus, Cavite | (14.38, 120.30) | valid |

This school is plotted in Manila Bay. City of Imus has a public-school centroid at (14.41, 120.93) — the school's longitude is 0.63 degrees off.

### Pattern

These are not placeholder coordinates (no clustering). They appear to be individual data entry errors — possibly transposed digits, wrong decimal placement, or copy-paste errors during the NSBI encoding process. The `nsbi_2324` source is the most common origin, but `monitoring_validated` also has cases.

## Impact

On a nationwide map, these schools appear as dots over the sea or far from any landmass, undermining the platform's credibility with planners. More importantly, they generate incorrect distance edges — a school in Camarines Sur with coordinates near Zambales will appear "connected" to Zambales schools and "isolated" from its actual Camarines Sur neighbors.

## Proposed Detection Strategy

### Point-in-polygon validation against municipal boundaries

The most reliable detection: check whether each school's coordinate falls inside its declared municipality's polygon.

**Resources already available:**
- Barangay-level shapefiles: `data/reference/phl_admbnda_adm4_updated/` (42,022 polygons)
- Municipal-level dissolved boundaries: can be produced from the above (project_ugnay already does this)
- PSGC municipality codes: `psgc_municity` column in both public and private school outputs
- GeoJSON `ADM3_PCODE` matches PSGC municipality code (first 7 digits of 10-digit PSGC, prefixed with `PH`)

**Algorithm:**
1. For each school with coordinates, find which municipal polygon its coordinate falls into (spatial join)
2. Compare the polygon's `ADM3_PCODE` with the school's `psgc_municity`
3. If they don't match, the coordinate is in the wrong municipality

This is similar to the existing `psgc_validation` (point-in-polygon against barangay boundaries), but here the goal is to **flag or reject** mismatches rather than just record them for reference.

**Note:** The public pipeline already does `psgc_validation` via `validate_psgc.py`, but the result (`psgc_match`/`psgc_mismatch`/`psgc_no_validation`) is stored as metadata — mismatched schools are NOT rejected or flagged as suspect. The schools listed above have `psgc_validation: psgc_no_validation`, meaning they weren't even checked.

### Fallback: distance-from-centroid check

For schools without PSGC data (640 public, 228 private), compute the distance from the school's coordinate to the centroid of all other schools in its declared municipality. If the distance exceeds a threshold (e.g., 50 km), flag as suspect.

### Water detection (optional)

If a land/sea mask is available (e.g., from the admin boundary shapefiles — any point outside all polygons is over water), this would catch the most visually obvious errors. However, the point-in-polygon approach already handles this since water areas are outside all municipal polygons.

## Design Questions

- Should mismatched coordinates be **rejected** (set to null / coord_status='no_coords') or **flagged** (coord_status='suspect' with a new rejection reason like `psgc_coordinate_mismatch`)?
- The existing `psgc_validation` column already records match/mismatch — should this validation be integrated into the existing PSGC validation step, or be a separate pass?
- Should this apply to both public and private schools? (Yes — both have cases.)
- How should schools with `psgc_no_validation` (no PSGC data) be handled? Fall back to distance-from-centroid, or skip?

## Reference Files

- PSGC validation module: `modules/validate_psgc.py`
- Barangay shapefiles: `data/reference/phl_admbnda_adm4_updated/`
- Public pipeline: `scripts/build_coordinates.py`
- Private pipeline: `scripts/build_private_coordinates.py` (Pass 4 in `modules/load_private_tosf.py`)
- Public output: `data/gold/public_school_coordinates.parquet`
- Private output: `data/gold/private_school_coordinates.parquet`
