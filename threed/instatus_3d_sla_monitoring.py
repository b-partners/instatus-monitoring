"""
Monitoring du SLA de l'API 3D sur Instatus.

Meme generation 3D que threed/instatus_3d_monitoring.py, sur les memes zones (threed/instatus-3d-datatest.json),
mais seules les erreurs de l'API comptent : le SLA est independant de la disponibilite du LiDAR.
Pour chaque 3D en echec, le statut brut est relu via GET {THREED_API_URL}/city-jsons/{id}
(GET /3d/{id} renvoie health=FAILED aussi bien pour FAILED que pour UNAVAILABLE) :
  FAILED      -> erreur de l'API, comptee dans le SLA
  UNAVAILABLE -> pas de LiDAR sur la zone, ignoree
  autre (timeout, lancement refuse, CityJSON vide...) -> ignoree, couverte par le monitoring de l'API 3D

Un seul composant Instatus (THREED_SLA_COMPONENT_ID), sur la meme page que l'API 3D (INSTATUS_3D_PAGE_ID) :
  aucune zone FAILED     -> OPERATIONAL    (resout l'incident ouvert)
  au moins une FAILED    -> PARTIALOUTAGE  (cree un incident)
  toutes les zones FAILED -> MAJOROUTAGE

Usage :
  python threed/instatus_3d_sla_monitoring.py
  python threed/instatus_3d_sla_monitoring.py --dry-run   # sans Instatus
"""
import argparse
import json
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests

from threed.instatus_3d_monitoring import (
    DATASET_PATH, OPERATIONAL, OUTAGE, THREED_API_KEY, THREED_API_URL, TIMEOUT_S, Progress, aggregate_status,
    build_session, fetch_active_incidents, fetch_components_statuses, format_timeline, log, run_3d, update_instatus,
)

NAME = "3D SLA"
FAILED, UNAVAILABLE = "FAILED", "UNAVAILABLE"


def fetch_request_status(session, city_json_id):
    """Statut brut de la demande (FINISHED, FAILED, PROCESSING, UNAVAILABLE), None si illisible."""
    try:
        response = session.get(f"{THREED_API_URL}/city-jsons/{city_json_id}", timeout=60)
        return response.json().get("status") if response.status_code == 200 else None
    except (requests.exceptions.RequestException, ValueError) as e:
        print(f"GET /city-jsons/{city_json_id} failed: {type(e).__name__} {e}")
        return None


def check_sla(zone, session, progress=None):
    """Retourne (statut Instatus | None si la zone n'est pas comptee, message, duree en s, historique des etats)."""
    history = []
    city_json_id = f"instatus-sla-{uuid.uuid4()}"
    status, message, duration = run_3d(zone, session, history, city_json_id)
    if status == OUTAGE:
        request_status = fetch_request_status(session, city_json_id)
        log(zone["name"], f"statut brut de la demande : {request_status}")
        if request_status == FAILED:
            status = OUTAGE
        elif request_status == UNAVAILABLE:
            status, message = None, f"LiDAR indisponible, non compte dans le SLA : {message}"
        else:
            status, message = None, f"non compte dans le SLA (statut {request_status}) : {message}"
    elif status is not None:
        status = OPERATIONAL  # une 3D lente reste une 3D reussie pour le SLA
    if progress:
        progress.finish(zone["name"], status, message)
    return status, message, duration, history


def write_step_summary(zones, results, status, message):
    """Tableau des zones dans le resume du job GitHub Actions (sans effet en local)."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = [f"## {NAME} : {status or 'UNKNOWN (no zone counted)'}", "", message, "",
             "| Zone | Statut | Duree | Etats successifs | Detail |", "|---|---|---|---|---|"]
    for zone, (s, detail, duration, history) in zip(zones, results):
        cells = [zone["name"], s or "NOT COUNTED", f"{duration:.0f}s", format_timeline(history), detail]
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def instatus_3d_sla_monitoring(dataset_path, dry_run=False, workers=4):
    with open(dataset_path, encoding="utf-8") as f:
        zones = json.load(f)
    if not zones:
        print(f"Empty dataset {dataset_path}, nothing to monitor")
        return True
    if not THREED_API_KEY:
        sys.exit("THREED_API_KEY is not set")

    api = build_session({"x-api-key": THREED_API_KEY, "Content-Type": "application/json"})
    print(f"Monitoring {NAME} {THREED_API_URL} on {len(zones)} zone(s) from {dataset_path}, {workers} in parallel, "
          f"timeout {TIMEOUT_S}s")
    progress = Progress(len(zones))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda z: check_sla(z, api, progress), zones))

    print("==================== RESULTS")
    for zone, (status, message, duration, history) in zip(zones, results):
        print(f"[{status or 'NOT COUNTED'}] {zone['name']} ({duration:.0f}s) : {message}")
        print(f"    {format_timeline(history)}")

    status = aggregate_status(results)
    problems = [f"{zone['name']} : {message}" for zone, (s, message, *_) in zip(zones, results) if s == OUTAGE]
    counted = sum(1 for s, *_ in results if s)
    message = f"{counted - len(problems)}/{counted} zone(s) OK" + (" | " + " | ".join(problems) if problems else "")
    print(f"{NAME} status={status or 'UNKNOWN (no zone counted)'} : {message}")
    write_step_summary(zones, results, status, message)

    if dry_run:
        print("--dry-run : Instatus is not updated")
        return status in (OPERATIONAL, None)
    if status is None:
        print("No zone counted in the SLA : Instatus is not updated")
        return True

    page_id = os.environ["INSTATUS_3D_PAGE_ID"]
    component_id = os.environ["THREED_SLA_COMPONENT_ID"]
    instatus = build_session({"Authorization": f"Bearer {os.environ['INSTATUS_API_KEY']}",
                              "Content-Type": "application/json"})
    components = fetch_components_statuses(instatus, page_id)
    if component_id not in components:
        sys.exit(f"Component {component_id} not found on Instatus page {page_id}")
    incident_id = fetch_active_incidents(instatus, page_id).get(component_id)
    print(f"Component {component_id} status={components[component_id]}, active incident={incident_id}")
    update_instatus(instatus, page_id, component_id, status, message, components[component_id], incident_id, NAME)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default=DATASET_PATH, help=f"zones a tester (defaut: {DATASET_PATH}, celles du monitoring 3D)")
    parser.add_argument("--dry-run", action="store_true", help="n'appelle pas Instatus, affiche seulement les resultats")
    parser.add_argument("--workers", type=int, default=4, help="zones testees en parallele (defaut: 4)")
    args = parser.parse_args()
    sys.exit(0 if instatus_3d_sla_monitoring(args.dataset, dry_run=args.dry_run, workers=args.workers) else 1)


if __name__ == "__main__":
    main()
