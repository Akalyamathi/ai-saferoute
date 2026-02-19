from flask import Flask, render_template, jsonify, request
import requests
import json
import networkx as nx
import time
import math
import logging
import traceback

from functools import lru_cache

app = Flask(__name__)

# ---------------- LOGGING ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AI_SafeRoute")

# ---------------- CONFIG ----------------
DEFAULT_SPEED_KMPH = 30
GRAPH_CACHE_TTL = 300
MAX_SNAP_DISTANCE = 0.02  # approx ~20–30m demo tolerance

VALID_ROUTE_TYPES = {"shortest", "safest", "balanced"}

RISK_CONFIG = {
    "crime_weight": 0.6,
    "lighting_weight": 0.15,
    "crowd_weight": 0.15,
    "nonlinear_exponent": 1.3
}

# ---------------- RESPONSE FORMAT ----------------
def success(data):
    return jsonify({"success": True, "data": data})

def error(code, message):
    return jsonify({"success": False, "error": message}), code

# ---------------- DATASET ----------------
DATASET_TIMESTAMP = 0
SEGMENTS_BY_ID = {}
SEGMENT_DISTANCES = {}

def validate_dataset_schema(data):
    for s in data["segments"]:
        assert all(k in s for k in ["id", "start", "end", "crime", "lighting", "crowd"])

def load_dataset():
    global DATASET_TIMESTAMP
    try:
        with open("risk_data.json") as f:
            data = json.load(f)

        validate_dataset_schema(data)

        SEGMENTS_BY_ID.clear()
        SEGMENT_DISTANCES.clear()

        for s in data["segments"]:
            SEGMENTS_BY_ID[s["id"]] = s
            SEGMENT_DISTANCES[s["id"]] = math.dist(s["start"], s["end"])

        DATASET_TIMESTAMP = time.time()
        logger.info(f"Dataset loaded ({len(data['segments'])} segments)")

    except Exception:
        traceback.print_exc()

load_dataset()

@app.route("/reload_data")
def reload_data():
    try:
        load_dataset()
        calculate_risk.cache_clear()
        build_graph.cache_clear()
        return success({"message": "Dataset reloaded"})
    except:
        traceback.print_exc()
        return error(500, "Reload failed")

# ---------------- VALIDATION ----------------
def valid_coords(c):
    if not isinstance(c, (list, tuple)) or len(c) != 2:
        return False
    lat, lon = c
    return (
        isinstance(lat, (int, float))
        and isinstance(lon, (int, float))
        and -90 <= lat <= 90
        and -180 <= lon <= 180
    )

def valid_hour(h):
    return isinstance(h, int) and 0 <= h <= 23

def valid_alpha(a):
    return isinstance(a, (int, float)) and 0 <= a <= 1

# ---------------- RISK ----------------
def nonlinear(v):
    return v ** RISK_CONFIG["nonlinear_exponent"]

def time_multiplier(hour):
    if hour < 20:
        return 1.0
    return round(1 + 0.2 * math.tanh((hour - 20) / 2), 2)

@lru_cache(maxsize=2000)
def calculate_risk(segment_id, hour):
    s = SEGMENTS_BY_ID[segment_id]

    crime = nonlinear(s["crime"])
    lighting_deficit = nonlinear(1 - s["lighting"])
    crowd_scarcity = nonlinear(1 - s["crowd"])

    base = (
        RISK_CONFIG["crime_weight"] * crime
        + RISK_CONFIG["lighting_weight"] * lighting_deficit
        + RISK_CONFIG["crowd_weight"] * crowd_scarcity
    )

    return round(base * time_multiplier(hour), 2)

# ---------------- ETA ----------------
def compute_eta(distance_km, speed):
    return round((distance_km / speed) * 60, 2)

# ---------------- CACHE TTL CLEAR ----------------
LAST_CACHE_CLEAR = time.time()

def auto_clear_cache():
    global LAST_CACHE_CLEAR
    if time.time() - LAST_CACHE_CLEAR > GRAPH_CACHE_TTL:
        calculate_risk.cache_clear()
        build_graph.cache_clear()
        LAST_CACHE_CLEAR = time.time()
        logger.info("Caches auto-cleared")

# ---------------- GRAPH ----------------
@lru_cache(maxsize=200)
def build_graph(hour, alpha, dataset_ts):
    G = nx.DiGraph()

    for sid, seg in SEGMENTS_BY_ID.items():
        start = tuple(seg["start"])
        end = tuple(seg["end"])

        risk = calculate_risk(sid, hour)
        eta = compute_eta(SEGMENT_DISTANCES[sid], DEFAULT_SPEED_KMPH)

        G.add_edge(
            start,
            end,
            eta=eta,
            risk=risk,
            weight=alpha * eta + (1 - alpha) * risk,
            geometry=[seg["start"], seg["end"]],
            segment_id=sid,
        )

    return G

# ---------------- NEAREST NODE ----------------
def nearest_node(G, point):
    closest = min(G.nodes, key=lambda n: math.dist(n, point))

    if math.dist(closest, point) > MAX_SNAP_DISTANCE:
        logger.warning("Snapping far point for demo")
    return closest



# ---------------- ROUTING ----------------
def compute_route(origin, destination, hour, alpha):
    auto_clear_cache()

    G = build_graph(hour, alpha, DATASET_TIMESTAMP)

    origin_node = nearest_node(G, origin)
    dest_node = nearest_node(G, destination)

    if not origin_node:
        return None, "Origin too far from known roads"

    if not dest_node:
        return None, "Destination too far from known roads"

    try:
        path = nx.astar_path(
            G,
            origin_node,
            dest_node,
            heuristic=lambda a, b: math.dist(a, b),
            weight="weight",
        )
    except nx.NetworkXNoPath:
        return None, "No route available"
    except Exception:
        traceback.print_exc()
        return None, "Routing failure"
    if len(path) == 1:
     return {
        "path": path,
        "eta": 0,
        "risk": 0,
        "confidence": 1,
        "warning": "Origin and destination are extremely close"
    }, None


    eta, risk, geometry = 0, 0, []

    for i in range(len(path) - 1):
        edge = G[path[i]][path[i + 1]]
        eta += edge["eta"]
        risk += edge["risk"]
        geometry.append(edge["geometry"])

    segments_count = max(len(path) - 1, 1)

    normalized_risk = round(risk / segments_count, 2)
    confidence = round(1 / (1 + normalized_risk), 2)


    return {
        "path": path,
        "eta": round(eta, 2),
        "risk": normalized_risk,
        "confidence": confidence,
        "geometry": geometry,
    }, None

# ---------------- RATE LIMIT ----------------
REQUESTS = {}

def rate_limit(ip, limit=30, window=60):
    now = time.time()
    REQUESTS.setdefault(ip, [])
    REQUESTS[ip] = [t for t in REQUESTS[ip] if now - t < window]

    if len(REQUESTS[ip]) >= limit:
        return False

    REQUESTS[ip].append(now)
    return True

# ---------------- CONFIG API ----------------
@app.route("/config_risk", methods=["POST"])
def config_risk():
    try:
        data = request.get_json()
        RISK_CONFIG.update(data)
        calculate_risk.cache_clear()
        return success({"message": "Risk config updated"})
    except:
        return error(400, "Invalid config payload")

# ---------------- API ----------------
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/get_routes", methods=["POST"])
def get_routes():

    ip = request.remote_addr
    if not rate_limit(ip):
        return error(429, "Too many requests")

    try:
        data = request.get_json()
    except:
        return error(400, "Malformed JSON")

    origin = data.get("origin")
    destination = data.get("destination")
    hour = data.get("hour", 22)
    alpha = data.get("alpha", 0.5)
    route_type = data.get("type", "balanced")
    speed = data.get("speed", DEFAULT_SPEED_KMPH)

    if route_type not in VALID_ROUTE_TYPES:
        return error(400, f"Invalid type {VALID_ROUTE_TYPES}")

    if not valid_coords(origin):
        return error(400, "Invalid origin")

    if not valid_coords(destination):
        return error(400, "Invalid destination")

    if origin == destination:
        return error(400, "Origin and destination cannot match")

    if not valid_hour(hour):
        return error(400, "Invalid hour")

    if not valid_alpha(alpha):
        return error(400, "Invalid alpha")

    if route_type == "shortest":
        alpha = 1.0
    elif route_type == "safest":
        alpha = 0.0

    route, err = compute_route(tuple(origin), tuple(destination), hour, alpha)

    if err:
        return error(404, err)

    return success(
        {
            "route": route,
            "strategy": route_type,
            "time_multiplier": time_multiplier(hour),
        }
    )

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)


