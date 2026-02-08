const API_BASE = "";
const DRONE_COVERAGE_AREA_KM2 = 10;
const DEFAULT_COVERAGE_RADIUS_M = Math.sqrt((DRONE_COVERAGE_AREA_KM2 * 1_000_000) / Math.PI);
const DATA_REFRESH_MS = 5000;
const EARTH_RADIUS_M = 6_371_000;
const REF_LAT_RAD = (45.5017 * Math.PI) / 180;
const MOBILE_LAYOUT_QUERY = "(max-width: 960px)";

const map = L.map("map").setView([45.5017, -73.5673], 11);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
}).addTo(map);

const droneLayer = L.layerGroup().addTo(map);
const droneZoneLayer = L.layerGroup().addTo(map);
const defectLayer = L.layerGroup().addTo(map);
const heatFallbackLayer = L.layerGroup().addTo(map);
let neighborhoodsLayer = null;
let heatLayer = null;
let hasFittedNeighborhoodBounds = false;
const supportsLeafletHeat = typeof L.heatLayer === "function";

const state = {
  dronesVisible: true,
  zonesVisible: true,
  defectsVisible: true,
  heatMode: "off"
};

let latestDefects = [];
let latestHeatPoints = [];
const droneVisuals = new Map();
let lastDroneFrameTs = null;
let loadInFlight = false;
let reloadQueued = false;

const droneDiamondIcon = L.divIcon({
  className: "drone-diamond-icon",
  html: '<span class="diamond-core"></span>',
  iconSize: [18, 18],
  iconAnchor: [9, 9],
  popupAnchor: [0, -10]
});

const severityColors = {
  low: "#3d8a56",
  medium: "#c08726",
  high: "#b55426",
  critical: "#9d2e23"
};

const assignmentColors = {
  unassigned: "#6d7588",
  assigned: "#2567a4",
  in_progress: "#157a65"
};

const activeDroneCountEl = document.getElementById("activeDroneCount");
const defectCountEl = document.getElementById("defectCount");
const criticalDefectCountEl = document.getElementById("criticalDefectCount");
const newWorsenedCountEl = document.getElementById("newWorsenedCount");
const avgConfidenceEl = document.getElementById("avgConfidence");
const assignedCountEl = document.getElementById("assignedCount");
const heatCountEl = document.getElementById("heatCount");
const teamCountEl = document.getElementById("teamCount");
const layerModeEl = document.getElementById("layerMode");
const statusEl = document.getElementById("status");
const scanWindowEl = document.getElementById("scanWindow");
const changeNewCountEl = document.getElementById("changeNewCount");
const changeWorsenedCountEl = document.getElementById("changeWorsenedCount");
const changeStableCountEl = document.getElementById("changeStableCount");
const changeImprovedCountEl = document.getElementById("changeImprovedCount");
const changeScopeSummaryEl = document.getElementById("changeScopeSummary");
const severityTrendEl = document.getElementById("severityTrend");
const boroughOwnershipBodyEl = document.getElementById("boroughOwnershipBody");
const teamOwnershipBodyEl = document.getElementById("teamOwnershipBody");

const toggleDronesBtn = document.getElementById("toggleDrones");
const toggleDroneZonesBtn = document.getElementById("toggleDroneZones");
const toggleDefectsBtn = document.getElementById("toggleDefects");
const refreshDataBtn = document.getElementById("refreshData");

const heatModeEl = document.getElementById("heatMode");
const changeScopeEl = document.getElementById("changeScope");
const boroughFilterEl = document.getElementById("boroughFilter");
const teamFilterEl = document.getElementById("teamFilter");
const viewModeEl = document.getElementById("viewMode");

const exportGeoJsonEl = document.getElementById("exportGeoJson");
const exportCsvEl = document.getElementById("exportCsv");
const wmsLikeUrlEl = document.getElementById("wmsLikeUrl");
const copyWmsUrlBtn = document.getElementById("copyWmsUrl");
const mobileMapToggleBtn = document.getElementById("mobileMapToggle");
const mobileLayoutMedia = window.matchMedia(MOBILE_LAYOUT_QUERY);
let mobileMapOnly = false;

function setStatus(message) {
  statusEl.textContent = message;
}

function formatScanTime(value) {
  if (!value) {
    return "--";
  }
  const time = new Date(value);
  if (Number.isNaN(time.getTime())) {
    return String(value);
  }
  return time.toLocaleString();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function colorForName(name) {
  const palette = ["#2f5149", "#4f6d60", "#5f7a6f", "#6d8f82", "#8ea99f", "#556f95", "#8b7d4e"];
  let hash = 0;
  const safeName = String(name || "");
  for (let index = 0; index < safeName.length; index += 1) {
    hash = (hash << 5) - hash + safeName.charCodeAt(index);
    hash |= 0;
  }
  return palette[Math.abs(hash) % palette.length];
}

function currentFilters() {
  return {
    borough: boroughFilterEl.value,
    team: teamFilterEl.value,
    changeScope: changeScopeEl.value,
    heatMode: heatModeEl.value
  };
}

function filtersToQuery(filters) {
  const params = new URLSearchParams();
  if (filters.borough && filters.borough !== "all") {
    params.set("borough", filters.borough);
  }
  if (filters.team && filters.team !== "all") {
    params.set("team", filters.team);
  }
  if (filters.changeScope && filters.changeScope !== "all") {
    params.set("change_scope", filters.changeScope);
  }
  return params;
}

function withQuery(path, params) {
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

function updateLayerModeText() {
  const heatLabel = state.heatMode === "off" ? "Off" : state.heatMode === "risk" ? "Risk" : "Density";
  layerModeEl.textContent = [
    `Layers: Drones ${state.dronesVisible ? "On" : "Off"}`,
    `Zones ${state.zonesVisible ? "On" : "Off"}`,
    `Defects ${state.defectsVisible ? "On" : "Off"}`,
    `Heat ${heatLabel}`
  ].join(" | ");
}

function updateMobileMapToggleUi() {
  if (!mobileMapToggleBtn) {
    return;
  }
  const enabled = mobileLayoutMedia.matches && mobileMapOnly;
  mobileMapToggleBtn.textContent = enabled ? "Show Panel" : "Map Only";
  mobileMapToggleBtn.setAttribute("aria-pressed", enabled ? "true" : "false");
}

function applyMobileMapOnlyMode() {
  if (mobileLayoutMedia.matches && mobileMapOnly) {
    document.body.classList.add("mobile-map-only");
  } else {
    document.body.classList.remove("mobile-map-only");
  }
  updateMobileMapToggleUi();
  window.requestAnimationFrame(() => {
    map.invalidateSize();
  });
}

function applyLayerVisibility() {
  if (state.dronesVisible) {
    droneLayer.addTo(map);
  } else {
    map.removeLayer(droneLayer);
  }

  if (state.zonesVisible) {
    droneZoneLayer.addTo(map);
  } else {
    map.removeLayer(droneZoneLayer);
  }

  if (state.defectsVisible) {
    defectLayer.addTo(map);
  } else {
    map.removeLayer(defectLayer);
  }

  if (heatLayer) {
    if (state.heatMode === "off") {
      map.removeLayer(heatLayer);
    } else {
      heatLayer.addTo(map);
    }
  }
  if (state.heatMode === "off") {
    map.removeLayer(heatFallbackLayer);
  } else if (heatFallbackLayer.getLayers().length) {
    heatFallbackLayer.addTo(map);
  }

  updateLayerModeText();
}

async function fetchJson(path) {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(`Request failed: ${path}`);
  }
  return response.json();
}

function defectStyle(defect) {
  const viewMode = viewModeEl.value;
  const severity = String(defect.severity || "low");
  const changeStatus = String(defect.change_status || "stable");
  const assignment = String(defect.assignment_status || "assigned");
  const baseColorBySeverity = severityColors[severity] || "#6d7588";
  let color = baseColorBySeverity;

  if (viewMode === "borough") {
    color = colorForName(defect.borough);
  } else if (viewMode === "team") {
    color = colorForName(defect.team?.name || defect.team?.id || "");
  } else if (viewMode === "assignment") {
    color = assignmentColors[assignment] || "#6d7588";
  }

  return {
    color,
    weight: changeStatus === "new" || changeStatus === "worsened" ? 3 : 2,
    fillColor: color,
    fillOpacity: 0.22,
    dashArray: changeStatus === "new" ? "2 4" : undefined
  };
}

function toLatLngPoint(coordinates) {
  return [coordinates[1], coordinates[0]];
}

function toLatLngLine(coordinates) {
  return coordinates.map((coord) => [coord[1], coord[0]]);
}

function toLatLngPolygon(coordinates) {
  const ring = Array.isArray(coordinates) && coordinates.length ? coordinates[0] : [];
  return ring.map((coord) => [coord[1], coord[0]]);
}

function defectPopupHtml(defect) {
  const severity = escapeHtml(defect.severity);
  const changeStatus = escapeHtml(defect.change_status);
  const confidence = Number(defect.confidence || 0).toFixed(2);
  const riskScore = Number(defect.risk_score || 0).toFixed(2);
  const previous = defect.previous_scan;
  const previousInfo = previous
    ? `Previous: ${escapeHtml(previous.severity)} (${Number(previous.confidence).toFixed(2)})`
    : "Previous: none (new in current scan)";

  const photos = Array.isArray(defect.photo_evidence) ? defect.photo_evidence : [];
  const photoStrip = photos.slice(0, 2).map((photo) => {
    const url = escapeHtml(photo.url || "");
    const caption = escapeHtml(photo.caption || "Photo evidence");
    return `<a href="${url}" target="_blank" rel="noopener"><img src="${url}" alt="${caption}" title="${caption}" loading="lazy" /></a>`;
  }).join("");

  return `
    <div class="defect-popup">
      <strong>${escapeHtml(defect.id)}</strong><br />
      Type: ${escapeHtml(defect.defect_type)}<br />
      <span class="severity-pill ${severity}">${severity}</span>
      Confidence: ${confidence}<br />
      Risk: ${riskScore} | Change: ${changeStatus}<br />
      Borough: ${escapeHtml(defect.borough)}<br />
      Team: ${escapeHtml(defect.team?.name || "Unassigned")}<br />
      Assignee: ${escapeHtml(defect.assignee || "Unassigned")}<br />
      Assignment: ${escapeHtml(defect.assignment_status)}<br />
      ${escapeHtml(previousInfo)}<br />
      Observed: ${escapeHtml(defect.observed_at)}
      <div class="photo-strip">${photoStrip}</div>
    </div>
  `;
}

function dronePopupHtml(drone) {
  return `
    <strong>${escapeHtml(drone.id)}</strong><br />
    Neighborhood: ${escapeHtml(drone.neighborhood || "Unassigned")}<br />
    Status: ${escapeHtml(drone.status)}<br />
    Speed: ${Number(drone.speed_mps || 0).toFixed(1)} m/s<br />
    Coverage: ${DRONE_COVERAGE_AREA_KM2} km^2
  `;
}

function normalizeRoute(route) {
  if (!Array.isArray(route)) {
    return [];
  }
  return route
    .map((point) => {
      if (!Array.isArray(point) || point.length < 2) {
        return null;
      }
      const lat = Number(point[0]);
      const lng = Number(point[1]);
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
        return null;
      }
      return [lat, lng];
    })
    .filter((point) => point !== null);
}

function latLngToXYMeters(lat, lng) {
  const latRad = (lat * Math.PI) / 180;
  const lngRad = (lng * Math.PI) / 180;
  return [
    EARTH_RADIUS_M * lngRad * Math.cos(REF_LAT_RAD),
    EARTH_RADIUS_M * latRad
  ];
}

function buildRouteMetrics(route) {
  if (!Array.isArray(route) || route.length < 2) {
    return { routeXY: [], segmentLengths: [], routeLengthM: 0 };
  }

  const routeXY = route.map(([lat, lng]) => latLngToXYMeters(lat, lng));
  const segmentLengths = [];
  let routeLengthM = 0;
  for (let index = 0; index < routeXY.length - 1; index += 1) {
    const dx = routeXY[index + 1][0] - routeXY[index][0];
    const dy = routeXY[index + 1][1] - routeXY[index][1];
    const distanceM = Math.hypot(dx, dy);
    segmentLengths.push(distanceM);
    routeLengthM += distanceM;
  }
  return { routeXY, segmentLengths, routeLengthM };
}

function pointAlongRoute(route, segmentLengths, routeLengthM, distanceM) {
  if (!Array.isArray(route) || !route.length) {
    return [45.5017, -73.5673];
  }
  if (route.length === 1 || routeLengthM <= 0) {
    return route[0];
  }

  let remaining = ((distanceM % routeLengthM) + routeLengthM) % routeLengthM;
  for (let index = 0; index < segmentLengths.length; index += 1) {
    const segmentLength = segmentLengths[index];
    if (segmentLength <= 0) {
      continue;
    }
    if (remaining <= segmentLength || index === segmentLengths.length - 1) {
      const start = route[index];
      const end = route[index + 1];
      const ratio = remaining / segmentLength;
      return [
        start[0] + ((end[0] - start[0]) * ratio),
        start[1] + ((end[1] - start[1]) * ratio)
      ];
    }
    remaining -= segmentLength;
  }

  return route[route.length - 1];
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function heatColorForIntensity(intensity) {
  const stops = [
    { t: 0.2, rgb: [11, 93, 30] },   // forest green
    { t: 0.45, rgb: [96, 140, 34] }, // olive green
    { t: 0.7, rgb: [194, 124, 24] }, // amber
    { t: 1.0, rgb: [106, 0, 0] }     // dark red
  ];

  const normalized = clamp(Number(intensity), 0.2, 1.0);
  for (let index = 0; index < stops.length - 1; index += 1) {
    const start = stops[index];
    const end = stops[index + 1];
    if (normalized > end.t) {
      continue;
    }
    const ratio = (normalized - start.t) / (end.t - start.t);
    const red = Math.round(start.rgb[0] + ((end.rgb[0] - start.rgb[0]) * ratio));
    const green = Math.round(start.rgb[1] + ((end.rgb[1] - start.rgb[1]) * ratio));
    const blue = Math.round(start.rgb[2] + ((end.rgb[2] - start.rgb[2]) * ratio));
    return `rgb(${red}, ${green}, ${blue})`;
  }
  return "rgb(106, 0, 0)";
}

function projectDistanceOnRoute(routeXY, segmentLengths, routeLengthM, pointLatLng) {
  if (!Array.isArray(routeXY) || routeXY.length < 2 || routeLengthM <= 0) {
    return 0;
  }

  const pointXY = latLngToXYMeters(pointLatLng[0], pointLatLng[1]);
  let traversedM = 0;
  let bestDistance2 = Number.POSITIVE_INFINITY;
  let bestDistanceAlongM = 0;

  for (let index = 0; index < segmentLengths.length; index += 1) {
    const segmentLength = segmentLengths[index];
    const start = routeXY[index];
    const end = routeXY[index + 1];
    if (segmentLength <= 0) {
      continue;
    }

    const vx = end[0] - start[0];
    const vy = end[1] - start[1];
    const wx = pointXY[0] - start[0];
    const wy = pointXY[1] - start[1];
    const projection = ((wx * vx) + (wy * vy)) / (segmentLength * segmentLength);
    const t = Math.max(0, Math.min(1, projection));
    const projX = start[0] + (vx * t);
    const projY = start[1] + (vy * t);
    const dx = pointXY[0] - projX;
    const dy = pointXY[1] - projY;
    const distance2 = (dx * dx) + (dy * dy);

    if (distance2 < bestDistance2) {
      bestDistance2 = distance2;
      bestDistanceAlongM = traversedM + (segmentLength * t);
    }
    traversedM += segmentLength;
  }

  return bestDistanceAlongM;
}

function animateDrones(frameTs) {
  if (lastDroneFrameTs === null) {
    lastDroneFrameTs = frameTs;
  }

  const deltaS = Math.max(0, Math.min(0.25, (frameTs - lastDroneFrameTs) / 1000));
  lastDroneFrameTs = frameTs;

  droneVisuals.forEach((visual) => {
    const canAnimate = visual.status === "active"
      && visual.speedMps > 0
      && visual.routeLengthM > 0
      && visual.route.length >= 2;

    if (!canAnimate) {
      return;
    }

    visual.distanceM = (visual.distanceM + (visual.speedMps * deltaS)) % visual.routeLengthM;
    const nextPosition = pointAlongRoute(
      visual.route,
      visual.segmentLengths,
      visual.routeLengthM,
      visual.distanceM
    );
    visual.marker.setLatLng(nextPosition);
  });

  window.requestAnimationFrame(animateDrones);
}

function renderDrones(drones) {
  droneZoneLayer.clearLayers();
  const seenDroneIds = new Set();

  drones.forEach((drone) => {
    seenDroneIds.add(drone.id);

    if (Array.isArray(drone.road_route) && drone.road_route.length >= 2) {
      L.polyline(drone.road_route, {
        color: "#5f6866",
        weight: 1,
        opacity: 0.35
      }).addTo(droneZoneLayer);
    }

    if (Array.isArray(drone.coverage_polygon) && drone.coverage_polygon.length >= 3) {
      L.polygon(drone.coverage_polygon, {
        color: "#2f5149",
        weight: 1.1,
        fillColor: "#7fc7b2",
        fillOpacity: 0.08
      }).addTo(droneZoneLayer);
    } else {
      const coverageRadius = drone.coverage_radius_m ?? DEFAULT_COVERAGE_RADIUS_M;
      L.circle([drone.lat, drone.lng], {
        radius: coverageRadius,
        color: "#2f5149",
        weight: 1.2,
        fillColor: "#7fc7b2",
        fillOpacity: 0.08,
        dashArray: "5 5"
      }).addTo(droneZoneLayer);
    }

    let visual = droneVisuals.get(drone.id);
    if (!visual) {
      const marker = L.marker([drone.lat, drone.lng], {
        icon: droneDiamondIcon,
        title: drone.id
      }).addTo(droneLayer);
      visual = {
        marker,
        route: [],
        routeXY: [],
        segmentLengths: [],
        routeLengthM: 0,
        distanceM: 0,
        speedMps: 0,
        status: "idle"
      };
      droneVisuals.set(drone.id, visual);
    }

    const normalizedRoute = normalizeRoute(drone.road_route);
    const speedMps = Math.max(0, Number(drone.speed_mps || 0));
    const status = String(drone.status || "idle");
    const lat = Number(drone.lat);
    const lng = Number(drone.lng);

    visual.speedMps = speedMps;
    visual.status = status;
    visual.route = normalizedRoute;

    if (
      normalizedRoute.length >= 2
      && Number.isFinite(lat)
      && Number.isFinite(lng)
    ) {
      const { routeXY, segmentLengths, routeLengthM } = buildRouteMetrics(normalizedRoute);
      const distanceM = projectDistanceOnRoute(
        routeXY,
        segmentLengths,
        routeLengthM,
        [lat, lng]
      );
      visual.routeXY = routeXY;
      visual.segmentLengths = segmentLengths;
      visual.routeLengthM = routeLengthM;
      visual.distanceM = distanceM;
      visual.marker.setLatLng(pointAlongRoute(normalizedRoute, segmentLengths, routeLengthM, distanceM));
    } else {
      visual.routeXY = [];
      visual.segmentLengths = [];
      visual.routeLengthM = 0;
      visual.distanceM = 0;
      if (Number.isFinite(lat) && Number.isFinite(lng)) {
        visual.marker.setLatLng([lat, lng]);
      }
    }

    visual.marker.bindPopup(dronePopupHtml(drone));
  });

  Array.from(droneVisuals.entries()).forEach(([droneId, visual]) => {
    if (seenDroneIds.has(droneId)) {
      return;
    }
    droneLayer.removeLayer(visual.marker);
    droneVisuals.delete(droneId);
  });
}

function renderDefects(defects) {
  latestDefects = defects;
  defectLayer.clearLayers();

  defects.forEach((defect) => {
    const style = defectStyle(defect);
    const geometry = defect.geometry || {};
    const geometryType = geometry.type;
    let layer = null;

    if (geometryType === "Point") {
      layer = L.circleMarker(toLatLngPoint(geometry.coordinates), {
        radius: 7,
        color: style.color,
        fillColor: style.fillColor,
        fillOpacity: 0.86,
        weight: style.weight
      });
    } else if (geometryType === "LineString") {
      layer = L.polyline(toLatLngLine(geometry.coordinates), style);
    } else if (geometryType === "Polygon") {
      layer = L.polygon(toLatLngPolygon(geometry.coordinates), style);
    }

    if (layer) {
      layer.addTo(defectLayer);
      layer.bindPopup(defectPopupHtml(defect), { maxWidth: 320 });
    }
  });
}

function renderHeatmap(points) {
  latestHeatPoints = Array.isArray(points) ? points : [];
  if (heatLayer) {
    map.removeLayer(heatLayer);
  }
  heatFallbackLayer.clearLayers();

  if (state.heatMode === "off") {
    heatCountEl.textContent = "0";
    heatLayer = null;
    return;
  }

  if (!supportsLeafletHeat) {
    latestHeatPoints.forEach((point) => {
      const lat = Number(point?.[0]);
      const lng = Number(point?.[1]);
      const intensity = Math.max(0.2, Math.min(0.99, Number(point?.[2] ?? 0.3)));
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
        return;
      }
      const radiusM = state.heatMode === "risk"
        ? 760 + (intensity * 920)
        : 820 + (intensity * 980);
      L.circle([lat, lng], {
        radius: radiusM,
        color: "transparent",
        stroke: false,
        fillColor: heatColorForIntensity(intensity),
        fillOpacity: 0.26 + (intensity * 0.32),
        interactive: false
      }).addTo(heatFallbackLayer);
    });
    heatLayer = null;
    heatFallbackLayer.addTo(map);
    heatCountEl.textContent = String(latestHeatPoints.length);
    return;
  }

  const gradient = {
    0.2: "#0b5d1e",
    0.45: "#608c22",
    0.7: "#c27c18",
    1.0: "#6a0000"
  };

  heatLayer = L.heatLayer(latestHeatPoints, {
    radius: state.heatMode === "risk" ? 52 : 46,
    blur: 38,
    maxZoom: 17,
    max: state.heatMode === "risk" ? 0.8 : 0.75,
    minOpacity: 0.3,
    gradient
  });
  heatLayer.addTo(map);
  heatCountEl.textContent = String(latestHeatPoints.length);
}

function renderNeighborhoods(geojson) {
  if (neighborhoodsLayer) {
    map.removeLayer(neighborhoodsLayer);
  }

  neighborhoodsLayer = L.geoJSON(geojson, {
    style: (feature) => {
      const name = feature.properties?.name ?? "Neighborhood";
      const stroke = colorForName(name);
      return {
        color: stroke,
        weight: 1.8,
        fillColor: stroke,
        fillOpacity: 0.08
      };
    },
    onEachFeature: (feature, layer) => {
      const name = feature.properties?.name ?? "Neighborhood";
      layer.bindTooltip(name, { sticky: true, direction: "center" });
    }
  }).addTo(map);

  if (!hasFittedNeighborhoodBounds) {
    const bounds = neighborhoodsLayer.getBounds();
    if (bounds.isValid()) {
      map.fitBounds(bounds.pad(0.02));
      hasFittedNeighborhoodBounds = true;
    }
  }
}

function populateSelect(selectEl, options, selectedValue, defaultOption) {
  const preserved = selectedValue ?? selectEl.value;
  selectEl.innerHTML = "";

  if (defaultOption) {
    const defaultEl = document.createElement("option");
    defaultEl.value = defaultOption.value;
    defaultEl.textContent = defaultOption.label;
    selectEl.appendChild(defaultEl);
  }

  options.forEach((option) => {
    const opt = document.createElement("option");
    opt.value = option.value;
    opt.textContent = option.label;
    selectEl.appendChild(opt);
  });

  if (preserved && Array.from(selectEl.options).some((opt) => opt.value === preserved)) {
    selectEl.value = preserved;
  }
}

function updateOwnershipFilters(viewData) {
  const boroughCountByName = new Map(
    (viewData.boroughs || []).map((item) => [item.borough, item.total_defects])
  );
  const teamCountById = new Map(
    (viewData.teams || []).map((item) => [item.team_id, item.total_defects])
  );

  const boroughCatalog = Array.isArray(viewData.borough_catalog) ? viewData.borough_catalog : [];
  const teamCatalog = Array.isArray(viewData.team_catalog) ? viewData.team_catalog : [];
  const boroughOptions = boroughCatalog.map((boroughName) => {
    const count = boroughCountByName.get(boroughName);
    return {
      value: boroughName,
      label: count !== undefined ? `${boroughName} (${count})` : boroughName
    };
  });
  const teamOptions = teamCatalog.map((teamItem) => {
    const count = teamCountById.get(teamItem.team_id);
    const labelBase = teamItem.team_name || teamItem.team_id;
    return {
      value: teamItem.team_id,
      label: count !== undefined ? `${labelBase} (${count})` : labelBase
    };
  });

  populateSelect(
    boroughFilterEl,
    boroughOptions,
    boroughFilterEl.value,
    { value: "all", label: "All Boroughs" }
  );
  populateSelect(
    teamFilterEl,
    teamOptions,
    teamFilterEl.value,
    { value: "all", label: "All Teams" }
  );
}

function renderChangeSummary(changes) {
  const counts = changes?.counts_all || changes?.counts || {};
  changeNewCountEl.textContent = String(counts.new || 0);
  changeWorsenedCountEl.textContent = String(counts.worsened || 0);
  changeStableCountEl.textContent = String(counts.stable || 0);
  changeImprovedCountEl.textContent = String(counts.improved || 0);

  const currentScan = formatScanTime(changes?.current_scan_time);
  const previousScan = formatScanTime(changes?.previous_scan_time);
  scanWindowEl.textContent = `Current scan: ${currentScan} | Previous scan: ${previousScan}`;

  const scope = changes?.change_scope || "all";
  const filteredTotal = Number(changes?.filtered_total || 0);
  const regionTotal = Number(changes?.region_total || 0);
  const scanCadenceSeconds = Number(changes?.scan_window_seconds || 0);
  const cadenceText = scanCadenceSeconds > 0 ? ` | Scan cadence: ${scanCadenceSeconds}s` : "";
  changeScopeSummaryEl.textContent = `Scope: ${scope} | Filtered: ${filteredTotal} / ${regionTotal}${cadenceText}`;

  const severityBreakdown = changes?.new_worsened_by_severity || {};
  const order = ["critical", "high", "medium", "low"];
  severityTrendEl.innerHTML = order.map((level) => {
    const total = Number(severityBreakdown[level] || 0);
    return `<span class="severity-chip ${level}">${escapeHtml(level)}: ${total}</span>`;
  }).join("");
}

function renderOwnershipTables(viewData) {
  const boroughRows = Array.isArray(viewData?.boroughs) ? viewData.boroughs : [];
  const teamRows = Array.isArray(viewData?.teams) ? viewData.teams : [];

  boroughOwnershipBodyEl.innerHTML = boroughRows.slice(0, 10).map((row) => `
    <tr>
      <td>${escapeHtml(row.borough)}</td>
      <td>${Number(row.total_defects || 0)}</td>
      <td>${Number(row.new_worsened || 0)}</td>
      <td>${Number(row.unassigned || 0)}</td>
      <td>${Number(row.avg_risk || 0).toFixed(2)}</td>
    </tr>
  `).join("");

  teamOwnershipBodyEl.innerHTML = teamRows.slice(0, 10).map((row) => `
    <tr>
      <td>${escapeHtml(row.team_name || row.team_id)}</td>
      <td>${Number(row.total_defects || 0)}</td>
      <td>${Number(row.in_progress || 0)}</td>
      <td>${Number(row.assigned || 0)}</td>
      <td>${Number(row.unassigned || 0)}</td>
    </tr>
  `).join("");
}

function updateExportLinks(params) {
  const query = params.toString();
  const suffix = query ? `?${query}` : "";
  exportGeoJsonEl.href = `/api/exports/defects.geojson${suffix}`;
  exportCsvEl.href = `/api/exports/defects.csv${suffix}`;

  const wmsParams = new URLSearchParams(params.toString());
  const bounds = map.getBounds();
  if (bounds.isValid()) {
    const bbox = [
      bounds.getWest(),
      bounds.getSouth(),
      bounds.getEast(),
      bounds.getNorth()
    ].map((value) => value.toFixed(6)).join(",");
    wmsParams.set("bbox", bbox);
  }
  wmsLikeUrlEl.value = `/api/integrations/wms-like?${wmsParams.toString()}`;
}

function updateDashboard(drones, defects, changes, viewData) {
  const activeDrones = drones.filter((drone) => drone.status === "active").length;
  const critical = defects.filter((defect) => defect.severity === "critical").length;
  const assigned = defects.filter((defect) => defect.assignment_status !== "unassigned").length;
  const avgConfidence = defects.length
    ? defects.reduce((sum, defect) => sum + Number(defect.confidence || 0), 0) / defects.length
    : 0;
  const allCounts = changes?.counts_all || changes?.counts || {};
  const newWorsened = Number(allCounts.new || 0) + Number(allCounts.worsened || 0);
  const teamCount = Array.isArray(viewData?.team_catalog) ? viewData.team_catalog.length : 0;

  activeDroneCountEl.textContent = String(activeDrones);
  defectCountEl.textContent = String(defects.length);
  criticalDefectCountEl.textContent = String(critical);
  newWorsenedCountEl.textContent = String(newWorsened);
  avgConfidenceEl.textContent = avgConfidence.toFixed(2);
  assignedCountEl.textContent = `${assigned}/${defects.length}`;
  teamCountEl.textContent = String(teamCount);
}

async function loadData() {
  if (loadInFlight) {
    reloadQueued = true;
    return;
  }
  loadInFlight = true;
  setStatus("Refreshing live defects data...");
  const filters = currentFilters();
  state.heatMode = filters.heatMode;
  const params = filtersToQuery(filters);
  updateExportLinks(params);

  try {
    const defectsPath = withQuery("/api/defects", params);
    const changesPath = withQuery("/api/defects/changes", params);
    const dronesPath = "/api/drones";
    const neighborhoodsPath = "/api/neighborhoods";
    const ownershipPath = withQuery("/api/views/boroughs-teams", params);
    const heatParams = new URLSearchParams(params.toString());
    heatParams.set("mode", state.heatMode);
    const heatPath = withQuery("/api/heatmap", heatParams);

    const [drones, defects, changes, neighborhoods, ownership, heatPoints] = await Promise.all([
      fetchJson(dronesPath),
      fetchJson(defectsPath),
      fetchJson(changesPath),
      fetchJson(neighborhoodsPath),
      fetchJson(ownershipPath),
      state.heatMode === "off" ? Promise.resolve([]) : fetchJson(heatPath)
    ]);

    updateOwnershipFilters(ownership);
    renderChangeSummary(changes);
    renderOwnershipTables(ownership);
    renderDrones(drones);
    renderDefects(defects);
    renderNeighborhoods(neighborhoods);
    renderHeatmap(heatPoints);
    updateDashboard(drones, defects, changes, ownership);
    applyLayerVisibility();

    const scanTime = changes?.current_scan_time || new Date().toISOString();
    setStatus(`Updated at ${new Date().toLocaleTimeString()} | Scan: ${scanTime}`);
  } catch (error) {
    setStatus("Data load failed. Make sure backend/app.py is running on port 5000.");
    console.error(error);
  } finally {
    loadInFlight = false;
    if (reloadQueued) {
      reloadQueued = false;
      loadData();
    }
  }
}

function rerenderDefectsOnly() {
  renderDefects(latestDefects);
  applyLayerVisibility();
}

toggleDronesBtn.addEventListener("click", () => {
  state.dronesVisible = !state.dronesVisible;
  toggleDronesBtn.textContent = state.dronesVisible ? "Hide Drones" : "Show Drones";
  applyLayerVisibility();
});

toggleDroneZonesBtn.addEventListener("click", () => {
  state.zonesVisible = !state.zonesVisible;
  toggleDroneZonesBtn.textContent = state.zonesVisible ? "Hide Drone Zones" : "Show Drone Zones";
  applyLayerVisibility();
});

toggleDefectsBtn.addEventListener("click", () => {
  state.defectsVisible = !state.defectsVisible;
  toggleDefectsBtn.textContent = state.defectsVisible ? "Hide Defects" : "Show Defects";
  applyLayerVisibility();
});

refreshDataBtn.addEventListener("click", loadData);
heatModeEl.addEventListener("change", loadData);
changeScopeEl.addEventListener("change", loadData);
boroughFilterEl.addEventListener("change", loadData);
teamFilterEl.addEventListener("change", loadData);
viewModeEl.addEventListener("change", rerenderDefectsOnly);
map.on("moveend", () => updateExportLinks(filtersToQuery(currentFilters())));

copyWmsUrlBtn.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(wmsLikeUrlEl.value);
    setStatus("Integration URL copied to clipboard.");
  } catch (error) {
    console.error(error);
    setStatus("Could not copy integration URL. You can still copy it manually.");
  }
});

if (mobileMapToggleBtn) {
  mobileMapToggleBtn.addEventListener("click", () => {
    mobileMapOnly = !mobileMapOnly;
    applyMobileMapOnlyMode();
  });
}

const onMobileLayoutChange = () => {
  if (!mobileLayoutMedia.matches) {
    mobileMapOnly = false;
  }
  applyMobileMapOnlyMode();
};

if (typeof mobileLayoutMedia.addEventListener === "function") {
  mobileLayoutMedia.addEventListener("change", onMobileLayoutChange);
} else if (typeof mobileLayoutMedia.addListener === "function") {
  mobileLayoutMedia.addListener(onMobileLayoutChange);
}

updateLayerModeText();
applyMobileMapOnlyMode();
window.requestAnimationFrame(animateDrones);
loadData();
setInterval(loadData, DATA_REFRESH_MS);
