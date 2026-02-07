const API_BASE = "";

const map = L.map("map").setView([45.5017, -73.5673], 13);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
}).addTo(map);

const droneLayer = L.layerGroup().addTo(map);
const potholeLayer = L.layerGroup().addTo(map);
let neighborhoodsLayer = null;
let heatLayer = null;
let heatVisible = true;

const droneCountEl = document.getElementById("droneCount");
const potholeCountEl = document.getElementById("potholeCount");
const heatCountEl = document.getElementById("heatCount");
const statusEl = document.getElementById("status");
const toggleHeatBtn = document.getElementById("toggleHeat");
const refreshDataBtn = document.getElementById("refreshData");

function setStatus(message) {
  statusEl.textContent = message;
}

async function fetchJson(path) {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(`Request failed: ${path}`);
  }
  return response.json();
}

function renderDrones(drones) {
  droneLayer.clearLayers();

  drones.forEach((drone) => {
    const marker = L.circleMarker([drone.lat, drone.lng], {
      radius: 8,
      color: "#0d7a5f",
      weight: 2,
      fillColor: "#28b189",
      fillOpacity: 0.7
    }).addTo(droneLayer);

    marker.bindPopup(`
      <strong>${drone.id}</strong><br />
      Status: ${drone.status}<br />
      Speed: ${drone.speed_mps} m/s
    `);
  });

  droneCountEl.textContent = String(drones.length);
}

function renderPotholes(potholes) {
  potholeLayer.clearLayers();

  potholes.forEach((spot) => {
    const marker = L.circleMarker([spot.lat, spot.lng], {
      radius: 6,
      color: "#7b2112",
      weight: 1,
      fillColor: "#cc5e3d",
      fillOpacity: 0.75
    }).addTo(potholeLayer);

    marker.bindPopup(`
      <strong>${spot.id}</strong><br />
      Severity: ${spot.severity}
    `);
  });

  potholeCountEl.textContent = String(potholes.length);
}

function renderHeatmap(points) {
  if (heatLayer) {
    map.removeLayer(heatLayer);
  }

  heatLayer = L.heatLayer(points, {
    radius: 24,
    blur: 18,
    maxZoom: 17,
    gradient: {
      0.2: "#90d9c6",
      0.45: "#52bf9f",
      0.7: "#f7b24f",
      1.0: "#cf4c2f"
    }
  });

  if (heatVisible) {
    heatLayer.addTo(map);
  }

  heatCountEl.textContent = String(points.length);
}

function renderNeighborhoods(geojson) {
  if (neighborhoodsLayer) {
    map.removeLayer(neighborhoodsLayer);
  }

  neighborhoodsLayer = L.geoJSON(geojson, {
    style: {
      color: "#2f5149",
      weight: 1.2,
      fillColor: "#95bfae",
      fillOpacity: 0.16
    },
    onEachFeature: (feature, layer) => {
      const name = feature.properties?.name ?? "Neighborhood";
      layer.bindTooltip(name);
    }
  }).addTo(map);
}

async function loadData() {
  setStatus("Refreshing map data...");

  try {
    const [drones, potholes, heatmapPoints, neighborhoods] = await Promise.all([
      fetchJson("/api/drones"),
      fetchJson("/api/potholes"),
      fetchJson("/api/heatmap"),
      fetchJson("/api/neighborhoods")
    ]);

    renderDrones(drones);
    renderPotholes(potholes);
    renderHeatmap(heatmapPoints);
    renderNeighborhoods(neighborhoods);

    setStatus(`Updated at ${new Date().toLocaleTimeString()}`);
  } catch (error) {
    setStatus("Data load failed. Make sure backend/app.py is running on port 5000.");
    console.error(error);
  }
}

toggleHeatBtn.addEventListener("click", () => {
  heatVisible = !heatVisible;

  if (heatLayer) {
    if (heatVisible) {
      heatLayer.addTo(map);
      toggleHeatBtn.textContent = "Hide Heatmap";
    } else {
      map.removeLayer(heatLayer);
      toggleHeatBtn.textContent = "Show Heatmap";
    }
  }
});

refreshDataBtn.addEventListener("click", loadData);

loadData();
setInterval(loadData, 15000);
