import json
import math
import csv
import hashlib
import random
from io import StringIO
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
NEIGHBORHOODS_PATH = DATA_DIR / "neighborhoods.json"
MONTREAL_BOUNDARY_PATH = DATA_DIR / "montreal_boundary.geojson"
EARTH_RADIUS_M = 6_371_000.0
DRONE_COVERAGE_KM2 = 10.0
DRONE_COVERAGE_M2 = DRONE_COVERAGE_KM2 * 1_000_000.0
DRONE_COVERAGE_RADIUS_M = math.sqrt(DRONE_COVERAGE_M2 / math.pi)
HEX_SIDE_M = math.sqrt((2.0 * DRONE_COVERAGE_M2) / (3.0 * math.sqrt(3.0)))
MONTREAL_REF_LAT = 45.5017
MONTREAL_REF_LNG = -73.5673
EPSILON = 1e-9
SCAN_WINDOW_SECONDS = 60

LatLng = Tuple[float, float]
XY = Tuple[float, float]
BBox = Tuple[float, float, float, float]

_DRONE_CACHE: Dict[str, object] = {"mtime_ns": None, "drones": []}
_DEFECT_CACHE: Dict[str, object] = {
    "signature": None,
    "scan_key": None,
    "defects": [],
    "scan_time": None,
    "previous_scan_time": None,
}
_REGION_SAMPLE_CACHE: Dict[str, object] = {"signature": None, "shapes": []}

SEVERITY_LEVELS = ("low", "medium", "high", "critical")
SEVERITY_WEIGHTS = {"low": 1.0, "medium": 2.0, "high": 3.0, "critical": 4.0}
TEAM_CATALOG = (
    {"id": "TEAM-WEST", "name": "West Island Ops"},
    {"id": "TEAM-CENTRAL", "name": "Central Core Ops"},
    {"id": "TEAM-EAST", "name": "East Corridor Ops"},
    {"id": "TEAM-NORTH", "name": "North Sector Ops"},
)
ASSIGNEES = (
    "A. Nguyen",
    "M. Chen",
    "R. Patel",
    "K. Moreau",
    "S. Tremblay",
    "L. Roy",
    "J. Diallo",
)
POINT_DEFECT_TYPES = ("pothole",)
LINE_DEFECT_TYPES = ("longitudinal-crack", "alligator-crack", "rutting")
POLYGON_DEFECT_TYPES = ("surface-break", "patch-failure", "deformation-zone")
DEFECTS_PER_DRONE = 2
POTHOLE_SAMPLE_LIMIT = 40


def _normalize_ring(ring: Sequence[Sequence[float]]) -> List[LatLng]:
    polygon: List[LatLng] = [(float(lat), float(lng)) for lng, lat in ring]
    if len(polygon) > 1 and polygon[0] == polygon[-1]:
        polygon = polygon[:-1]
    return polygon


def _polygons_from_geometry(geometry: Dict[str, object]) -> List[List[LatLng]]:
    coordinates = geometry.get("coordinates", [])
    geometry_type = geometry.get("type")
    if not coordinates:
        return []

    if geometry_type == "Polygon":
        polygon = _normalize_ring(coordinates[0])
        return [polygon] if len(polygon) >= 3 else []

    if geometry_type == "MultiPolygon":
        polygons: List[List[LatLng]] = []
        for polygon_coordinates in coordinates:
            if not polygon_coordinates:
                continue
            polygon = _normalize_ring(polygon_coordinates[0])
            if len(polygon) >= 3:
                polygons.append(polygon)
        return polygons

    return []


def _polygons_from_feature(feature: Dict[str, object]) -> List[List[LatLng]]:
    geometry = feature.get("geometry", {})
    if not isinstance(geometry, dict):
        return []
    return _polygons_from_geometry(geometry)


def _polygon_centroid(polygon: Sequence[LatLng]) -> LatLng:
    lat = sum(point[0] for point in polygon) / len(polygon)
    lng = sum(point[1] for point in polygon) / len(polygon)
    return lat, lng


def _latlng_to_xy(lat: float, lng: float, origin_lat: float, origin_lng: float) -> XY:
    x = math.radians(lng - origin_lng) * EARTH_RADIUS_M * math.cos(math.radians(origin_lat))
    y = math.radians(lat - origin_lat) * EARTH_RADIUS_M
    return x, y


def _xy_to_latlng(x: float, y: float, origin_lat: float, origin_lng: float) -> LatLng:
    lat = origin_lat + math.degrees(y / EARTH_RADIUS_M)
    lng = origin_lng + math.degrees(x / (EARTH_RADIUS_M * math.cos(math.radians(origin_lat))))
    return lat, lng


def _point_in_polygon(point: XY, polygon: Sequence[XY]) -> bool:
    x, y = point
    inside = False
    prev_x, prev_y = polygon[-1]
    for curr_x, curr_y in polygon:
        crosses = (curr_y > y) != (prev_y > y)
        if crosses:
            slope_x = (prev_x - curr_x) * (y - curr_y) / (prev_y - curr_y + EPSILON) + curr_x
            if x < slope_x:
                inside = not inside
        prev_x, prev_y = curr_x, curr_y
    return inside


def _point_in_convex_polygon(point: XY, polygon: Sequence[XY]) -> bool:
    sign = 0
    for index, current in enumerate(polygon):
        next_point = polygon[(index + 1) % len(polygon)]
        cross = (
            (next_point[0] - current[0]) * (point[1] - current[1])
            - (next_point[1] - current[1]) * (point[0] - current[0])
        )
        if abs(cross) <= EPSILON:
            continue
        orientation = 1 if cross > 0 else -1
        if sign == 0:
            sign = orientation
        elif orientation != sign:
            return False
    return True


def _distance_xy(a: XY, b: XY) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _bbox_for_points(points: Sequence[XY]) -> BBox:
    x_values = [point[0] for point in points]
    y_values = [point[1] for point in points]
    return (min(x_values), min(y_values), max(x_values), max(y_values))


def _bboxes_overlap(a: BBox, b: BBox) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _orientation(a: XY, b: XY, c: XY) -> int:
    cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if abs(cross) <= EPSILON:
        return 0
    return 1 if cross > 0 else -1


def _on_segment(a: XY, b: XY, p: XY) -> bool:
    return (
        min(a[0], b[0]) - EPSILON <= p[0] <= max(a[0], b[0]) + EPSILON
        and min(a[1], b[1]) - EPSILON <= p[1] <= max(a[1], b[1]) + EPSILON
    )


def _segments_intersect(a: XY, b: XY, c: XY, d: XY) -> bool:
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)

    if o1 != o2 and o3 != o4:
        return True

    if o1 == 0 and _on_segment(a, b, c):
        return True
    if o2 == 0 and _on_segment(a, b, d):
        return True
    if o3 == 0 and _on_segment(c, d, a):
        return True
    if o4 == 0 and _on_segment(c, d, b):
        return True
    return False


def _polygon_intersects_hex(polygon_xy: Sequence[XY], hex_xy: Sequence[XY]) -> bool:
    if any(_point_in_polygon(point, polygon_xy) for point in hex_xy):
        return True
    if any(_point_in_convex_polygon(point, hex_xy) for point in polygon_xy):
        return True

    polygon_edges = [
        (polygon_xy[index], polygon_xy[(index + 1) % len(polygon_xy)])
        for index in range(len(polygon_xy))
    ]
    hex_edges = [
        (hex_xy[index], hex_xy[(index + 1) % len(hex_xy)])
        for index in range(len(hex_xy))
    ]
    for edge_a in polygon_edges:
        for edge_b in hex_edges:
            if _segments_intersect(edge_a[0], edge_a[1], edge_b[0], edge_b[1]):
                return True
    return False


def _hex_vertices(center_xy: XY, side_m: float) -> List[XY]:
    cx, cy = center_xy
    vertices: List[XY] = []
    for index in range(6):
        angle = math.radians((60.0 * index) - 30.0)
        vertices.append((cx + side_m * math.cos(angle), cy + side_m * math.sin(angle)))
    return vertices


def _offset_latlng(lat: float, lng: float, north_m: float, east_m: float) -> LatLng:
    lat_offset = math.degrees(north_m / EARTH_RADIUS_M)
    lng_offset = math.degrees(east_m / (EARTH_RADIUS_M * math.cos(math.radians(lat))))
    return lat + lat_offset, lng + lng_offset


def _build_neighborhood_shapes() -> List[Dict[str, object]]:
    geojson = get_neighborhoods()
    features = geojson.get("features", [])
    shapes: List[Dict[str, object]] = []
    for feature in features:
        name = feature.get("properties", {}).get("name", "Neighborhood")
        for polygon_latlng in _polygons_from_feature(feature):
            polygon_xy = [
                _latlng_to_xy(lat, lng, MONTREAL_REF_LAT, MONTREAL_REF_LNG)
                for lat, lng in polygon_latlng
            ]
            centroid_lat, centroid_lng = _polygon_centroid(polygon_latlng)
            centroid_xy = _latlng_to_xy(
                centroid_lat, centroid_lng, MONTREAL_REF_LAT, MONTREAL_REF_LNG
            )
            shapes.append(
                {
                    "name": name,
                    "polygon_xy": polygon_xy,
                    "bbox": _bbox_for_points(polygon_xy),
                    "centroid_xy": centroid_xy,
                }
            )
    return shapes


def _build_coverage_envelope_shapes() -> List[Dict[str, object]]:
    if not MONTREAL_BOUNDARY_PATH.exists():
        return []

    with MONTREAL_BOUNDARY_PATH.open("r", encoding="utf-8-sig") as file:
        geojson = json.load(file)

    shapes: List[Dict[str, object]] = []
    for feature in geojson.get("features", []):
        for polygon_latlng in _polygons_from_feature(feature):
            polygon_xy = [
                _latlng_to_xy(lat, lng, MONTREAL_REF_LAT, MONTREAL_REF_LNG)
                for lat, lng in polygon_latlng
            ]
            shapes.append(
                {
                    "polygon_xy": polygon_xy,
                    "bbox": _bbox_for_points(polygon_xy),
                }
            )
    return shapes


def _generate_hex_cells() -> List[Dict[str, object]]:
    shapes = _build_neighborhood_shapes()
    if not shapes:
        return []

    envelope_shapes = _build_coverage_envelope_shapes()
    if not envelope_shapes:
        envelope_shapes = [{"polygon_xy": shape["polygon_xy"], "bbox": shape["bbox"]} for shape in shapes]

    x_min = min(shape["bbox"][0] for shape in envelope_shapes)
    y_min = min(shape["bbox"][1] for shape in envelope_shapes)
    x_max = max(shape["bbox"][2] for shape in envelope_shapes)
    y_max = max(shape["bbox"][3] for shape in envelope_shapes)

    col_step = math.sqrt(3.0) * HEX_SIDE_M
    row_step = 1.5 * HEX_SIDE_M
    margin = col_step * 2.0
    x_start = math.floor((x_min - margin) / col_step) * col_step
    y_start = math.floor((y_min - margin) / row_step) * row_step

    cells: List[Dict[str, object]] = []
    row_index = 0
    y = y_start
    while y <= y_max + margin:
        x_offset = col_step / 2.0 if row_index % 2 else 0.0
        x = x_start + x_offset
        while x <= x_max + margin:
            center_xy = (x, y)
            hex_xy = _hex_vertices(center_xy, HEX_SIDE_M)
            hex_bbox = _bbox_for_points(hex_xy)

            inside_envelope = False
            for envelope in envelope_shapes:
                envelope_bbox = envelope["bbox"]
                if not _bboxes_overlap(hex_bbox, envelope_bbox):
                    continue
                if _polygon_intersects_hex(envelope["polygon_xy"], hex_xy):
                    inside_envelope = True
                    break

            if not inside_envelope:
                x += col_step
                continue

            intersecting_shapes: List[Dict[str, object]] = []
            center_owner = None

            for shape in shapes:
                shape_bbox = shape["bbox"]
                if not _bboxes_overlap(hex_bbox, shape_bbox):
                    continue

                polygon_xy = shape["polygon_xy"]
                if _polygon_intersects_hex(polygon_xy, hex_xy):
                    intersecting_shapes.append(shape)
                    if center_owner is None and _point_in_polygon(center_xy, polygon_xy):
                        center_owner = shape

            assigned_shape = center_owner
            if assigned_shape is None:
                assignment_pool = intersecting_shapes if intersecting_shapes else shapes
                assigned_shape = min(
                    assignment_pool,
                    key=lambda shape: _distance_xy(center_xy, shape["centroid_xy"]),
                )

            center_latlng = _xy_to_latlng(
                center_xy[0], center_xy[1], MONTREAL_REF_LAT, MONTREAL_REF_LNG
            )
            hex_latlng = [
                _xy_to_latlng(vertex[0], vertex[1], MONTREAL_REF_LAT, MONTREAL_REF_LNG)
                for vertex in hex_xy
            ]
            cells.append(
                {
                    "neighborhood": assigned_shape["name"],
                    "center_latlng": center_latlng,
                    "coverage_polygon": hex_latlng,
                }
            )

            x += col_step
        y += row_step
        row_index += 1

    cells.sort(key=lambda cell: (cell["center_latlng"][0], cell["center_latlng"][1]))
    return cells


def _mtime_ns(path: Path) -> int:
    if not path.exists():
        return -1
    return path.stat().st_mtime_ns


def _coverage_signature() -> Tuple[int, int]:
    return (_mtime_ns(NEIGHBORHOODS_PATH), _mtime_ns(MONTREAL_BOUNDARY_PATH))


def _scan_key(now: Optional[datetime] = None) -> int:
    moment = now or datetime.now(timezone.utc)
    return int(moment.timestamp() // SCAN_WINDOW_SECONDS)


def _scan_timestamp(scan_key: int) -> str:
    scan_time = datetime.fromtimestamp(scan_key * SCAN_WINDOW_SECONDS, tz=timezone.utc)
    return scan_time.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_defect_active(index: int, scan_key: int) -> bool:
    if index <= 5:
        return True
    return ((index + (scan_key % 7)) % 9) != 0


def _centroid_xy(points: Sequence[XY]) -> XY:
    if not points:
        return (0.0, 0.0)
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _polygon_area_xy(points: Sequence[XY]) -> float:
    if len(points) < 3:
        return 0.0
    area = 0.0
    for index, current in enumerate(points):
        nxt = points[(index + 1) % len(points)]
        area += (current[0] * nxt[1]) - (nxt[0] * current[1])
    return abs(area) * 0.5


def _seeded_rng(*parts: object) -> random.Random:
    seed_text = "|".join(str(part) for part in parts)
    digest = hashlib.sha1(seed_text.encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def _sample_point_in_polygon_xy(
    polygon_xy: Sequence[XY],
    bbox: BBox,
    rng: random.Random,
    max_attempts: int = 180,
) -> XY:
    for _ in range(max_attempts):
        x = rng.uniform(bbox[0], bbox[2])
        y = rng.uniform(bbox[1], bbox[3])
        point = (x, y)
        if _point_in_polygon(point, polygon_xy):
            return point
    return _centroid_xy(polygon_xy)


def _region_sampling_shapes() -> List[Dict[str, object]]:
    signature = _coverage_signature()
    if _REGION_SAMPLE_CACHE["signature"] == signature:
        return _REGION_SAMPLE_CACHE["shapes"]  # type: ignore[return-value]

    raw_shapes = _build_coverage_envelope_shapes()
    if not raw_shapes:
        raw_shapes = [
            {"polygon_xy": shape["polygon_xy"], "bbox": shape["bbox"]}
            for shape in _build_neighborhood_shapes()
        ]

    shapes: List[Dict[str, object]] = []
    for shape in raw_shapes:
        polygon_xy = shape["polygon_xy"]
        area = _polygon_area_xy(polygon_xy)
        if area <= EPSILON:
            continue
        shapes.append(
            {
                "polygon_xy": polygon_xy,
                "bbox": shape["bbox"],
                "area": area,
            }
        )

    _REGION_SAMPLE_CACHE["signature"] = signature
    _REGION_SAMPLE_CACHE["shapes"] = shapes
    return shapes


def _random_city_point_latlng(scan_key: int, index: int) -> LatLng:
    shapes = _region_sampling_shapes()
    if not shapes:
        return MONTREAL_REF_LAT, MONTREAL_REF_LNG

    rng = _seeded_rng("city-point", scan_key, index)
    total_area = sum(float(shape["area"]) for shape in shapes)
    remaining = rng.uniform(0.0, total_area)
    chosen = shapes[-1]
    for shape in shapes:
        remaining -= float(shape["area"])
        if remaining <= 0:
            chosen = shape
            break

    point_xy = _sample_point_in_polygon_xy(chosen["polygon_xy"], chosen["bbox"], rng)
    return _xy_to_latlng(point_xy[0], point_xy[1], MONTREAL_REF_LAT, MONTREAL_REF_LNG)


def _build_patrol_route_xy(coverage_polygon_xy: Sequence[XY], drone_id: str) -> List[XY]:
    if len(coverage_polygon_xy) < 3:
        return list(coverage_polygon_xy)

    midpoints: List[XY] = []
    for index, current in enumerate(coverage_polygon_xy):
        nxt = coverage_polygon_xy[(index + 1) % len(coverage_polygon_xy)]
        midpoints.append(((current[0] + nxt[0]) / 2.0, (current[1] + nxt[1]) / 2.0))

    center = _centroid_xy(coverage_polygon_xy)
    rng = _seeded_rng("road-route", drone_id)
    center_jitter = (
        center[0] + rng.uniform(-180.0, 180.0),
        center[1] + rng.uniform(-180.0, 180.0),
    )
    hub = center_jitter if _point_in_polygon(center_jitter, coverage_polygon_xy) else center

    if len(midpoints) >= 6:
        start_index = rng.randrange(len(midpoints))
        first = midpoints[start_index]
        second = midpoints[(start_index + 2) % len(midpoints)]
        third = midpoints[(start_index + 4) % len(midpoints)]
        return [first, hub, second, hub, third, hub, first]

    return [midpoints[0], hub, midpoints[len(midpoints) // 2], hub, midpoints[0]]


def _polyline_segment_lengths(points_xy: Sequence[XY]) -> List[float]:
    if len(points_xy) < 2:
        return []
    return [
        _distance_xy(points_xy[index], points_xy[index + 1])
        for index in range(len(points_xy) - 1)
    ]


def _point_along_polyline(points_xy: Sequence[XY], segment_lengths: Sequence[float], distance_m: float) -> XY:
    if not points_xy:
        return (0.0, 0.0)
    if len(points_xy) == 1:
        return points_xy[0]

    total_length = sum(segment_lengths)
    if total_length <= EPSILON:
        return points_xy[0]

    remaining = distance_m % total_length
    for index, segment_length in enumerate(segment_lengths):
        if segment_length <= EPSILON:
            continue
        if remaining <= segment_length or index == len(segment_lengths) - 1:
            start = points_xy[index]
            end = points_xy[index + 1]
            ratio = remaining / segment_length
            return (
                start[0] + ((end[0] - start[0]) * ratio),
                start[1] + ((end[1] - start[1]) * ratio),
            )
        remaining -= segment_length
    return points_xy[-1]


def _latlng_list_from_xy(points_xy: Sequence[XY]) -> List[List[float]]:
    points: List[List[float]] = []
    for point_xy in points_xy:
        lat, lng = _xy_to_latlng(point_xy[0], point_xy[1], MONTREAL_REF_LAT, MONTREAL_REF_LNG)
        points.append([round(lat, 6), round(lng, 6)])
    return points


def _drone_public_view(drone_state: Dict[str, object]) -> Dict[str, object]:
    return {
        "id": drone_state["id"],
        "lat": drone_state["lat"],
        "lng": drone_state["lng"],
        "status": drone_state["status"],
        "speed_mps": drone_state["speed_mps"],
        "neighborhood": drone_state["neighborhood"],
        "coverage_radius_m": drone_state["coverage_radius_m"],
        "coverage_polygon": drone_state["coverage_polygon"],
        "road_route": drone_state["road_route"],
    }


def _initialize_drone_simulation(signature: Tuple[int, int]) -> None:
    cells = _generate_hex_cells()
    now_s = datetime.now(timezone.utc).timestamp()

    sim_drones: List[Dict[str, object]] = []
    for index, cell in enumerate(cells, start=1):
        drone_id = f"DRN-{index:03d}"
        coverage_polygon_latlng = cell["coverage_polygon"]
        coverage_polygon_xy = [
            _latlng_to_xy(lat, lng, MONTREAL_REF_LAT, MONTREAL_REF_LNG)
            for lat, lng in coverage_polygon_latlng
        ]
        route_xy = _build_patrol_route_xy(coverage_polygon_xy, drone_id)
        segment_lengths = _polyline_segment_lengths(route_xy)
        route_length_m = sum(segment_lengths)

        speed_mps = 0.0 if (index % 3) == 0 else round(7.5 + (index * 1.1), 1)
        status = "active" if speed_mps > 0 else "idle"

        spawn_rng = _seeded_rng("spawn", drone_id)
        distance_m = spawn_rng.uniform(0.0, route_length_m) if route_length_m > EPSILON else 0.0
        spawn_xy = _point_along_polyline(route_xy, segment_lengths, distance_m)
        spawn_lat, spawn_lng = _xy_to_latlng(
            spawn_xy[0], spawn_xy[1], MONTREAL_REF_LAT, MONTREAL_REF_LNG
        )

        sim_drones.append(
            {
                "id": drone_id,
                "lat": round(spawn_lat, 6),
                "lng": round(spawn_lng, 6),
                "status": status,
                "speed_mps": speed_mps,
                "neighborhood": cell["neighborhood"],
                "coverage_radius_m": round(DRONE_COVERAGE_RADIUS_M, 1),
                "coverage_polygon": [
                    [round(lat, 6), round(lng, 6)] for lat, lng in coverage_polygon_latlng
                ],
                "road_route": _latlng_list_from_xy(route_xy),
                "route_xy": route_xy,
                "segment_lengths": segment_lengths,
                "route_length_m": route_length_m,
                "distance_m": distance_m,
            }
        )

    _DRONE_CACHE["mtime_ns"] = signature
    _DRONE_CACHE["sim_drones"] = sim_drones
    _DRONE_CACHE["last_update_s"] = now_s
    _DRONE_CACHE["drones"] = [_drone_public_view(drone) for drone in sim_drones]


def _advance_drone_simulation(now_s: float) -> None:
    sim_drones = _DRONE_CACHE.get("sim_drones", [])
    if not isinstance(sim_drones, list):
        return

    last_update_s = float(_DRONE_CACHE.get("last_update_s", now_s))
    delta_s = max(0.0, now_s - last_update_s)
    drones_view: List[Dict[str, object]] = []

    for drone_state in sim_drones:
        route_length_m = float(drone_state.get("route_length_m", 0.0))
        speed_mps = float(drone_state.get("speed_mps", 0.0))
        distance_m = float(drone_state.get("distance_m", 0.0))

        if route_length_m > EPSILON and speed_mps > 0:
            distance_m = (distance_m + (speed_mps * delta_s)) % route_length_m
            drone_state["distance_m"] = distance_m

        route_xy = drone_state.get("route_xy", [])
        segment_lengths = drone_state.get("segment_lengths", [])
        if isinstance(route_xy, list) and isinstance(segment_lengths, list):
            point_xy = _point_along_polyline(route_xy, segment_lengths, distance_m)
            lat, lng = _xy_to_latlng(point_xy[0], point_xy[1], MONTREAL_REF_LAT, MONTREAL_REF_LNG)
            drone_state["lat"] = round(lat, 6)
            drone_state["lng"] = round(lng, 6)

        drones_view.append(_drone_public_view(drone_state))

    _DRONE_CACHE["last_update_s"] = now_s
    _DRONE_CACHE["drones"] = drones_view


def get_drones() -> List[Dict[str, object]]:
    signature = _coverage_signature()
    if _DRONE_CACHE["mtime_ns"] != signature or not _DRONE_CACHE.get("sim_drones"):
        _initialize_drone_simulation(signature)

    _advance_drone_simulation(datetime.now(timezone.utc).timestamp())
    return _DRONE_CACHE["drones"]  # type: ignore[return-value]


def _stable_index(value: str, modulo: int) -> int:
    if modulo <= 0:
        return 0
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % modulo


def _severity_from_index(index: int) -> str:
    bounded = max(0, min(index, len(SEVERITY_LEVELS) - 1))
    return SEVERITY_LEVELS[bounded]


def _severity_index_for_scan(index: int, borough: str, scan_key: int) -> int:
    base_index = (index * 3 + _stable_index(borough, 5)) % len(SEVERITY_LEVELS)
    drift_seed = _stable_index(f"{borough}-{index}", 11)
    drift = ((scan_key + drift_seed) % 3) - 1
    return max(0, min(len(SEVERITY_LEVELS) - 1, base_index + drift))


def _confidence_for_scan(index: int, team_id: str, scan_key: int) -> float:
    base = 0.56 + (((index * 13) + _stable_index(team_id, 19)) % 38) / 100.0
    jitter_seed = _stable_index(f"{team_id}-{index}", 13)
    jitter = (((scan_key + jitter_seed) % 5) - 2) * 0.01
    return round(max(0.45, min(0.99, base + jitter)), 2)


def _assignment_status_for_scan(index: int, severity: str, scan_key: int) -> str:
    if severity == "critical":
        return "in_progress"
    if ((index + scan_key) % 10) == 0:
        return "unassigned"
    return "assigned"


def _offset_with_bearing(lat: float, lng: float, bearing_deg: float, distance_m: float) -> LatLng:
    radians = math.radians(bearing_deg)
    north_m = math.cos(radians) * distance_m
    east_m = math.sin(radians) * distance_m
    return _offset_latlng(lat, lng, north_m, east_m)


def _team_for_borough(borough: str) -> Dict[str, str]:
    team_index = _stable_index(borough, len(TEAM_CATALOG))
    team = TEAM_CATALOG[team_index]
    return {"id": team["id"], "name": team["name"]}


def _geometry_points_latlng(geometry: Dict[str, object]) -> List[LatLng]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])

    if geometry_type == "Point" and isinstance(coordinates, list) and len(coordinates) >= 2:
        lng, lat = coordinates[0], coordinates[1]
        return [(float(lat), float(lng))]

    if geometry_type == "LineString" and isinstance(coordinates, list):
        return [(float(lat), float(lng)) for lng, lat in coordinates]

    if geometry_type == "Polygon" and isinstance(coordinates, list) and coordinates:
        ring = coordinates[0]
        points = [(float(lat), float(lng)) for lng, lat in ring]
        if len(points) > 1 and points[0] == points[-1]:
            return points[:-1]
        return points

    return []


def _geometry_centroid(geometry: Dict[str, object]) -> LatLng:
    points = _geometry_points_latlng(geometry)
    if not points:
        return (MONTREAL_REF_LAT, MONTREAL_REF_LNG)
    lat = sum(point[0] for point in points) / len(points)
    lng = sum(point[1] for point in points) / len(points)
    return lat, lng


def _geometry_for_drone(drone: Dict[str, object], index: int, scan_key: int) -> Dict[str, object]:
    lat = float(drone["lat"])
    lng = float(drone["lng"])
    geometry_selector = index % 6

    if geometry_selector in (0, 3):
        bearing = (index * 37) % 360
        segment_m = 110.0 + ((index % 4) * 45.0)
        endpoint_a = _offset_with_bearing(lat, lng, bearing, segment_m / 2.0)
        endpoint_b = _offset_with_bearing(lat, lng, bearing + 180.0, segment_m / 2.0)
        bend = _offset_with_bearing(lat, lng, bearing + 90.0, 16.0 if (index % 2) else -16.0)
        return {
            "type": "LineString",
            "coordinates": [
                [round(endpoint_b[1], 6), round(endpoint_b[0], 6)],
                [round(bend[1], 6), round(bend[0], 6)],
                [round(endpoint_a[1], 6), round(endpoint_a[0], 6)],
            ],
        }

    if geometry_selector in (4, 5):
        angle = math.radians((index * 19) % 360)
        radius_x = 45.0 + ((index % 3) * 14.0)
        radius_y = 30.0 + ((index % 2) * 12.0)
        corners_xy = [
            (-radius_x, -radius_y),
            (radius_x, -radius_y),
            (radius_x, radius_y),
            (-radius_x, radius_y),
        ]
        ring: List[List[float]] = []
        for east_base, north_base in corners_xy:
            east_m = (east_base * math.cos(angle)) - (north_base * math.sin(angle))
            north_m = (east_base * math.sin(angle)) + (north_base * math.cos(angle))
            corner = _offset_latlng(lat, lng, north_m, east_m)
            ring.append([round(corner[1], 6), round(corner[0], 6)])
        ring.append(ring[0])
        return {"type": "Polygon", "coordinates": [ring]}

    random_lat, random_lng = _random_city_point_latlng(scan_key, index)
    return {
        "type": "Point",
        "coordinates": [round(random_lng, 6), round(random_lat, 6)],
    }


def _defect_type_for_geometry(geometry_type: str, index: int) -> str:
    if geometry_type == "LineString":
        return LINE_DEFECT_TYPES[index % len(LINE_DEFECT_TYPES)]
    if geometry_type == "Polygon":
        return POLYGON_DEFECT_TYPES[index % len(POLYGON_DEFECT_TYPES)]
    return POINT_DEFECT_TYPES[index % len(POINT_DEFECT_TYPES)]


def _build_defects() -> Tuple[List[Dict[str, object]], str, str]:
    signature = _coverage_signature()
    scan_key = _scan_key()
    if _DEFECT_CACHE["signature"] == signature and _DEFECT_CACHE["scan_key"] == scan_key:
        return (
            _DEFECT_CACHE["defects"],  # type: ignore[return-value]
            _DEFECT_CACHE["scan_time"],  # type: ignore[return-value]
            _DEFECT_CACHE["previous_scan_time"],  # type: ignore[return-value]
        )

    previous_scan_key = scan_key - 1
    scan_time = _scan_timestamp(scan_key)
    previous_scan_time = _scan_timestamp(previous_scan_key)

    defects: List[Dict[str, object]] = []
    drones = get_drones()
    for drone_index, drone in enumerate(drones, start=1):
        for variant_index in range(DEFECTS_PER_DRONE):
            index = ((drone_index - 1) * DEFECTS_PER_DRONE) + variant_index + 1
            if not _is_defect_active(index, scan_key):
                continue

            geometry = _geometry_for_drone(drone, index, scan_key)
            geometry_type = str(geometry.get("type", "Point"))
            borough = str(drone.get("neighborhood", "Unknown"))
            team = _team_for_borough(borough)

            severity_index = _severity_index_for_scan(index, borough, scan_key)
            severity = _severity_from_index(severity_index)
            confidence = _confidence_for_scan(index, team["id"], scan_key)

            previous_severity: Optional[str] = None
            previous_confidence: Optional[float] = None
            previous_severity_index: Optional[int] = None
            if _is_defect_active(index, previous_scan_key):
                previous_severity_index = _severity_index_for_scan(index, borough, previous_scan_key)
                previous_severity = _severity_from_index(previous_severity_index)
                previous_confidence = _confidence_for_scan(index, team["id"], previous_scan_key)

            change_status = "new"
            if previous_severity_index is not None:
                if severity_index > previous_severity_index:
                    change_status = "worsened"
                elif severity_index < previous_severity_index:
                    change_status = "improved"
                else:
                    change_status = "stable"

            assignment_status = _assignment_status_for_scan(index, severity, scan_key)

            assignee_index = (index + _stable_index(team["id"], len(ASSIGNEES))) % len(ASSIGNEES)
            assignee = ASSIGNEES[assignee_index]

            centroid_lat, centroid_lng = _geometry_centroid(geometry)
            risk_multiplier = 1.2 if change_status in {"new", "worsened"} else 1.0
            if assignment_status == "unassigned":
                risk_multiplier *= 1.1
            risk_score = round(SEVERITY_WEIGHTS[severity] * confidence * risk_multiplier, 2)

            defect_id = f"DEF-{index:04d}"
            defect_type = _defect_type_for_geometry(geometry_type, index)
            photo_evidence = [
                {
                    "url": f"https://picsum.photos/seed/{defect_id}-a/420/260",
                    "caption": "Forward camera frame",
                },
                {
                    "url": f"https://picsum.photos/seed/{defect_id}-b/420/260",
                    "caption": "Side camera frame",
                },
            ]

            defect: Dict[str, object] = {
                "id": defect_id,
                "defect_type": defect_type,
                "geometry": geometry,
                "geometry_type": geometry_type,
                "lat": round(centroid_lat, 6),
                "lng": round(centroid_lng, 6),
                "severity": severity,
                "confidence": confidence,
                "risk_score": risk_score,
                "change_status": change_status,
                "borough": borough,
                "team": team,
                "assignee": assignee,
                "assignment_status": assignment_status,
                "observed_at": scan_time,
                "photo_evidence": photo_evidence,
                "source_scan": {
                    "scan_id": f"scan-{scan_key}",
                    "captured_at": scan_time,
                },
            }

            if previous_severity is not None and previous_confidence is not None:
                defect["previous_scan"] = {
                    "scan_id": f"scan-{previous_scan_key}",
                    "captured_at": previous_scan_time,
                    "severity": previous_severity,
                    "confidence": previous_confidence,
                }
            else:
                defect["previous_scan"] = None

            defects.append(defect)

    _DEFECT_CACHE["signature"] = signature
    _DEFECT_CACHE["scan_key"] = scan_key
    _DEFECT_CACHE["defects"] = defects
    _DEFECT_CACHE["scan_time"] = scan_time
    _DEFECT_CACHE["previous_scan_time"] = previous_scan_time
    return defects, scan_time, previous_scan_time


def _normalize_filter_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or normalized.lower() == "all":
        return None
    return normalized


def _matches_change_scope(change_status: str, change_scope: str) -> bool:
    if change_scope == "new_worsened":
        return change_status in {"new", "worsened"}
    if change_scope == "new":
        return change_status == "new"
    if change_scope == "worsened":
        return change_status == "worsened"
    return True


def get_defects(
    borough: Optional[str] = None,
    team: Optional[str] = None,
    change_scope: str = "all",
) -> List[Dict[str, object]]:
    defects, _, _ = _build_defects()
    borough_filter = _normalize_filter_value(borough)
    team_filter = _normalize_filter_value(team)
    normalized_scope = (change_scope or "all").strip().lower()

    filtered: List[Dict[str, object]] = []
    for defect in defects:
        if borough_filter and defect["borough"] != borough_filter:
            continue
        if team_filter and team_filter not in {defect["team"]["id"], defect["team"]["name"]}:
            continue
        if not _matches_change_scope(str(defect["change_status"]), normalized_scope):
            continue
        filtered.append(defect)
    return filtered


def _empty_change_counts() -> Dict[str, int]:
    return {"new": 0, "worsened": 0, "stable": 0, "improved": 0}


def _count_change_statuses(defects: Sequence[Dict[str, object]]) -> Dict[str, int]:
    counts = _empty_change_counts()
    for defect in defects:
        status = str(defect.get("change_status", "stable"))
        if status in counts:
            counts[status] += 1
    return counts


def get_defect_changes_summary(
    borough: Optional[str] = None,
    team: Optional[str] = None,
    change_scope: str = "all",
) -> Dict[str, object]:
    normalized_scope = (change_scope or "all").strip().lower()
    scoped_defects = get_defects(
        borough=borough,
        team=team,
        change_scope=normalized_scope,
    )
    all_region_defects = get_defects(
        borough=borough,
        team=team,
        change_scope="all",
    )
    scoped_counts = _count_change_statuses(scoped_defects)
    all_counts = _count_change_statuses(all_region_defects)

    new_worsened_by_severity = {level: 0 for level in SEVERITY_LEVELS}
    for defect in all_region_defects:
        change_status = str(defect.get("change_status", "stable"))
        if change_status not in {"new", "worsened"}:
            continue
        severity = str(defect.get("severity", "low"))
        if severity in new_worsened_by_severity:
            new_worsened_by_severity[severity] += 1

    _, scan_time, previous_scan_time = _build_defects()
    return {
        "current_scan_time": scan_time,
        "previous_scan_time": previous_scan_time,
        "scan_window_seconds": SCAN_WINDOW_SECONDS,
        "change_scope": normalized_scope,
        "counts": scoped_counts,
        "counts_all": all_counts,
        "filtered_total": len(scoped_defects),
        "region_total": len(all_region_defects),
        "new_worsened_total": all_counts["new"] + all_counts["worsened"],
        "new_worsened_by_severity": new_worsened_by_severity,
    }


def get_borough_team_view(
    borough: Optional[str] = None,
    team: Optional[str] = None,
    change_scope: str = "all",
) -> Dict[str, object]:
    defects = get_defects(borough=borough, team=team, change_scope=change_scope)
    all_defects = get_defects(change_scope="all")
    borough_stats: Dict[str, Dict[str, object]] = {}
    team_stats: Dict[str, Dict[str, object]] = {}

    for defect in defects:
        borough_name = str(defect["borough"])
        defect_team = defect["team"]
        team_id = str(defect_team["id"])
        team_name = str(defect_team["name"])
        severity = str(defect["severity"])
        confidence = float(defect["confidence"])
        risk_score = float(defect["risk_score"])
        change_status = str(defect["change_status"])
        assignment_status = str(defect["assignment_status"])

        if borough_name not in borough_stats:
            borough_stats[borough_name] = {
                "borough": borough_name,
                "team_id": team_id,
                "team_name": team_name,
                "total_defects": 0,
                "critical": 0,
                "new_worsened": 0,
                "assigned": 0,
                "in_progress": 0,
                "unassigned": 0,
                "confidence_sum": 0.0,
                "risk_sum": 0.0,
            }
        borough_stats[borough_name]["total_defects"] += 1
        borough_stats[borough_name]["confidence_sum"] += confidence
        borough_stats[borough_name]["risk_sum"] += risk_score
        if severity == "critical":
            borough_stats[borough_name]["critical"] += 1
        if change_status in {"new", "worsened"}:
            borough_stats[borough_name]["new_worsened"] += 1
        if assignment_status == "unassigned":
            borough_stats[borough_name]["unassigned"] += 1
        elif assignment_status == "in_progress":
            borough_stats[borough_name]["in_progress"] += 1
        else:
            borough_stats[borough_name]["assigned"] += 1

        if team_id not in team_stats:
            team_stats[team_id] = {
                "team_id": team_id,
                "team_name": team_name,
                "total_defects": 0,
                "critical": 0,
                "new_worsened": 0,
                "assigned": 0,
                "in_progress": 0,
                "unassigned": 0,
                "confidence_sum": 0.0,
                "risk_sum": 0.0,
            }
        team_stats[team_id]["total_defects"] += 1
        team_stats[team_id]["confidence_sum"] += confidence
        team_stats[team_id]["risk_sum"] += risk_score
        if severity == "critical":
            team_stats[team_id]["critical"] += 1
        if change_status in {"new", "worsened"}:
            team_stats[team_id]["new_worsened"] += 1
        if assignment_status == "unassigned":
            team_stats[team_id]["unassigned"] += 1
        elif assignment_status == "in_progress":
            team_stats[team_id]["in_progress"] += 1
        else:
            team_stats[team_id]["assigned"] += 1

    boroughs = []
    for borough_name, values in borough_stats.items():
        total = int(values["total_defects"])
        avg_confidence = (float(values["confidence_sum"]) / total) if total else 0.0
        avg_risk = (float(values["risk_sum"]) / total) if total else 0.0
        boroughs.append(
            {
                "borough": borough_name,
                "team_id": values["team_id"],
                "team_name": values["team_name"],
                "total_defects": total,
                "critical": values["critical"],
                "new_worsened": values["new_worsened"],
                "assigned": values["assigned"],
                "in_progress": values["in_progress"],
                "unassigned": values["unassigned"],
                "avg_confidence": round(avg_confidence, 2),
                "avg_risk": round(avg_risk, 2),
            }
        )

    teams: List[Dict[str, object]] = []
    for team_id, values in team_stats.items():
        total = int(values["total_defects"])
        avg_confidence = (float(values["confidence_sum"]) / total) if total else 0.0
        avg_risk = (float(values["risk_sum"]) / total) if total else 0.0
        teams.append(
            {
                "team_id": team_id,
                "team_name": values["team_name"],
                "total_defects": total,
                "critical": values["critical"],
                "new_worsened": values["new_worsened"],
                "assigned": values["assigned"],
                "in_progress": values["in_progress"],
                "unassigned": values["unassigned"],
                "avg_confidence": round(avg_confidence, 2),
                "avg_risk": round(avg_risk, 2),
            }
        )

    borough_catalog = sorted({str(defect["borough"]) for defect in all_defects})
    team_catalog: List[Dict[str, str]] = []
    team_seen = set()
    for defect in all_defects:
        team_values = defect["team"]
        team_id = str(team_values["id"])
        if team_id in team_seen:
            continue
        team_seen.add(team_id)
        team_catalog.append(
            {
                "team_id": team_id,
                "team_name": str(team_values["name"]),
            }
        )
    team_catalog.sort(key=lambda item: item["team_name"])

    normalized_scope = (change_scope or "all").strip().lower()
    _, scan_time, _ = _build_defects()
    return {
        "scan_time": scan_time,
        "filters": {
            "borough": _normalize_filter_value(borough) or "all",
            "team": _normalize_filter_value(team) or "all",
            "change_scope": normalized_scope,
        },
        "borough_catalog": borough_catalog,
        "team_catalog": team_catalog,
        "boroughs": sorted(
            boroughs,
            key=lambda item: (int(item["total_defects"]) * -1, str(item["borough"])),
        ),
        "teams": sorted(
            teams,
            key=lambda item: (int(item["total_defects"]) * -1, str(item["team_name"])),
        ),
    }


def _geometry_samples_for_heatmap(geometry: Dict[str, object]) -> List[LatLng]:
    geometry_type = str(geometry.get("type"))
    points = _geometry_points_latlng(geometry)
    if not points:
        return []

    if geometry_type == "Point":
        return points

    if geometry_type == "LineString":
        sampled = points.copy()
        for index in range(len(points) - 1):
            lat_mid = (points[index][0] + points[index + 1][0]) / 2.0
            lng_mid = (points[index][1] + points[index + 1][1]) / 2.0
            sampled.append((lat_mid, lng_mid))
        return sampled

    centroid = _geometry_centroid(geometry)
    every_second = points[::2] if len(points) > 2 else points
    return [centroid] + every_second


def get_heatmap_points(
    mode: str = "density",
    borough: Optional[str] = None,
    team: Optional[str] = None,
    change_scope: str = "all",
) -> List[List[float]]:
    normalized_mode = (mode or "density").strip().lower()
    defects = get_defects(borough=borough, team=team, change_scope=change_scope)
    points: List[List[float]] = []

    for defect in defects:
        samples = _geometry_samples_for_heatmap(defect["geometry"])
        if not samples:
            continue

        severity_weight = SEVERITY_WEIGHTS.get(str(defect["severity"]), 1.0)
        risk_score = float(defect["risk_score"])
        base_intensity = 0.38 + (severity_weight * 0.08)
        if normalized_mode == "risk":
            base_intensity = max(0.3, min(0.99, risk_score / 5.2))
        else:
            base_intensity = min(0.92, base_intensity)

        for sample_index, (lat, lng) in enumerate(samples):
            intensity = max(0.2, base_intensity - (sample_index * 0.04))
            points.append([round(lat, 6), round(lng, 6), round(min(intensity, 0.99), 2)])

    return points


def get_defects_geojson(
    borough: Optional[str] = None,
    team: Optional[str] = None,
    change_scope: str = "all",
) -> Dict[str, object]:
    defects = get_defects(borough=borough, team=team, change_scope=change_scope)
    return _defects_to_geojson(defects)


def _defects_to_geojson(defects: Sequence[Dict[str, object]]) -> Dict[str, object]:
    features: List[Dict[str, object]] = []
    for defect in defects:
        properties = {key: value for key, value in defect.items() if key != "geometry"}
        features.append(
            {
                "type": "Feature",
                "id": defect["id"],
                "geometry": defect["geometry"],
                "properties": properties,
            }
        )
    return {"type": "FeatureCollection", "features": features}


def get_defects_csv(
    borough: Optional[str] = None,
    team: Optional[str] = None,
    change_scope: str = "all",
) -> str:
    fieldnames = [
        "id",
        "defect_type",
        "geometry_type",
        "severity",
        "confidence",
        "risk_score",
        "change_status",
        "borough",
        "team_id",
        "team_name",
        "assignee",
        "assignment_status",
        "observed_at",
        "previous_observed_at",
        "previous_severity",
        "previous_confidence",
        "centroid_lat",
        "centroid_lng",
        "photo_1_url",
        "photo_2_url",
    ]

    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for defect in get_defects(borough=borough, team=team, change_scope=change_scope):
        previous_scan = defect.get("previous_scan") or {}
        photos = defect.get("photo_evidence", [])
        writer.writerow(
            {
                "id": defect["id"],
                "defect_type": defect["defect_type"],
                "geometry_type": defect["geometry_type"],
                "severity": defect["severity"],
                "confidence": defect["confidence"],
                "risk_score": defect["risk_score"],
                "change_status": defect["change_status"],
                "borough": defect["borough"],
                "team_id": defect["team"]["id"],
                "team_name": defect["team"]["name"],
                "assignee": defect["assignee"],
                "assignment_status": defect["assignment_status"],
                "observed_at": defect["observed_at"],
                "previous_observed_at": previous_scan.get("captured_at", ""),
                "previous_severity": previous_scan.get("severity", ""),
                "previous_confidence": previous_scan.get("confidence", ""),
                "centroid_lat": defect["lat"],
                "centroid_lng": defect["lng"],
                "photo_1_url": photos[0]["url"] if len(photos) > 0 else "",
                "photo_2_url": photos[1]["url"] if len(photos) > 1 else "",
            }
        )
    return output.getvalue()


def _geometry_in_bbox(geometry: Dict[str, object], bbox: Tuple[float, float, float, float]) -> bool:
    min_lng, min_lat, max_lng, max_lat = bbox
    for lat, lng in _geometry_points_latlng(geometry):
        if min_lat <= lat <= max_lat and min_lng <= lng <= max_lng:
            return True
    return False


def get_wms_like_features(
    bbox: Optional[Tuple[float, float, float, float]] = None,
    borough: Optional[str] = None,
    team: Optional[str] = None,
    change_scope: str = "all",
) -> Dict[str, object]:
    defects = get_defects(borough=borough, team=team, change_scope=change_scope)
    if bbox is not None:
        defects = [defect for defect in defects if _geometry_in_bbox(defect["geometry"], bbox)]

    geojson = _defects_to_geojson(defects)

    geojson["meta"] = {
        "service": "SkySpot WMS-like Defect Feed",
        "format": "geojson",
        "bbox": list(bbox) if bbox is not None else None,
        "feature_count": len(geojson["features"]),
        "filters": {
            "borough": _normalize_filter_value(borough) or "all",
            "team": _normalize_filter_value(team) or "all",
            "change_scope": (change_scope or "all").strip().lower(),
        },
    }
    return geojson


def get_potholes() -> List[Dict[str, object]]:
    point_defects = [defect for defect in get_defects() if str(defect.get("defect_type")) == "pothole"]
    potholes: List[Dict[str, object]] = []
    for defect in point_defects[:POTHOLE_SAMPLE_LIMIT]:
        potholes.append(
            {
                "id": str(defect["id"]),
                "lat": float(defect["lat"]),
                "lng": float(defect["lng"]),
                "severity": str(defect["severity"]),
                "confidence": float(defect["confidence"]),
            }
        )
    return potholes


def get_neighborhoods() -> Dict[str, object]:
    if not NEIGHBORHOODS_PATH.exists():
        return {"type": "FeatureCollection", "features": []}

    with NEIGHBORHOODS_PATH.open("r", encoding="utf-8-sig") as file:
        return json.load(file)
