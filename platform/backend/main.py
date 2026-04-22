"""
FastAPI backend for project_ugnay platform.

Serves precomputed school accessibility metrics, edges, aggregations,
and boundary GeoJSON for the deck.gl frontend.
"""

import os
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, ORJSONResponse

from data_loader import DataStore

app = FastAPI(
    title="Ugnay – School Connectivity Platform",
    default_response_class=ORJSONResponse,
)

store = DataStore()


@app.on_event("startup")
def startup():
    store.load()


# --- API Endpoints ---


@app.get("/api/stats")
def get_stats():
    """System-wide summary statistics."""
    return store.stats


@app.get("/api/filters")
def get_filters(
    region: Optional[str] = None,
    province: Optional[str] = None,
):
    """Cascading filter options."""
    return store.get_filter_options(region=region, province=province)


@app.get("/api/schools")
def get_schools(
    region: Optional[str] = None,
    province: Optional[str] = None,
    municipality: Optional[str] = None,
    sector: Optional[str] = None,
    limit: int = Query(default=1000, le=60000),
    offset: int = Query(default=0, ge=0),
):
    """
    List schools with metrics, optionally filtered by location.
    Returns a paginated list of school records.
    """
    results = store.schools_records

    if region:
        results = [s for s in results if s.get("region") == region]
    if province:
        results = [s for s in results if s.get("province") == province]
    if municipality:
        results = [s for s in results if s.get("municipality") == municipality]
    if sector:
        results = [s for s in results if s.get("sector") == sector]

    total = len(results)
    results = results[offset : offset + limit]

    return {"total": total, "schools": results}


@app.get("/api/schools/{school_id}")
def get_school(school_id: str):
    """Single school detail with all metrics."""
    school = store.get_school(school_id)
    if school is None:
        return {"error": "School not found"}
    return school


@app.get("/api/schools/{school_id}/neighbors")
def get_neighbors(
    school_id: str,
    max_km: float = Query(default=20, le=20),
):
    """Edges from a school to its neighbors within max_km."""
    school = store.get_school(school_id)
    if school is None:
        return {"error": "School not found"}

    neighbors = store.get_neighbors(school_id, max_km=max_km)
    return {
        "school_id": school_id,
        "school_name": school.get("school_name"),
        "n_neighbors": len(neighbors),
        "max_km": max_km,
        "neighbors": neighbors,
    }


@app.get("/api/aggregations")
def get_aggregations(
    level: str = Query(default="municipal", regex="^(municipal|provincial|regional)$"),
    region: Optional[str] = None,
    province: Optional[str] = None,
):
    """Administrative-level aggregated metrics."""
    if level == "municipal":
        data = store.municipal
    elif level == "provincial":
        data = store.provincial
    else:
        data = store.regional

    if region:
        data = [d for d in data if d.get("region") == region]
    if province and level == "municipal":
        data = [d for d in data if d.get("province") == province]

    return {"level": level, "total": len(data), "data": data}


@app.get("/api/boundaries/{level}")
def get_boundaries(
    level: str,
):
    """GeoJSON boundaries for choropleth layers."""
    if level == "municipal" and store.boundaries_municipal:
        return store.boundaries_municipal
    elif level == "provincial" and store.boundaries_provincial:
        return store.boundaries_provincial
    return {"error": f"Boundaries not found for level: {level}"}


# --- Static file serving (React SPA) ---

# Serve frontend static files
frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.exists(frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")

    @app.get("/{path:path}")
    def serve_spa(path: str):
        """Serve React SPA for all non-API routes."""
        file_path = os.path.join(frontend_dist, path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(frontend_dist, "index.html"))
