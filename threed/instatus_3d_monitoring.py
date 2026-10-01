"""
Monitoring de l'API 3D (generation de maquette par emprise de toit) sur Instatus.

Pour chaque zone du jeu de test (emprise BD TOPO + tuile zoom 20, cf. build_3d_dataset.py) :
  1. verifie que la texture (orthophoto IGN, 3x3 tuiles) est disponible : sinon la zone est ignoree,
     une panne IGN n'est pas une panne de l'API 3D
  2. PUT {THREED_API_URL}/city-jsons/{id}/process
  3. GET {THREED_API_URL}/3d/{id} toutes les POLL_DELAY_S secondes jusqu'a FINISHED
  4. telecharge le premier CityJSON produit et verifie qu'il contient des CityObjects

Un seul composant Instatus pour l'API 3D (THREED_COMPONENT_ID), statut calcule sur l'ensemble des zones :
  toutes OK                   -> OPERATIONAL          (resout l'incident ouvert)
  toutes OK, une plus de SLOW_S -> DEGRADEDPERFORMANCE
  au moins une KO             -> PARTIALOUTAGE        (refus, echec, timeout, CityJSON vide)
  toutes KO                   -> MAJOROUTAGE

Les zones sont lues dans threed/instatus-3d-datatest.json, versionne dans le depot (checkout du workflow).

Usage :
  python threed/instatus_3d_monitoring.py
  python threed/instatus_3d_monitoring.py --dry-run   # sans Instatus
"""
import argparse
import json
import math
import os
import sys
import threading
import time
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

THREED_API_URL = os.environ.get("THREED_API_URL", "https://api.birdia.fr").rstrip("/")
THREED_API_KEY = os.environ.get("THREED_API_KEY")
INSTATUS_BASE_URL_V1 = "https://api.instatus.com/v1"
INSTATUS_BASE_URL_V2 = "https://api.instatus.com/v2"

IGN_WMS_URL = "https://data.geopf.fr/wms-r"
IGN_ORTHO_LAYER = "HR.ORTHOIMAGERY.ORTHOPHOTOS"
EARTH_CIRCUMFERENCE = 2 * math.pi * 6378137
TILE_SIZE_PX = 1024

POLL_DELAY_S = 10
TIMEOUT_S = int(os.environ.get("THREED_TIMEOUT_S", 600))
SLOW_S = int(os.environ.get("THREED_SLOW_S", 300))

OPERATIONAL, DEGRADED, OUTAGE, MAJOR_OUTAGE = "OPERATIONAL", "DEGRADEDPERFORMANCE", "PARTIALOUTAGE", "MAJOROUTAGE"
DATASET_PATH = "threed/instatus-3d-datatest.json"


def log(zone, message):
    print(f"{datetime.now(timezone.utc):%H:%M:%S} [{zone}] {message}", flush=True)


def describe_state(data):
    """'PROCESSING/UNKNOWN [GEOMETRY_CONSTRUCTION] (message)' a partir de la reponse de GET /3d/{id}."""
    state = data.get("status") or {}
    label = f"{state.get('progression') or '?'}/{state.get('health') or '?'}"
    if data.get("step"):
        label += f" [{data['step']}]"
    return f"{label} ({state['message']})" if state.get("message") else label


def format_timeline(history):
    """'PENDING/UNKNOWN @10s -> PROCESSING/UNKNOWN @20s -> FINISHED/SUCCEEDED @85s'"""
    return " -> ".join(f"{label} @{elapsed:.0f}s" for label, elapsed in history) or "-"


class Progress:
    """Compteur partage entre les threads : nombre de zones terminees sur le total."""

    def __init__(self, total):
        self.total, self.done, self.lock = total, 0, threading.Lock()

    def finish(self, zone, status, message):
        with self.lock:
            self.done += 1
            log(zone, f"[{self.done}/{self.total}] {status or 'SKIPPED'} : {message}")


def build_session(headers=None):
    session = requests.Session()
    session.headers.update(headers or {})
    retries = Retry(total=3, backoff_factor=2, status_forcelist=[502, 503, 504], allowed_methods=["GET"])
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# --------------------------------------------------------------------------- tuiles / texture

def lonlat_to_tile(lon, lat, zoom):
    n = 2 ** zoom
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return int(x), int(y)


def texture_url(tile_x, tile_y, zoom, tiles=3):
    """Orthophoto IGN couvrant `tiles` x `tiles` tuiles a partir de la tuile (tile_x, tile_y) (coin nord-ouest)."""
    size = EARTH_CIRCUMFERENCE / 2 ** zoom
    minx = tile_x * size - EARTH_CIRCUMFERENCE / 2
    maxy = EARTH_CIRCUMFERENCE / 2 - tile_y * size
    bbox = (minx, maxy - tiles * size, minx + tiles * size, maxy)
    return f"{IGN_WMS_URL}?" + urllib.parse.urlencode({
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": IGN_ORTHO_LAYER, "STYLES": "",
        "CRS": "EPSG:3857", "BBOX": ",".join(repr(v) for v in bbox),
        "WIDTH": tiles * TILE_SIZE_PX, "HEIGHT": tiles * TILE_SIZE_PX, "FORMAT": "image/jpeg",
    })


def is_texture_available(url):
    try:
        response = requests.get(url, timeout=60)
        return response.status_code == 200 and response.content[:3] == b"\xff\xd8\xff"
    except requests.exceptions.RequestException as e:
        print(f"Texture request failed: {e}")
        return False


# --------------------------------------------------------------------------- API 3D

def check_3d(zone, session, progress=None):
    """Retourne (statut Instatus | None si la zone est ignoree, message, duree en s, historique des etats)."""
    history = []
    status, message, duration = run_3d(zone, session, history)
    if progress:
        progress.finish(zone["name"], status, message)
    return status, message, duration, history


def run_3d(zone, session, history, city_json_id=None):
    """Lance la 3D d'une zone ; `history` recoit chaque changement d'etat (libelle, secondes depuis le lancement)."""
    name = zone["name"]
    tile_x, tile_y, zoom = zone["tile"]["x"], zone["tile"]["y"], zone["tile"]["z"]
    image_uri = texture_url(tile_x - 1, tile_y - 1, zoom)

    if not is_texture_available(image_uri):
        log(name, "[SKIP] texture IGN indisponible : la zone n'est pas testee")
        return None, "texture IGN indisponible", 0

    city_json_id = city_json_id or f"instatus-{str(uuid.uuid4())}"
    body = {
        "id": city_json_id,
        "delimitationObjectType": "BUILDING_ROOF",
        "delimitations": [{
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [zone["delimitation"]]},
        }],
        "threeDTextureInfo": {
            "tileX": tile_x - 1,
            "tileY": tile_y - 1,
            "tileImageSizePx": TILE_SIZE_PX,
            "imageWidth": 3 * TILE_SIZE_PX,
            "imageHeight": 3 * TILE_SIZE_PX,
            "zoom": zoom,
            "imageUri": image_uri,
        },
    }

    start = time.time()
    log(name, f"PUT /city-jsons/{city_json_id}/process")
    try:
        response = session.put(f"{THREED_API_URL}/city-jsons/{city_json_id}/process", json=body, timeout=120)
    except requests.exceptions.RequestException as e:
        return OUTAGE, f"lancement de la 3D impossible : {type(e).__name__} {e}", time.time() - start
    log(name, f"-> HTTP {response.status_code} en {time.time() - start:.1f}s")
    if response.status_code >= 300:
        return OUTAGE, f"lancement de la 3D refuse (HTTP {response.status_code}) : {response.text[:200]}", time.time() - start

    data = {}
    while time.time() - start < TIMEOUT_S:
        time.sleep(POLL_DELAY_S)
        elapsed = time.time() - start
        try:
            response = session.get(f"{THREED_API_URL}/3d/{city_json_id}", timeout=60)
        except requests.exceptions.RequestException as e:
            log(name, f"GET /3d/{city_json_id} en echec : {type(e).__name__} ({elapsed:.0f}s)")
            continue
        data = response.json() if response.status_code == 200 else {}
        state = data.get("status") or {}
        label = describe_state(data) if state else f"HTTP {response.status_code}"
        if not history or history[-1][0] != label:
            previous = history[-1][0] if history else "PUT"
            history.append((label, elapsed))
            log(name, f"[STATUS] {previous} -> {label} ({elapsed:.0f}s / {TIMEOUT_S}s)")
        else:
            log(name, f"[STATUS] {label} depuis {elapsed - history[-1][1]:.0f}s ({elapsed:.0f}s / {TIMEOUT_S}s)")
        if state.get("progression") not in (None, "PENDING", "PROCESSING"):
            break
    else:
        last = history[-1][0] if history else "aucune reponse"
        return OUTAGE, f"3D non terminee apres {TIMEOUT_S}s, dernier etat {last} (id {city_json_id})", time.time() - start

    duration = time.time() - start
    state = data.get("status") or {}
    if state.get("health") != "SUCCEEDED":
        return OUTAGE, (f"3D en echec (progression={state.get('progression')}, health={state.get('health')}, step={data.get('step')}, "
                        f"message={state.get('message')}, id {city_json_id})"), duration

    files = data.get("cityJsonFileUrls") or []
    if not files:
        return OUTAGE, f"3D terminee sans fichier CityJSON (id {city_json_id})", duration
    try:
        city_json = requests.get(files[0]["url"], timeout=60).json()
    except (requests.exceptions.RequestException, ValueError) as e:
        return OUTAGE, f"CityJSON illisible : {type(e).__name__} (id {city_json_id})", duration
    if city_json.get("type") != "CityJSON" or not city_json.get("CityObjects"):
        return OUTAGE, f"CityJSON vide ou invalide (id {city_json_id})", duration

    if duration > SLOW_S:
        return DEGRADED, f"3D generee en {duration:.0f}s (> {SLOW_S}s)", duration
    return OPERATIONAL, f"3D generee en {duration:.0f}s ({len(city_json['CityObjects'])} CityObjects)", duration


# --------------------------------------------------------------------------- Instatus

def fetch_components_statuses(session, page_id):
    response = session.get(f"{INSTATUS_BASE_URL_V2}/{page_id}/components", params={"page": 1, "per_page": 100})
    response.raise_for_status()
    return {c["id"]: c["status"] for c in response.json()}


def fetch_active_incidents(session, page_id):
    """{ component_id: incident_id } des incidents non resolus."""
    active = {}
    page = 1
    while True:
        response = session.get(f"{INSTATUS_BASE_URL_V1}/{page_id}/incidents",
                               params={"page": page, "per_page": 100, "!status": "RESOLVED"})
        response.raise_for_status()
        incidents = response.json()
        for incident in incidents:
            for component in incident.get("components", []):
                active[component["id"]] = incident["id"]
        if len(incidents) < 100:
            return active
        page += 1


def aggregate_status(results):
    """Statut du composant 3D a partir des zones testees (les zones ignorees ne comptent pas)."""
    tested = [status for status, *_ in results if status]
    failed = tested.count(OUTAGE)
    if not tested:
        return None
    if failed == len(tested):
        return MAJOR_OUTAGE
    if failed:
        return OUTAGE
    if DEGRADED in tested:
        return DEGRADED
    return OPERATIONAL


def update_instatus(session, page_id, component_id, status, message, current_status, incident_id, name="3D API"):
    statuses = [{"id": component_id, "status": status}]

    # ---------------------------------------------------------------- OK -> resolution de l'incident ouvert
    if status == OPERATIONAL:
        if not incident_id:
            print("No action required, monitoring OK, component OPERATIONAL.")
            return None
        print(f"[RESOLVE] incident {incident_id}")
        session.post(f"{INSTATUS_BASE_URL_V1}/{page_id}/incidents/{incident_id}/incident-updates", json={
            "message": f"{name} available : {message}",
            "started": datetime.now(timezone.utc).isoformat(),
            "components": [component_id], "status": "RESOLVED", "notify": True, "statuses": statuses,
        }).raise_for_status()
        return None

    # ---------------------------------------------------------------- KO / lent, sans incident -> creation
    if not incident_id:
        print(f"[CREATE] incident {status}")
        response = session.post(f"{INSTATUS_BASE_URL_V1}/{page_id}/incidents", json={
            "name": f"{name} {'slow' if status == DEGRADED else 'unavailable'}",
            "message": message,
            "components": [component_id], "status": "INVESTIGATING", "notify": True, "statuses": statuses,
        })
        response.raise_for_status()
        return response.json()["id"]

    # ---------------------------------------------------------------- incident deja ouvert
    if current_status == status:
        print(f"[SKIP] already {status}, incident {incident_id} kept open")
        return incident_id
    print(f"[UPDATE] incident {incident_id} : {current_status} -> {status}")
    session.post(f"{INSTATUS_BASE_URL_V1}/{page_id}/incidents/{incident_id}/incident-updates", json={
        "message": message,
        "started": datetime.now(timezone.utc).isoformat(),
        "components": [component_id], "status": "IDENTIFIED", "notify": True, "statuses": statuses,
    }).raise_for_status()
    return incident_id


# --------------------------------------------------------------------------- orchestration

def write_step_summary(zones, results, status, message):
    """Tableau des zones dans le resume du job GitHub Actions (sans effet en local)."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = [f"## 3D API : {status or 'UNKNOWN (no zone tested)'}", "", message, "",
             "| Zone | Statut | Duree | Etats successifs | Detail |", "|---|---|---|---|---|"]
    for zone, (s, detail, duration, history) in zip(zones, results):
        cells = [zone["name"], s or "SKIPPED", f"{duration:.0f}s", format_timeline(history), detail]
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def instatus_3d_monitoring(dataset_path, dry_run=False, workers=4):
    with open(dataset_path, encoding="utf-8") as f:
        zones = json.load(f)
    if not zones:
        print(f"Empty dataset {dataset_path}, nothing to monitor")
        return True
    if not THREED_API_KEY:
        sys.exit("THREED_API_KEY is not set")

    api = build_session({"x-api-key": THREED_API_KEY, "Content-Type": "application/json"})
    print(f"Monitoring 3D API {THREED_API_URL} on {len(zones)} zone(s) from {dataset_path}, {workers} in parallel, "
          f"timeout {TIMEOUT_S}s, slow above {SLOW_S}s")
    progress = Progress(len(zones))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda z: check_3d(z, api, progress), zones))

    print("==================== RESULTS")
    for zone, (status, message, duration, history) in zip(zones, results):
        print(f"[{status or 'SKIPPED'}] {zone['name']} ({duration:.0f}s) : {message}")
        print(f"    {format_timeline(history)}")

    status = aggregate_status(results)
    problems = [f"{zone['name']} : {message}" for zone, (s, message, *_) in zip(zones, results) if s and s != OPERATIONAL]
    tested = sum(1 for s, *_ in results if s)
    message = (f"{tested - len(problems)}/{tested} zone(s) OK" + (" | " + " | ".join(problems) if problems else ""))
    print(f"3D API status={status or 'UNKNOWN (no zone tested)'} : {message}")
    write_step_summary(zones, results, status, message)

    if dry_run:
        print("--dry-run : Instatus is not updated")
        return status in (OPERATIONAL, None)
    if status is None:
        print("No zone tested (texture unavailable everywhere) : Instatus is not updated")
        return True

    page_id = os.environ["INSTATUS_3D_PAGE_ID"]
    component_id = os.environ["THREED_COMPONENT_ID"]
    instatus = build_session({"Authorization": f"Bearer {os.environ['INSTATUS_API_KEY']}",
                              "Content-Type": "application/json"})
    components = fetch_components_statuses(instatus, page_id)
    if component_id not in components:
        sys.exit(f"Component {component_id} not found on Instatus page {page_id}")
    incident_id = fetch_active_incidents(instatus, page_id).get(component_id)
    print(f"Component {component_id} status={components[component_id]}, active incident={incident_id}")
    update_instatus(instatus, page_id, component_id, status, message, components[component_id], incident_id)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default=DATASET_PATH, help=f"zones a tester (defaut: {DATASET_PATH}, versionne dans le depot)")
    parser.add_argument("--dry-run", action="store_true", help="n'appelle pas Instatus, affiche seulement les resultats")
    parser.add_argument("--workers", type=int, default=4, help="zones testees en parallele (defaut: 4)")
    args = parser.parse_args()
    sys.exit(0 if instatus_3d_monitoring(args.dataset, dry_run=args.dry_run, workers=args.workers) else 1)


if __name__ == "__main__":
    main()
