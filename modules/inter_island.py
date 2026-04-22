"""
Inter-island handling for Philippine school connectivity.

Provides province-to-island-group mapping and sea-separation tagging
for edges where OSRM returns no road route but schools are within
haversine cutoff.
"""

import numpy as np

# ---------------------------------------------------------------------------
# Province PSGC codes → island group
#
# PSGC region codes (first 2 digits) mapped to island groups.
# Multi-island regions are handled at the province level.
# ---------------------------------------------------------------------------

# Regions that are entirely on one island group
_REGION_ISLAND = {
    "NCR": "Luzon",
    "CAR": "Luzon",
    "Region I": "Luzon",
    "Region II": "Luzon",
    "Region III": "Luzon",
    "Region IV-A": "Luzon",
    "Region V": "Luzon",
    "Region VI": "Visayas",
    "NIR": "Visayas",
    "Region VII": "Visayas",
    "Region VIII": "Visayas",
    "Region IX": "Mindanao",
    "Region X": "Mindanao",
    "Region XI": "Mindanao",
    "Region XII": "Mindanao",
    "Region XIII": "Mindanao",
    "CARAGA": "Mindanao",
    "BARMM": "Mindanao",
    "MIMAROPA": "Luzon",
}

# Regions that span multiple islands
# Region IV-B (MIMAROPA): Mindoro + Marinduque + Romblon + Palawan
# For simplicity, all are tagged as "Luzon" (they're administratively Luzon)
# but are functionally island-separated from mainland Luzon
_MULTI_ISLAND_REGIONS = {
    "Region IV-B": "Luzon",  # MIMAROPA — islands off Luzon
}

# PSGC region prefix → island group (for PSGC-based lookup)
_PSGC_REGION_ISLAND = {
    "13": "Luzon",   # NCR
    "14": "Luzon",   # CAR
    "01": "Luzon",   # Region I
    "02": "Luzon",   # Region II
    "03": "Luzon",   # Region III
    "04": "Luzon",   # Region IV-A (CALABARZON)
    "17": "Luzon",   # Region IV-B (MIMAROPA)
    "05": "Luzon",   # Region V (Bicol)
    "06": "Visayas", # Region VI
    "07": "Visayas", # Region VII
    "08": "Visayas", # Region VIII
    "09": "Mindanao",  # Region IX
    "10": "Mindanao",  # Region X
    "11": "Mindanao",  # Region XI
    "12": "Mindanao",  # Region XII
    "16": "Mindanao",  # Region XIII (CARAGA)
    "15": "Mindanao",  # BARMM
    "19": "Mindanao",  # BARMM (alternate code)
}


def get_island_group(region=None, psgc_region=None):
    """
    Determine island group from region name or PSGC region code.

    Parameters
    ----------
    region : str, optional
        DepEd region name (e.g., "Region III", "NCR").
    psgc_region : str, optional
        PSGC region code (first 2 digits, e.g., "03").

    Returns
    -------
    str or None
        "Luzon", "Visayas", or "Mindanao". None if unknown.
    """
    if region is not None:
        result = _REGION_ISLAND.get(region)
        if result is None:
            result = _MULTI_ISLAND_REGIONS.get(region)
        return result

    if psgc_region is not None:
        code = str(psgc_region).zfill(2)[:2]
        return _PSGC_REGION_ISLAND.get(code)

    return None


def tag_island_group(df):
    """
    Add `island_group` column to a school DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        Must have `region` column.

    Returns
    -------
    pd.DataFrame
        Input DataFrame with `island_group` column added.
    """
    df["island_group"] = df["region"].map(
        lambda r: get_island_group(region=r)
    )
    return df


def tag_sea_separated(df_edges):
    """
    Tag edges as sea-separated where road_distance_m is inf/NaN but
    haversine_distance_m is finite.

    These represent school pairs that are geographically close but have
    no road connection (likely separated by water).

    Parameters
    ----------
    df_edges : pd.DataFrame
        Edge table with road_distance_m and haversine_distance_m columns.

    Returns
    -------
    pd.DataFrame
        Input with `is_sea_separated` column added.
    """
    if "haversine_distance_m" not in df_edges.columns:
        df_edges["is_sea_separated"] = False
        return df_edges

    road_missing = ~np.isfinite(df_edges["road_distance_m"])
    hav_present = np.isfinite(df_edges["haversine_distance_m"])
    df_edges["is_sea_separated"] = road_missing & hav_present

    return df_edges
