"""
Load and merge public + private school coordinates from project_coordinates.

Reads the canonical parquet outputs and returns a unified DataFrame with
consistent columns for downstream edge computation.

Uses PSGC-standardized location names (psgc_province_name, psgc_municity_name)
for consistency, falling back to raw columns when PSGC data is missing.
Applies Philippine bounding box filter to exclude erroneous coordinates.
"""

from pathlib import Path

import numpy as np
import pandas as pd


# Default path to project_coordinates output
COORDINATES_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "project_coordinates"
    / "data"
    / "gold"
)

# Philippine bounding box (same bounds used by private school pipeline Pass 3)
PH_LAT_MIN, PH_LAT_MAX = 4.5, 21.5
PH_LON_MIN, PH_LON_MAX = 116.0, 127.0

# Rejection reasons that make coordinates unusable for distance computation.
# Schools with these reasons are excluded. All other reasons (None,
# wrong_municipality, round_coordinates) are included — their coordinates
# are imprecise but real.
_EXCLUDE_REJECTION_REASONS = {
    "placeholder_default",
    "coordinate_cluster",
    "outside_all_polygons",
    "no_coordinate_source",
    "no_submission",
    "invalid",
    "out_of_bounds",
    "not_in_lis",
}

# Columns to read from source parquets (superset — not all may exist)
_READ_COLS = [
    "school_id", "school_name", "latitude", "longitude",
    # Raw location (fallback)
    "region", "province", "municipality", "barangay",
    # PSGC-standardized location
    "psgc_region", "psgc_region_name",
    "psgc_province", "psgc_province_name",
    "psgc_municity", "psgc_municity_name",
    "psgc_barangay_name",
    # Coordinate quality
    "coord_rejection_reason",
    # School characteristics
    "offers_es", "offers_jhs", "offers_shs",
    "urban_rural", "enrollment_status",
]

# Additional columns from private school coordinates
_PRIVATE_EXTRA_COLS = [
    "esc_participating", "shsvp_participating", "jdvp_participating",
]


def _standardize_location(df):
    """
    Use PSGC-standardized names where available, fall back to raw columns.
    For schools without PSGC data, harmonize names to the dominant spelling
    in their region+province group to avoid casing duplicates.
    """
    # Province: prefer psgc_province_name (Title Case, consistent)
    if "psgc_province_name" in df.columns:
        df["province"] = df["psgc_province_name"].fillna(df["province"])

    # Municipality: prefer psgc_municity_name
    if "psgc_municity_name" in df.columns:
        df["municipality"] = df["psgc_municity_name"].fillna(df["municipality"])

    # Barangay: prefer psgc_barangay_name
    if "psgc_barangay_name" in df.columns:
        df["barangay"] = df["psgc_barangay_name"].fillna(df.get("barangay", pd.Series(dtype=str)))

    # Harmonize remaining casing mismatches: for each region+UPPER(province),
    # pick the most common spelling and apply it to all schools in that group
    df = _harmonize_column(df, group_cols=["region"], target_col="province")
    df = _harmonize_column(df, group_cols=["region", "province"], target_col="municipality")

    return df


def _harmonize_column(df, group_cols, target_col):
    """
    Within each group, if the same value appears with different casing
    (e.g., 'ABRA' and 'Abra'), replace all with the most common spelling.
    """
    if target_col not in df.columns:
        return df

    df["_upper"] = df[target_col].astype(str).str.upper().str.strip()
    valid_groups = [c for c in group_cols if c in df.columns]
    key_cols = valid_groups + ["_upper"]

    # Find the dominant spelling per group
    dominant = (
        df.dropna(subset=[target_col])
        .groupby(key_cols)[target_col]
        .agg(lambda x: x.value_counts().index[0])
        .rename("_dominant")
        .reset_index()
    )

    df = df.merge(dominant, on=key_cols, how="left")
    mask = df["_dominant"].notna()
    df.loc[mask, target_col] = df.loc[mask, "_dominant"]
    df = df.drop(columns=["_upper", "_dominant"])
    return df


def load_unified(coordinates_dir=None):
    """
    Load and merge public + private school coordinates into a single DataFrame.

    Parameters
    ----------
    coordinates_dir : Path or str, optional
        Path to project_coordinates/data/gold/. Defaults to the sibling
        project in the workspace.

    Returns
    -------
    pd.DataFrame
        Unified coordinates. Only schools with valid coordinates within
        Philippine bounds are included.
    """
    if coordinates_dir is None:
        coordinates_dir = COORDINATES_DIR
    coordinates_dir = Path(coordinates_dir)

    # --- Public schools ---
    pub_path = coordinates_dir / "public_school_coordinates.parquet"
    df_pub = pd.read_parquet(pub_path)

    read_cols = [c for c in _READ_COLS if c in df_pub.columns]
    df_pub = df_pub[read_cols].copy()
    df_pub["sector"] = "public"
    df_pub["esc_participating"] = np.nan
    df_pub["shsvp_participating"] = np.nan
    df_pub["jdvp_participating"] = np.nan

    # --- Private schools ---
    prv_path = coordinates_dir / "private_school_coordinates.parquet"
    df_prv = pd.read_parquet(prv_path)

    prv_cols = [c for c in _READ_COLS + _PRIVATE_EXTRA_COLS if c in df_prv.columns]
    df_prv = df_prv[prv_cols].copy()
    df_prv["sector"] = "private"

    # --- Merge ---
    df = pd.concat([df_pub, df_prv], ignore_index=True)

    # --- Filter by coord_rejection_reason (applies to both sectors) ---
    if "coord_rejection_reason" in df.columns:
        excluded = df["coord_rejection_reason"].isin(_EXCLUDE_REJECTION_REASONS)
        n_excluded = excluded.sum()
        if n_excluded > 0:
            # Report by reason
            reasons = df.loc[excluded, "coord_rejection_reason"].value_counts()
            print(f"  Excluded {n_excluded:,} schools by coord_rejection_reason:")
            for reason, count in reasons.items():
                print(f"    {reason}: {count:,}")
        df = df[~excluded].copy()

    # Ensure school_id is string
    df["school_id"] = df["school_id"].astype(str).str.strip()

    # --- Standardize location names ---
    df = _standardize_location(df)

    # --- Filter to valid coordinates ---
    valid_coords = df["latitude"].notna() & df["longitude"].notna()
    n_no_coords = (~valid_coords).sum()
    df = df.loc[valid_coords].copy()

    # --- Filter to Philippine bounding box ---
    in_bounds = (
        (df["latitude"] >= PH_LAT_MIN) & (df["latitude"] <= PH_LAT_MAX) &
        (df["longitude"] >= PH_LON_MIN) & (df["longitude"] <= PH_LON_MAX)
    )
    n_out_of_bounds = (~in_bounds).sum()
    df = df.loc[in_bounds].copy()

    # --- Deduplicate by school_id ---
    n_before = len(df)
    df = df.drop_duplicates(subset="school_id", keep="first")
    n_dupes = n_before - len(df)

    # --- Output columns ---
    # Keep a clean set of columns in consistent order
    output_cols = [
        "school_id", "school_name", "latitude", "longitude",
        "region", "province", "municipality", "barangay", "sector",
        "offers_es", "offers_jhs", "offers_shs",
        "urban_rural", "enrollment_status",
        "esc_participating", "shsvp_participating", "jdvp_participating",
        "psgc_region", "psgc_province", "psgc_municity",
    ]
    output_cols = [c for c in output_cols if c in df.columns]
    df = df[output_cols]

    print(f"Loaded {len(df):,} schools with valid coordinates")
    print(f"  Public:  {(df['sector'] == 'public').sum():,}")
    print(f"  Private: {(df['sector'] == 'private').sum():,}")
    if n_no_coords > 0:
        print(f"  Dropped {n_no_coords:,} schools without coordinates")
    if n_out_of_bounds > 0:
        print(f"  Dropped {n_out_of_bounds:,} schools outside Philippine bounds")
    if n_dupes > 0:
        print(f"  Removed {n_dupes:,} duplicate school_ids")

    return df.reset_index(drop=True)


def get_region_schools(df, region):
    """Return subset of schools in a given region."""
    return df[df["region"] == region].copy()


def list_regions(df):
    """Return sorted list of unique regions."""
    return sorted(r for r in df["region"].dropna().unique().tolist() if r.strip())
