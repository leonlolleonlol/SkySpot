from pathlib import Path

from flask import Flask, jsonify, send_from_directory

from data import get_drones, get_heatmap_points, get_neighborhoods, get_potholes

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")


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
    return jsonify(get_heatmap_points()), 200


@app.get("/api/neighborhoods")
def neighborhoods() -> tuple:
    return jsonify(get_neighborhoods()), 200


@app.get("/")
def index() -> object:
    return send_from_directory(FRONTEND_DIR, "index.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
