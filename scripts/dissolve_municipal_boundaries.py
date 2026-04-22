#!/usr/bin/env python3
"""
Dissolve barangay-level (adm4) boundaries into municipal-level (adm3)
boundaries for choropleth visualization.

Input: project_coordinates adm4 shapefile
Output: GeoJSON file with one polygon per municipality

Usage:
    python dissolve_municipal_boundaries.py
"""

import sys
import time
from pathlib import Path

import geopandas as gpd

PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_DIR / "output" / "boundaries"

ADM4_PATH = (
    PROJECT_DIR.parent / "project_coordinates" / "data" / "reference"
    / "phl_admbnda_adm4_updated"
)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading adm4 (barangay) boundaries...")
    t0 = time.time()
    gdf = gpd.read_file(str(ADM4_PATH))
    print(f"  Loaded {len(gdf):,} barangays in {time.time() - t0:.1f}s")

    # Dissolve to municipal level (adm3)
    print("Dissolving to municipal level (ADM3_PCODE)...")
    t0 = time.time()

    # Keep administrative name columns
    keep_cols = [
        "ADM3_PCODE", "ADM3_EN",  # municipality
        "ADM2_PCODE", "ADM2_EN",  # province
        "ADM1_PCODE", "ADM1_EN",  # region
        "geometry",
    ]
    gdf_sub = gdf[keep_cols].copy()

    gdf_municipal = gdf_sub.dissolve(
        by=["ADM3_PCODE", "ADM3_EN", "ADM2_PCODE", "ADM2_EN", "ADM1_PCODE", "ADM1_EN"],
        as_index=False,
    )

    # Compute area
    gdf_municipal["area_sqkm"] = gdf_municipal.geometry.to_crs(epsg=32651).area / 1e6

    elapsed = time.time() - t0
    print(f"  Dissolved to {len(gdf_municipal):,} municipalities in {elapsed:.1f}s")

    # Also dissolve to provincial level
    print("Dissolving to provincial level (ADM2_PCODE)...")
    t0 = time.time()

    prov_cols = [
        "ADM2_PCODE", "ADM2_EN",
        "ADM1_PCODE", "ADM1_EN",
        "geometry",
    ]
    gdf_provincial = gdf[prov_cols].copy().dissolve(
        by=["ADM2_PCODE", "ADM2_EN", "ADM1_PCODE", "ADM1_EN"],
        as_index=False,
    )
    gdf_provincial["area_sqkm"] = gdf_provincial.geometry.to_crs(epsg=32651).area / 1e6
    print(f"  Dissolved to {len(gdf_provincial):,} provinces in {time.time() - t0:.1f}s")

    # Simplify geometries for web (tolerance ~110m, keeps shape recognizable)
    print("Simplifying geometries (tolerance=0.001° ≈ 110m)...")
    t0 = time.time()
    gdf_municipal["geometry"] = gdf_municipal.geometry.simplify(
        tolerance=0.001, preserve_topology=True
    )
    gdf_provincial["geometry"] = gdf_provincial.geometry.simplify(
        tolerance=0.001, preserve_topology=True
    )
    print(f"  Done in {time.time() - t0:.1f}s")

    # Save as GeoJSON (for deck.gl GeoJsonLayer)
    muni_path = OUTPUT_DIR / "municipal_boundaries.geojson"
    gdf_municipal.to_file(muni_path, driver="GeoJSON")
    print(f"\nSaved: {muni_path.name} ({muni_path.stat().st_size / 1e6:.1f} MB)")

    prov_path = OUTPUT_DIR / "provincial_boundaries.geojson"
    gdf_provincial.to_file(prov_path, driver="GeoJSON")
    print(f"Saved: {prov_path.name} ({prov_path.stat().st_size / 1e6:.1f} MB)")

    # Summary
    print(f"\nMunicipal boundaries: {len(gdf_municipal):,}")
    print(f"  Regions: {gdf_municipal['ADM1_EN'].nunique()}")
    print(f"  Provinces: {gdf_municipal['ADM2_EN'].nunique()}")
    print(f"  Area range: {gdf_municipal['area_sqkm'].min():.1f} - {gdf_municipal['area_sqkm'].max():.1f} sq km")

    print(f"\nProvincial boundaries: {len(gdf_provincial):,}")


if __name__ == "__main__":
    main()
