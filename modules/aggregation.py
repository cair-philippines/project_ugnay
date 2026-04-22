"""
Administrative-level aggregation of school accessibility metrics.

Produces municipal, provincial, and regional summaries for choropleth
visualization and planning dashboards.
"""

import numpy as np
import pandas as pd


def _aggregate_level(df_metrics, group_cols, level_name):
    """
    Aggregate school metrics to an administrative level.

    Parameters
    ----------
    df_metrics : pd.DataFrame
        Per-school metrics from accessibility_metrics.compute_metrics().
    group_cols : list of str
        Columns to group by (e.g., ["region", "province", "municipality"]).
    level_name : str
        Name of this aggregation level (for the output column).

    Returns
    -------
    pd.DataFrame
        One row per administrative unit.
    """
    # Only aggregate public schools for isolation/desert metrics
    # (private school isolation is less meaningful for planning)
    df_pub = df_metrics[df_metrics["sector"] == "public"]
    df_all = df_metrics

    # School counts (all sectors)
    counts = df_all.groupby(group_cols).agg(
        n_schools=("school_id", "count"),
        n_public=("sector", lambda s: (s == "public").sum()),
        n_private=("sector", lambda s: (s == "private").sum()),
    )

    # ESC school count
    if "esc_participating" in df_all.columns:
        esc_counts = df_all[df_all.get("esc_participating", pd.Series(dtype=float)) == 1]
        esc_agg = esc_counts.groupby(group_cols).size().rename("n_esc")
    else:
        esc_agg = pd.Series(0, index=counts.index, name="n_esc")

    # Public school metrics
    if len(df_pub) > 0:
        pub_metrics = df_pub.groupby(group_cols).agg(
            mean_isolation_score=("isolation_score", "mean"),
            max_isolation_score=("isolation_score", "max"),
            median_isolation_score=("isolation_score", "median"),
            pct_private_desert=("private_desert", "mean"),
            pct_esc_desert=("esc_desert", "mean"),
            mean_nearest_private_km=("nearest_private_km", lambda x: x.replace(np.inf, np.nan).mean()),
            median_nearest_private_km=("nearest_private_km", lambda x: x.replace(np.inf, np.nan).median()),
            mean_nearest_esc_km=("nearest_esc_km", lambda x: x.replace(np.inf, np.nan).mean()),
            median_nearest_esc_km=("nearest_esc_km", lambda x: x.replace(np.inf, np.nan).median()),
            mean_neighbors_5km=("n_neighbors_5km", "mean"),
            mean_neighbors_10km=("n_neighbors_10km", "mean"),
            mean_neighbors_20km=("n_neighbors_20km", "mean"),
            mean_road_haversine_ratio=("mean_road_haversine_ratio", lambda x: x.dropna().mean()),
        )
        # Convert pct columns to actual percentages
        pub_metrics["pct_private_desert"] = (pub_metrics["pct_private_desert"] * 100).round(1)
        pub_metrics["pct_esc_desert"] = (pub_metrics["pct_esc_desert"] * 100).round(1)
    else:
        pub_metrics = pd.DataFrame(index=counts.index)

    # Level-aware feeder metrics (ES schools for JHS desert, JHS schools for SHS desert)
    df_es = df_metrics[(df_metrics["sector"] == "public") & (df_metrics["offers_es"] == True)]
    df_jhs = df_metrics[(df_metrics["sector"] == "public") & (df_metrics["offers_jhs"] == True)]

    feeder_parts = []
    if len(df_es) > 0:
        es_agg = df_es.groupby(group_cols).agg(
            n_es_schools=("school_id", "count"),
            pct_jhs_desert=("jhs_desert", "mean"),
            mean_nearest_jhs_km=("nearest_jhs_km", lambda x: x.replace([np.inf, np.nan], np.nan).mean()),
            mean_feeder_isolation=("feeder_isolation_score", "mean"),
        )
        es_agg["pct_jhs_desert"] = (es_agg["pct_jhs_desert"] * 100).round(1)
        feeder_parts.append(es_agg)

    if len(df_jhs) > 0:
        jhs_agg = df_jhs.groupby(group_cols).agg(
            n_jhs_schools=("school_id", "count"),
            pct_shs_desert=("shs_desert", "mean"),
            mean_nearest_shs_km=("nearest_shs_km", lambda x: x.replace([np.inf, np.nan], np.nan).mean()),
        )
        jhs_agg["pct_shs_desert"] = (jhs_agg["pct_shs_desert"] * 100).round(1)
        feeder_parts.append(jhs_agg)

    # Centroids (all schools)
    centroids = df_all.groupby(group_cols).agg(
        centroid_lat=("latitude", "mean"),
        centroid_lon=("longitude", "mean"),
    )

    # Combine
    result = counts.join(esc_agg).join(pub_metrics)
    for part in feeder_parts:
        result = result.join(part)
    result = result.join(centroids)
    result["n_esc"] = result["n_esc"].fillna(0).astype(int)
    result["level"] = level_name
    result = result.reset_index()

    return result


def aggregate_municipal(df_metrics):
    """Aggregate metrics to municipality level, including PSGC code for choropleth matching."""
    # Resolve psgc_municity per municipality (take the most common code)
    df = df_metrics.copy()
    if "psgc_municity" in df.columns:
        # Include psgc_municity in grouping so it carries through
        # First, assign the dominant psgc_municity per municipality
        psgc_lookup = (
            df.dropna(subset=["psgc_municity"])
            .groupby(["region", "province", "municipality"])["psgc_municity"]
            .agg(lambda x: x.mode().iloc[0] if len(x.mode()) > 0 else None)
            .reset_index()
        )
        result = _aggregate_level(
            df,
            group_cols=["region", "province", "municipality"],
            level_name="municipal",
        )
        result = result.merge(psgc_lookup, on=["region", "province", "municipality"], how="left")
        return result

    return _aggregate_level(
        df,
        group_cols=["region", "province", "municipality"],
        level_name="municipal",
    )


def aggregate_provincial(df_metrics):
    """Aggregate metrics to province level."""
    return _aggregate_level(
        df_metrics,
        group_cols=["region", "province"],
        level_name="provincial",
    )


def aggregate_regional(df_metrics):
    """Aggregate metrics to region level."""
    return _aggregate_level(
        df_metrics,
        group_cols=["region"],
        level_name="regional",
    )


def aggregate_all(df_metrics):
    """
    Compute all three aggregation levels.

    Returns
    -------
    dict of str → pd.DataFrame
        Keys: "municipal", "provincial", "regional"
    """
    return {
        "municipal": aggregate_municipal(df_metrics),
        "provincial": aggregate_provincial(df_metrics),
        "regional": aggregate_regional(df_metrics),
    }
