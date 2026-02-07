import json
from pathlib import Path
from typing import Dict, List

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
NEIGHBORHOODS_PATH = DATA_DIR / "neighborhoods.json"


def get_drones() -> List[Dict[str, object]]:
    return [
        {
            "id": "DRN-101",
            "lat": 45.5031,
            "lng": -73.5693,
            "status": "active",
            "speed_mps": 11.6,
        },
        {
            "id": "DRN-102",
            "lat": 45.4976,
            "lng": -73.5794,
            "status": "active",
            "speed_mps": 9.1,
        },
        {
            "id": "DRN-103",
            "lat": 45.5107,
            "lng": -73.5557,
            "status": "idle",
            "speed_mps": 0.0,
        },
    ]


def get_potholes() -> List[Dict[str, object]]:
    return [
        {"id": "PTH-01", "lat": 45.5044, "lng": -73.5715, "severity": "high"},
        {"id": "PTH-02", "lat": 45.5001, "lng": -73.5622, "severity": "medium"},
        {"id": "PTH-03", "lat": 45.4958, "lng": -73.5768, "severity": "low"},
    ]


def get_heatmap_points() -> List[List[float]]:
    return [
        [45.5023, -73.5684, 0.9],
        [45.5010, -73.5660, 0.8],
        [45.4998, -73.5709, 0.7],
        [45.4986, -73.5743, 0.6],
        [45.5052, -73.5608, 0.85],
        [45.5084, -73.5576, 0.5],
        [45.4969, -73.5799, 0.55],
    ]


def get_neighborhoods() -> Dict[str, object]:
    if not NEIGHBORHOODS_PATH.exists():
        return {"type": "FeatureCollection", "features": []}

    with NEIGHBORHOODS_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)
