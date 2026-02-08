from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

try:
    from .data import (
        get_borough_team_view,
        get_defect_changes_summary,
        get_defects,
        get_defects_csv,
        get_defects_geojson,
        get_drones,
        get_heatmap_points,
        get_neighborhoods,
        get_potholes,
        get_wms_like_features,
    )
except ImportError:
    from data import (
        get_borough_team_view,
        get_defect_changes_summary,
        get_defects,
        get_defects_csv,
        get_defects_geojson,
        get_drones,
        get_heatmap_points,
        get_neighborhoods,
        get_potholes,
        get_wms_like_features,
    )

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")


def _parse_bbox(bbox_raw: str | None) -> tuple | None:
    if not bbox_raw:
        return None
    try:
        values = [float(value.strip()) for value in bbox_raw.split(",")]
    except ValueError:
        return None
    if len(values) != 4:
        return None
    min_lng, min_lat, max_lng, max_lat = values
    if min_lng > max_lng or min_lat > max_lat:
        return None
    return min_lng, min_lat, max_lng, max_lat


def _query_filters() -> tuple[str | None, str | None, str]:
    borough = request.args.get("borough")
    team = request.args.get("team")
    change_scope = request.args.get("change_scope", "all")
    return borough, team, change_scope


@app.get("/api/health")
def health() -> tuple:
    return jsonify({"status": "ok"}), 200


@app.get("/api/drones")
def drones() -> tuple:
    return jsonify(get_drones()), 200


@app.get("/api/potholes")
def potholes() -> tuple:
    return jsonify(get_potholes()), 200


@app.get("/api/heatmap")
def heatmap() -> tuple:
    borough, team, change_scope = _query_filters()
    mode = request.args.get("mode", "density")
    return jsonify(get_heatmap_points(mode=mode, borough=borough, team=team, change_scope=change_scope)), 200


@app.get("/api/defects")
def defects() -> tuple:
    borough, team, change_scope = _query_filters()
    return jsonify(get_defects(borough=borough, team=team, change_scope=change_scope)), 200


@app.get("/api/defects/geojson")
def defects_geojson() -> tuple:
    borough, team, change_scope = _query_filters()
    return jsonify(get_defects_geojson(borough=borough, team=team, change_scope=change_scope)), 200


@app.get("/api/defects/changes")
def defects_changes() -> tuple:
    borough, team, change_scope = _query_filters()
    return jsonify(
        get_defect_changes_summary(
            borough=borough,
            team=team,
            change_scope=change_scope,
        )
    ), 200


@app.get("/api/views/boroughs-teams")
def borough_team_view() -> tuple:
    borough, team, change_scope = _query_filters()
    return jsonify(
        get_borough_team_view(
            borough=borough,
            team=team,
            change_scope=change_scope,
        )
    ), 200


@app.get("/api/exports/defects.csv")
def defects_csv() -> Response:
    borough, team, change_scope = _query_filters()
    payload = get_defects_csv(borough=borough, team=team, change_scope=change_scope)
    return Response(
        payload,
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=defects_export.csv",
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/exports/defects.geojson")
def defects_export_geojson() -> tuple:
    borough, team, change_scope = _query_filters()
    return jsonify(get_defects_geojson(borough=borough, team=team, change_scope=change_scope)), 200


@app.get("/api/integrations/wms-like")
def wms_like() -> tuple:
    borough, team, change_scope = _query_filters()
    bbox = _parse_bbox(request.args.get("bbox"))
    payload = get_wms_like_features(
        bbox=bbox,
        borough=borough,
        team=team,
        change_scope=change_scope,
    )
    return jsonify(payload), 200


@app.get("/api/neighborhoods")
def neighborhoods() -> tuple:
    return jsonify(get_neighborhoods()), 200


@app.get("/")
def index() -> object:
    return send_from_directory(FRONTEND_DIR, "index.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
