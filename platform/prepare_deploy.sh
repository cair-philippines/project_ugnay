#!/bin/bash
# Prepare data directory for Docker build.
# Copies precomputed outputs into the platform's data/ staging area.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"
OUTPUT_DIR="$SCRIPT_DIR/../output"

echo "Preparing deployment data..."

# Create staging directories
mkdir -p "$DATA_DIR/edges"
mkdir -p "$DATA_DIR/metrics"
mkdir -p "$DATA_DIR/aggregations"
mkdir -p "$DATA_DIR/boundaries"

# Copy Phase 1 outputs
cp "$OUTPUT_DIR/edges/all_edges.parquet" "$DATA_DIR/edges/"
cp "$OUTPUT_DIR/edges/schools_unified_snapshot.parquet" "$DATA_DIR/edges/"
echo "  Copied edge data"

# Copy Phase 2 outputs
cp "$OUTPUT_DIR/metrics/school_accessibility.parquet" "$DATA_DIR/metrics/"
cp "$OUTPUT_DIR/aggregations/municipal_summary.parquet" "$DATA_DIR/aggregations/"
cp "$OUTPUT_DIR/aggregations/provincial_summary.parquet" "$DATA_DIR/aggregations/"
cp "$OUTPUT_DIR/aggregations/regional_summary.parquet" "$DATA_DIR/aggregations/"
echo "  Copied metrics and aggregations"

# Copy boundaries
cp "$OUTPUT_DIR/boundaries/municipal_boundaries.geojson" "$DATA_DIR/boundaries/"
cp "$OUTPUT_DIR/boundaries/provincial_boundaries.geojson" "$DATA_DIR/boundaries/"
echo "  Copied boundary GeoJSON"

echo "Done. Data staged at: $DATA_DIR"
echo "Total size: $(du -sh "$DATA_DIR" | cut -f1)"
