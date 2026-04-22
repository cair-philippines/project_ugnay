# Prompt: Private School Coordinate Validation for project_coordinates

Use this prompt in a project_coordinates conversation to design and implement coordinate validation for private schools, similar to what exists for public schools.

---

## Context

project_coordinates has a well-defined public school coordinate pipeline with a trust-based priority cascade across 5 sources, spatial PSGC validation via point-in-polygon, and a duplication audit. The private school pipeline (`build_private_coordinates.py`) has a 3-pass coordinate cleaning process (fix swapped lat/lon → reject invalid → reject out-of-bounds), but lacks validation for **coordinates that are valid but wrong** — specifically, placeholder/default values that passed all three cleaning passes.

## The Problem We Found

While building a nationwide school-to-school distance network in project_ugnay, we discovered that **469 private schools** across all 18 DepEd regions share the exact coordinate `(14.57929, 121.06494)` — a point in the San Juan/Pasig area of NCR. These schools are administratively in places like Ilocos Norte, Sultan Kudarat, and Cagayan, but their coordinates all point to the same NCR location. The private school TOSF submission system likely pre-filled this as a default, and these schools never corrected it.

Additional suspect clusters:
- `(14.0, 121.0)`: 16 schools across multiple regions — suspiciously round
- `(14.0, 120.0)`: 9 schools — suspiciously round
- `(15.0, 120.0)`: 7 schools — suspiciously round
- `(14.61789, 121.10269)`: 7 schools — may be a second default value

These coordinates have `coord_status=valid` in the current pipeline output because they pass all three cleaning checks: they're not swapped, not invalid, and not out of bounds. They're just wrong.

## Impact

Any downstream project that uses private school coordinates for spatial analysis (distance computation, catchment mapping, accessibility metrics) will produce incorrect results for these ~500 schools. In project_ugnay, they created bogus edges — a school in Laoag appeared 5 km from a school in San Juan because both share the same fake coordinate.

## What Needs to Be Designed

A validation layer for private school coordinates that detects and flags/rejects coordinates that are technically valid but spatially implausible. This should be added to `build_private_coordinates.py` or as a new module.

### Detection strategies to consider

1. **Duplicate coordinate detection**: Flag coordinates shared by N+ schools that are in different municipalities/provinces/regions. A legitimate shared coordinate (e.g., schools in the same building) would be in the same municipality. Schools in different regions sharing exact coordinates is a clear marker of placeholder data.

2. **Round number detection**: Coordinates like `(14.0, 121.0)` or `(15.0, 120.0)` with zero decimal precision are almost certainly not real GPS readings. Consider rejecting coordinates where both lat and lon have fewer than 2 decimal places.

3. **Administrative boundary cross-check**: Compare each school's coordinate against its declared region/province/municipality. If a school is administratively in Nueva Ecija but its coordinate falls in NCR, the coordinate is suspect. The public pipeline already does this via PSGC point-in-polygon validation (`validate_psgc.py` + barangay shapefiles) — can this be extended to private schools?

4. **Statistical outlier detection per administrative unit**: For each municipality, compute the centroid and spread of its schools' coordinates. A school whose coordinate is far from its municipality's centroid (relative to the municipality's geographic size) is suspect.

### Design questions

- Should suspect coordinates be **rejected** (set to null, treated as no_coords) or **flagged** (kept but marked with a new status like `coord_status=suspect`)?
- Should the validation apply only to the known placeholder values, or be generalized to catch future placeholders too?
- The public pipeline has `psgc_validation` (match/mismatch/no_validation) from point-in-polygon checks. Should private schools get the same treatment? The shapefiles already exist at `data/reference/phl_admbnda_adm4_updated/`.

### Reference files

- Private pipeline: `scripts/build_private_coordinates.py`
- Private pipeline plan: `documentation/private_pipeline_plan.md`
- Private technical notes: `documentation/private_technical_notes.md`
- Public PSGC validation (reference): `modules/validate_psgc.py`
- Barangay shapefiles: `data/reference/phl_admbnda_adm4_updated/`
- Current private output: `data/gold/private_school_coordinates.parquet` (12,167 rows, 8,914 with coordinates)
- Duplication audit: `scripts/duplication_audit.py`, `documentation/duplication_audit.md`

### Current 3-pass cleaning (for reference)

- Pass 1: Fix swapped lat/lon — detects via non-overlapping PH ranges (lat 4.5–21.5, lon 116–127). 105 schools fixed.
- Pass 2: Reject invalid coords — null, non-finite, out-of-Earth-bounds, zero. 505 rejected.
- Pass 3: Reject out-of-PH-bounds coords. 208 rejected.
- Result: 8,914 schools with `coord_status=valid`. The 469 placeholder schools are among these.
