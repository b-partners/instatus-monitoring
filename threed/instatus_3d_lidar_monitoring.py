"""
Monitoring des LiDAR utilises par l'API 3D sur Instatus.

Meme logique que lidar/instatus_lidar_hd_monitoring.py, mais uniquement sur les zones testees par le
monitoring 3D (threed/instatus-3d-datatest.json) : pour la tuile zoom 20 de chaque zone, on cherche l'URL
du LiDAR HD (STAC IGN, puis API de scraping, puis WFS IGN) et on verifie qu'elle est telechargeable
(premier Mo). Une source qui renvoie une URL non telechargeable passe aussi a la source suivante.

Un seul composant Instatus (LIDAR_3D_COMPONENT_ID), sur la meme page que l'API 3D (INSTATUS_3D_PAGE_ID) :
  toutes OK      -> OPERATIONAL    (resout l'incident ouvert)
  au moins une KO -> PARTIALOUTAGE
  toutes KO      -> MAJOROUTAGE

Usage :
  python threed/instatus_3d_lidar_monitoring.py
  python threed/instatus_3d_lidar_monitoring.py --dry-run   # sans Instatus
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lidar"))

from lidar.instatus_lidar_hd_monitoring import download_first_mb, monitor_lidar  # noqa: E402
from threed.instatus_3d_monitoring import (  # noqa: E402
    DATASET_PATH, OPERATIONAL, OUTAGE, aggregate_status, build_session, fetch_active_incidents,
    fetch_components_statuses, log, update_instatus,
)

NAME = "3D LiDAR"


def check_lidar(zone):
    """Retourne (statut Instatus, message, url du LiDAR)."""
    x, y, z = zone["tile"]["x"], zone["tile"]["y"], zone["tile"]["z"]
    url, is_downloadable = monitor_lidar(x, y, z, download_first_mb)
    if is_downloadable:
        return OPERATIONAL, f"LiDAR telechargeable ({url})", url
    if url:
        return OUTAGE, f"LiDAR non telechargeable ({url})", url
    return OUTAGE, f"aucun LiDAR trouve sur la tuile {x},{y},{z}", None


def write_step_summary(zones, results, status, message):
    """Tableau des zones dans le resume du job GitHub Actions (sans effet en local)."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = [f"## {NAME} : {status}", "", message, "", "| Zone | Statut | Detail |", "|---|---|---|"]
    for zone, (s, detail, _) in zip(zones, results):
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in [zone["name"], s, detail]) + " |")
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def instatus_3d_lidar_monitoring(dataset_path, dry_run=False):
    with open(dataset_path, encoding="utf-8") as f:
        zones = json.load(f)
    if not zones:
        print(f"Empty dataset {dataset_path}, nothing to monitor")
        return True

    print(f"Monitoring {NAME} on {len(zones)} zone(s) from {dataset_path}")
    results = []
    for i, zone in enumerate(zones, 1):
        print(f"=============================================== \n[{i}/{len(zones)}] {zone['name']}")
        results.append(check_lidar(zone))
        log(zone["name"], f"[{i}/{len(zones)}] {results[-1][0]} : {results[-1][1]}")

    print("==================== RESULTS")
    for zone, (status, message, _) in zip(zones, results):
        print(f"[{status}] {zone['name']} : {message}")

    status = aggregate_status(results)
    problems = [f"{zone['name']} : {message}" for zone, (s, message, _) in zip(zones, results) if s != OPERATIONAL]
    message = f"{len(zones) - len(problems)}/{len(zones)} zone(s) OK" + (" | " + " | ".join(problems) if problems else "")
    print(f"{NAME} status={status} : {message}")
    write_step_summary(zones, results, status, message)

    if dry_run:
        print("--dry-run : Instatus is not updated")
        return status == OPERATIONAL

    page_id = os.environ["INSTATUS_3D_PAGE_ID"]
    component_id = os.environ["LIDAR_3D_COMPONENT_ID"]
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
    args = parser.parse_args()
    sys.exit(0 if instatus_3d_lidar_monitoring(args.dataset, dry_run=args.dry_run) else 1)


if __name__ == "__main__":
    main()
