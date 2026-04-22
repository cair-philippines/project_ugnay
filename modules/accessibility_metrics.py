"""
Per-school accessibility metrics computed from the sparse edge table.

Two layers of metrics:
1. General connectivity — neighbor counts, nearest private/ESC, desert flags
2. Level-aware feeder isolation — can ES graduates reach JHS? Can JHS reach SHS?

The level-aware metrics capture the educationally meaningful definition of
isolation: a school is isolated if its learners cannot feed forward to the
next level within reachable distance.
"""

import numpy as np
import pandas as pd


# Distance bands in meters
DISTANCE_BANDS_M = [5_000, 10_000, 20_000]


def _fix_bool_strings(df, cols):
    """Convert string 'True'/'False' columns to actual booleans."""
    for c in cols:
        if c in df.columns and df[c].dtype == object:
            df[c] = df[c].map({"True": True, "False": False})
            df[c] = df[c].fillna(False).astype(bool)
    return df


def compute_metrics(df_edges, df_schools):
    """
    Compute per-school accessibility metrics from the edge table.

    Parameters
    ----------
    df_edges : pd.DataFrame
        Sparse edge table (all_edges.parquet).
    df_schools : pd.DataFrame
        Unified school coordinates (schools_unified_snapshot.parquet).

    Returns
    -------
    pd.DataFrame
        One row per school with general + level-aware accessibility metrics.
    """
    # Fix boolean columns that may be stored as strings
    df_schools = df_schools.copy()
    df_schools = _fix_bool_strings(df_schools, ["offers_es", "offers_jhs", "offers_shs"])

    # Build lookup sets
    all_ids = set(df_schools["school_id"])
    private_ids = set(df_schools[df_schools["sector"] == "private"]["school_id"])
    public_ids = set(df_schools[df_schools["sector"] == "public"]["school_id"])
    esc_ids = set(
        df_schools[
            (df_schools["sector"] == "private")
            & (df_schools.get("esc_participating", pd.Series(dtype=float)) == 1)
        ]["school_id"]
    )

    # Level-aware sets
    jhs_ids = set(df_schools[df_schools["offers_jhs"] == True]["school_id"])
    shs_ids = set(df_schools[df_schools["offers_shs"] == True]["school_id"])

    # Per-school offering lookup
    school_offers = df_schools.set_index("school_id")[
        ["offers_es", "offers_jhs", "offers_shs"]
    ].to_dict("index")

    # Build target type flags on edges
    edges = df_edges[["source_id", "target_id", "road_distance_m", "road_haversine_ratio"]].copy()
    edges["target_is_private"] = edges["target_id"].isin(private_ids)
    edges["target_is_public"] = edges["target_id"].isin(public_ids)
    edges["target_is_esc"] = edges["target_id"].isin(esc_ids)
    edges["target_offers_jhs"] = edges["target_id"].isin(jhs_ids)
    edges["target_offers_shs"] = edges["target_id"].isin(shs_ids)

    # --- Aggregate per source school ---
    metrics = []
    grouped = edges.groupby("source_id")

    for school_id in all_ids:
        row = {"school_id": school_id}
        offers = school_offers.get(school_id, {})
        src_es = offers.get("offers_es", False)
        src_jhs = offers.get("offers_jhs", False)
        src_shs = offers.get("offers_shs", False)

        if school_id not in grouped.groups:
            # No edges — fill defaults
            _fill_no_edges(row, src_es, src_jhs, src_shs)
            metrics.append(row)
            continue

        school_edges = grouped.get_group(school_id)

        # --- General connectivity metrics ---
        _compute_general_metrics(row, school_edges, private_ids)

        # --- Level-aware feeder metrics ---
        _compute_feeder_metrics(
            row, school_edges, src_es, src_jhs, src_shs
        )

        metrics.append(row)

    df_metrics = pd.DataFrame(metrics)

    # Join school attributes
    attr_cols = [
        "school_id", "school_name", "latitude", "longitude",
        "region", "province", "municipality", "sector",
        "offers_es", "offers_jhs", "offers_shs",
        "urban_rural", "enrollment_status", "osrm_status",
        "island_group", "psgc_municity",
    ]
    attr_cols = [c for c in attr_cols if c in df_schools.columns]
    school_attrs = df_schools[attr_cols].copy()

    df_metrics = school_attrs.merge(df_metrics, on="school_id", how="left")

    # Cast types
    int_cols = [c for c in df_metrics.columns if c.startswith("n_")]
    for c in int_cols:
        df_metrics[c] = df_metrics[c].fillna(0).astype(int)

    bool_cols = [c for c in df_metrics.columns if c.endswith("_desert") or c == "self_feeds_jhs" or c == "self_feeds_shs"]
    for c in bool_cols:
        if c in df_metrics.columns:
            df_metrics[c] = df_metrics[c].fillna(c.startswith("self_feeds") == False).astype(bool)

    float_cols = ["isolation_score", "feeder_isolation_score"]
    for c in float_cols:
        if c in df_metrics.columns:
            df_metrics[c] = df_metrics[c].fillna(1.0).astype(float)

    return df_metrics


def _fill_no_edges(row, src_es, src_jhs, src_shs):
    """Fill metrics for a school with zero edges."""
    for band_m in DISTANCE_BANDS_M:
        band_km = band_m // 1000
        row[f"n_neighbors_{band_km}km"] = 0
        row[f"n_public_{band_km}km"] = 0
        row[f"n_private_{band_km}km"] = 0

    row["nearest_private_km"] = np.inf
    row["nearest_public_km"] = np.inf
    row["nearest_esc_km"] = np.inf
    row["nearest_private_id"] = None
    row["nearest_esc_id"] = None
    row["mean_road_haversine_ratio"] = np.nan
    row["private_desert"] = True
    row["esc_desert"] = True
    row["isolation_score"] = 1.0

    # Level-aware defaults
    row["self_feeds_jhs"] = src_es and src_jhs
    row["self_feeds_shs"] = src_jhs and src_shs
    row["nearest_jhs_km"] = 0.0 if (src_es and src_jhs) else np.inf
    row["nearest_jhs_id"] = None
    row["n_jhs_5km"] = 0
    row["n_jhs_10km"] = 0
    row["n_jhs_20km"] = 0
    row["jhs_desert"] = not (src_es and src_jhs) if src_es else False
    row["nearest_shs_km"] = 0.0 if (src_jhs and src_shs) else np.inf
    row["nearest_shs_id"] = None
    row["n_shs_5km"] = 0
    row["n_shs_10km"] = 0
    row["n_shs_20km"] = 0
    row["shs_desert"] = not (src_jhs and src_shs) if src_jhs else False
    row["feeder_isolation_score"] = _compute_feeder_isolation(
        row, src_es, src_jhs, src_shs
    )


def _compute_general_metrics(row, school_edges, private_ids):
    """Compute general connectivity metrics for one school."""
    # Neighbor counts at distance bands
    for band_m in DISTANCE_BANDS_M:
        band_km = band_m // 1000
        within = school_edges[school_edges["road_distance_m"] <= band_m]
        row[f"n_neighbors_{band_km}km"] = len(within)
        row[f"n_public_{band_km}km"] = int(within["target_is_public"].sum())
        row[f"n_private_{band_km}km"] = int(within["target_is_private"].sum())

    # Nearest private school
    prv_edges = school_edges[school_edges["target_is_private"]]
    if len(prv_edges) > 0:
        nearest = prv_edges.loc[prv_edges["road_distance_m"].idxmin()]
        row["nearest_private_km"] = nearest["road_distance_m"] / 1000
        row["nearest_private_id"] = nearest["target_id"]
    else:
        row["nearest_private_km"] = np.inf
        row["nearest_private_id"] = None

    # Nearest public school
    pub_edges = school_edges[school_edges["target_is_public"]]
    if len(pub_edges) > 0:
        row["nearest_public_km"] = pub_edges["road_distance_m"].min() / 1000
    else:
        row["nearest_public_km"] = np.inf

    # Nearest ESC school
    esc_edges = school_edges[school_edges["target_is_esc"]]
    if len(esc_edges) > 0:
        nearest = esc_edges.loc[esc_edges["road_distance_m"].idxmin()]
        row["nearest_esc_km"] = nearest["road_distance_m"] / 1000
        row["nearest_esc_id"] = nearest["target_id"]
    else:
        row["nearest_esc_km"] = np.inf
        row["nearest_esc_id"] = None

    # Road detour factor
    valid_ratios = school_edges["road_haversine_ratio"].dropna()
    row["mean_road_haversine_ratio"] = (
        float(valid_ratios.mean()) if len(valid_ratios) > 0 else np.nan
    )

    # Desert flags
    row["private_desert"] = row["n_private_10km"] == 0
    row["esc_desert"] = int(
        (school_edges["target_is_esc"] & (school_edges["road_distance_m"] <= 10_000)).sum()
    ) == 0

    # General isolation score
    n5 = row["n_neighbors_5km"]
    n10 = row["n_neighbors_10km"]
    n20 = row["n_neighbors_20km"]
    row["isolation_score"] = (
        0.5 * (1 / (1 + n5))
        + 0.3 * (1 / (1 + n10))
        + 0.2 * (1 / (1 + n20))
    )


def _compute_feeder_metrics(row, school_edges, src_es, src_jhs, src_shs):
    """
    Compute level-aware feeder metrics for one school.

    ES→JHS: If this school offers ES, how accessible is JHS?
    JHS→SHS: If this school offers JHS, how accessible is SHS?
    Self-feeding: If the school offers both levels, distance is 0.
    """
    # --- ES → JHS transition ---
    row["self_feeds_jhs"] = bool(src_es and src_jhs)

    if src_es:
        if src_jhs:
            # Self-feeding: offers both ES and JHS
            row["nearest_jhs_km"] = 0.0
            row["nearest_jhs_id"] = None  # self
        else:
            # Needs external JHS
            jhs_edges = school_edges[school_edges["target_offers_jhs"]]
            if len(jhs_edges) > 0:
                nearest = jhs_edges.loc[jhs_edges["road_distance_m"].idxmin()]
                row["nearest_jhs_km"] = nearest["road_distance_m"] / 1000
                row["nearest_jhs_id"] = nearest["target_id"]
            else:
                row["nearest_jhs_km"] = np.inf
                row["nearest_jhs_id"] = None

        # JHS counts at distance bands
        jhs_edges_all = school_edges[school_edges["target_offers_jhs"]]
        for band_m in DISTANCE_BANDS_M:
            band_km = band_m // 1000
            row[f"n_jhs_{band_km}km"] = int(
                (jhs_edges_all["road_distance_m"] <= band_m).sum()
            )

        # JHS desert: no JHS within 10 km (and doesn't self-feed)
        row["jhs_desert"] = (not row["self_feeds_jhs"]) and (row["n_jhs_10km"] == 0)
    else:
        row["nearest_jhs_km"] = np.nan  # not applicable
        row["nearest_jhs_id"] = None
        row["n_jhs_5km"] = 0
        row["n_jhs_10km"] = 0
        row["n_jhs_20km"] = 0
        row["jhs_desert"] = False  # not applicable

    # --- JHS → SHS transition ---
    row["self_feeds_shs"] = bool(src_jhs and src_shs)

    if src_jhs:
        if src_shs:
            # Self-feeding
            row["nearest_shs_km"] = 0.0
            row["nearest_shs_id"] = None  # self
        else:
            shs_edges = school_edges[school_edges["target_offers_shs"]]
            if len(shs_edges) > 0:
                nearest = shs_edges.loc[shs_edges["road_distance_m"].idxmin()]
                row["nearest_shs_km"] = nearest["road_distance_m"] / 1000
                row["nearest_shs_id"] = nearest["target_id"]
            else:
                row["nearest_shs_km"] = np.inf
                row["nearest_shs_id"] = None

        # SHS counts at distance bands
        shs_edges_all = school_edges[school_edges["target_offers_shs"]]
        for band_m in DISTANCE_BANDS_M:
            band_km = band_m // 1000
            row[f"n_shs_{band_km}km"] = int(
                (shs_edges_all["road_distance_m"] <= band_m).sum()
            )

        # SHS desert
        row["shs_desert"] = (not row["self_feeds_shs"]) and (row["n_shs_10km"] == 0)
    else:
        row["nearest_shs_km"] = np.nan
        row["nearest_shs_id"] = None
        row["n_shs_5km"] = 0
        row["n_shs_10km"] = 0
        row["n_shs_20km"] = 0
        row["shs_desert"] = False

    # Feeder isolation score
    row["feeder_isolation_score"] = _compute_feeder_isolation(
        row, src_es, src_jhs, src_shs
    )


def _compute_feeder_isolation(row, src_es, src_jhs, src_shs):
    """
    Compute a feeder-aware isolation score.

    Measures how well a school can feed its learners to the next level.

    - For ES schools: based on JHS accessibility
    - For JHS schools: based on SHS accessibility
    - For schools offering both transitions: average of both
    - For SHS-only or no-offering schools: 0 (not applicable)

    Returns a value from 0 (well-connected for feeding) to 1 (no feeder
    destination within any distance band).
    """
    scores = []

    if src_es:
        n5 = row.get("n_jhs_5km", 0)
        n10 = row.get("n_jhs_10km", 0)
        n20 = row.get("n_jhs_20km", 0)
        if row.get("self_feeds_jhs", False):
            # Self-feeding counts as having a JHS at distance 0
            n5 += 1
            n10 += 1
            n20 += 1
        es_score = (
            0.5 * (1 / (1 + n5))
            + 0.3 * (1 / (1 + n10))
            + 0.2 * (1 / (1 + n20))
        )
        scores.append(es_score)

    if src_jhs:
        n5 = row.get("n_shs_5km", 0)
        n10 = row.get("n_shs_10km", 0)
        n20 = row.get("n_shs_20km", 0)
        if row.get("self_feeds_shs", False):
            n5 += 1
            n10 += 1
            n20 += 1
        jhs_score = (
            0.5 * (1 / (1 + n5))
            + 0.3 * (1 / (1 + n10))
            + 0.2 * (1 / (1 + n20))
        )
        scores.append(jhs_score)

    if scores:
        return sum(scores) / len(scores)
    else:
        return 0.0  # SHS-only or no offerings — not applicable
