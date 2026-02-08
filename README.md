# SkySpot

SkySpot is a Flask + Leaflet operations map for road-defect monitoring.

## Implemented capabilities

- Live defect map with `Point`, `LineString`, and `Polygon` geometries.
- Photo evidence per defect in map popups.
- Severity categories and confidence scores.
- Heatmaps:
  - Density mode
  - Risk-weighted mode
- True street-centerline drone routing:
  - Uses `data/roads.geojson` if present (preferred local source)
  - Otherwise uses cached Overpass roads at `data/montreal_roads_overpass.json`
  - Falls back to synthetic patrol loops when no road source is available
- Change-over-time view:
  - New / worsened / stable / improved counts
  - Current scan vs previous scan timestamps
  - Rolling scan window (60s) for live deltas
  - "New and worsened since last scan" severity breakdown
- Borough/team ownership and assignment views:
  - Borough ownership table (total, new+worsened, unassigned, avg risk)
  - Team assignment table (in-progress, assigned, unassigned)
- Export and integrations:
  - GeoJSON export endpoint
  - CSV export endpoint
  - WMS-like GeoJSON endpoint with bbox/filter support

## Project structure

```text
backend/
  app.py
  data.py
frontend/
  index.html
  main.js
  style.css
data/
  neighborhoods.json
  montreal_boundary.geojson
  roads.geojson (optional)
  montreal_roads_overpass.json (auto-created cache, optional)
```

## Run locally

1. Install dependencies:
```bash
pip install flask
```

2. Start the server from repository root:
```bash
python backend/app.py
```

3. Open:
`http://127.0.0.1:5000`

Notes:
- For true OSM centerline routing without internet, place a local road centerline file at `data/roads.geojson` (`LineString`/`MultiLineString` GeoJSON).
- To allow live Overpass download/caching, set `SKYSPOT_ENABLE_OVERPASS=1` before starting the backend.
- If Overpass download is disabled, the backend uses `data/roads.geojson` or `data/montreal_roads_overpass.json` when available, then falls back to synthetic routes.

## Key API endpoints

- `GET /api/defects`
- `GET /api/heatmap?mode=density|risk`
- `GET /api/defects/changes`
- `GET /api/views/boroughs-teams`
- `GET /api/exports/defects.geojson`
- `GET /api/exports/defects.csv`
- `GET /api/integrations/wms-like?bbox=minLng,minLat,maxLng,maxLat`
