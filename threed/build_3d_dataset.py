"""
Construit le jeu de test du monitoring 3D : pour chaque zone (nom, lon, lat), recupere l'emprise du batiment
BD TOPO (IGN) sous le point, et la tuile zoom 20 correspondante.

Le fichier produit est versionne dans le depot : le workflow le lit apres le checkout.

  python threed/build_3d_dataset.py      # ecrit threed/instatus-3d-datatest.json, a commiter
"""
import json

import requests

from threed.instatus_3d_monitoring import DATASET_PATH, lonlat_to_tile

BDTOPO_WFS_URL = "https://data.geopf.fr/wfs/ows"

# batiments de taille moyenne, couverts par le LiDAR HD
ZONES = [
    {"name": "ISERE (Pont-de-Claix)", "lon": 5.6979941, "lat": 45.1232701},
    {"name": "HAUTE-GARONNE (Toulouse)", "lon": 1.4436, "lat": 43.6045},
    {"name": "GIRONDE (Bordeaux)", "lon": -0.5775, "lat": 44.8380},
    {"name": "RHONE (Lyon)", "lon": 4.8360, "lat": 45.7672},
    {"name": "TARN-ET-GARONNE (Montauban)", "lon": 1.3556, "lat": 44.0159},
]


def point_in_ring(lon, lat, ring):
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def building_footprint(lon, lat, radius_deg=0.0003):
    """Anneau exterieur [lon, lat] ferme du batiment BD TOPO sous le point (ou le plus proche dans ~30 m)."""
    response = requests.get(BDTOPO_WFS_URL, params={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": "BDTOPO_V3:batiment",
        "outputFormat": "application/json", "srsName": "EPSG:4326",
        # BBOX en EPSG:4326 : ordre lat, lon
        "bbox": f"{lat - radius_deg},{lon - radius_deg},{lat + radius_deg},{lon + radius_deg},urn:ogc:def:crs:EPSG::4326",
    }, timeout=60)
    response.raise_for_status()

    rings = []
    for feature in response.json().get("features", []):
        geometry = feature["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        rings += [[p[:2] for p in polygon[0]] for polygon in polygons]
    if not rings:
        raise RuntimeError(f"aucun batiment BD TOPO autour de {lon}, {lat}")

    def distance(ring):
        cx, cy = sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)
        return 0 if point_in_ring(lon, lat, ring) else (cx - lon) ** 2 + (cy - lat) ** 2

    ring = min(rings, key=distance)
    return ring if ring[0] == ring[-1] else [*ring, ring[0]]


def main():
    dataset = []
    for zone in ZONES:
        x, y = lonlat_to_tile(zone["lon"], zone["lat"], 20)
        ring = building_footprint(zone["lon"], zone["lat"])
        dataset.append({**zone, "tile": {"x": x, "y": y, "z": 20}, "delimitation": ring})
        print(f"{zone['name']}: tuile {x},{y} emprise {len(ring)} points")

    with open(DATASET_PATH, "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2, ensure_ascii=False)
    print(f"Dataset written: {DATASET_PATH} (a commiter pour que le workflow l'utilise)")


if __name__ == "__main__":
    main()
