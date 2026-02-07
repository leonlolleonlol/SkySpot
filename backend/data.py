import json
from pathlib import Path
from typing import Dict, List

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
NEIGHBORHOODS_PATH = DATA_DIR / "neighborhoods.json"


def get_drones() -> List[Dict[str, object]]:
    return [
        {
            "id": "DRN-101",
            "lat": 37.7757,
            "lng": -122.4183,
            "status": "active",
            "speed_mps": 11.6,
        },
        {
            "id": "DRN-102",
            "lat": 37.7708,
            "lng": -122.4314,
            "status": "active",
            "speed_mps": 9.1,
        },
        {
            "id": "DRN-103",
            "lat": 37.7832,
            "lng": -122.4074,
            "status": "idle",
            "speed_mps": 0.0,
        },
    ]


def get_potholes() -> List[Dict[str, object]]:
    return [
        {"id": "PTH-01", "lat": 37.7739, "lng": -122.4212, "severity": "high"},
        {"id": "PTH-02", "lat": 37.7799, "lng": -122.4148, "severity": "medium"},
        {"id": "PTH-03", "lat": 37.7688, "lng": -122.4267, "severity": "low"},
    ]


def get_heatmap_points() -> List[List[float]]:
    return [
        [37.7750, -122.4195, 0.9],
        [37.7742, -122.4188, 0.8],
        [37.7730, -122.4210, 0.7],
        [37.7715, -122.4242, 0.6],
        [37.7792, -122.4130, 0.85],
        [37.7810, -122.4103, 0.5],
        [37.7690, -122.4290, 0.55],
    ]


def get_neighborhoods() -> Dict[str, object]:
    if not NEIGHBORHOODS_PATH.exists():
        return {"type": "FeatureCollection", "features": []}

    with NEIGHBORHOODS_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)
