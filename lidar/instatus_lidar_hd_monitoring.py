from datetime import datetime, timezone
import json
import os
import sys


import requests
import mercantile
from pyproj import Transformer

from s3_conf import download_fileconf_from_s3, upload_config


LIDAR_BASE_URL = "https://api.stac.teledetection.fr/collections/lidarhd/items"
LIDAR_FALLBACK_BASE_URL = "https://data.geopf.fr/wfs/ows"

INSTATUS_API_KEY = os.environ["INSTATUS_API_KEY"]
INSTATUS_PAGE_ID = os.environ["INSTATUS_PAGE_ID"]

def monitor_lidar(x,y,z):
    print("Retrieve lidar download url on principal URL ...")
    tile = [x,y,z]
    bbox = mercantile.bounds(*tile)
    minx, miny = bbox[0], bbox[1]
    maxx, maxy = bbox[2], bbox[3]

    params = {"bbox": f"{minx},{miny},{maxx},{maxy}"}

    response = requests.get(LIDAR_BASE_URL, params=params)
    data = response.json()
    if "features" in data and len(data["features"]) > 0:
        href = data["features"][0]["assets"]["data"]["href"]
        print(f"LIDAR-IGN={href}")
        return href
    else:
        print("Lidar not found on principal url, process lidar retrieval on fallback")
        url = retrieve_ign_lidar_from(x,y,z)
        if url is not None:
            return url
        return None


def retrieve_ign_lidar_from(x, y, z):
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)
    tile = [x, y, z]

    bbox = mercantile.bounds(*tile)
    minlon, minlat = bbox.west, bbox.south
    maxlon, maxlat = bbox.east, bbox.north

    # Conversion to Lambert 93
    minx, miny = transformer.transform(minlon, minlat)
    maxx, maxy = transformer.transform(maxlon, maxlat)

    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": "IGNF_NUAGES-DE-POINTS-LIDAR-HD:bloc",
        "srsName": "EPSG:2154",
        "outputFormat": "application/json",
        "bbox": f"{minx},{miny},{maxx},{maxy}"
    }

    response = requests.get(LIDAR_FALLBACK_BASE_URL, params=params)
    ign_feature_collection = requests.get(response.url).json()
    features = ign_feature_collection.get("features", [])
    print(ign_feature_collection)
    if features:
        lidar_url = features[0].get("properties", {}).get("url")
        print(f"LIDAR FALLBACK URL={lidar_url}")
        return lidar_url
    return None

def download_first_mb(url, output_path="tmp_lidar.bin", max_bytes=1024*1024, timeout=10):
    try:
        if url is None:
            return False
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()

            downloaded = 0

            with open(output_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if not chunk:
                        continue

                    remaining = max_bytes - downloaded
                    if remaining <= 0:
                        break

                    if len(chunk) > remaining:
                        f.write(chunk[:remaining])
                        break
                    else:
                        f.write(chunk)
                        downloaded += len(chunk)

        # Remove file
        if os.path.exists(output_path):
            os.remove(output_path)

        return True

    except requests.RequestException as e:
        return False, f"Error while downloading lidar file : {str(e)}"

def instatus_monitoring(s3_bucket, s3_conf_file_key):
    output_path = "lidar/instatus-lidar-datatest.json"
    download_fileconf_from_s3(s3_bucket, s3_conf_file_key, output_path)

    authorization_headers = {
        "Authorization": f"Bearer {INSTATUS_API_KEY}",
        "Content-Type": "application/json",
    }

    base_url_v1 = f"https://api.instatus.com/v1/{INSTATUS_PAGE_ID}"
    base_url_v2 = f"https://api.instatus.com/v2/{INSTATUS_PAGE_ID}"

    with requests.Session() as session:
        session.headers.update(authorization_headers)

        # Retrieve all components statuses
        print("Retrieve all component statuses ...")
        response = session.get(f"{base_url_v2}/components?page=1&per_page=50")
        response.raise_for_status()
        components_status = response.json()

        # Save component id and status on components_dict
        components_dict = {c["id"]: c["status"] for c in components_status}

        with open(output_path, "r") as f:
            data = json.load(f)

        updated = False

        for address in data:
            monitoring_failed = False

            layer = address["layer"]
            x, y, z = map(int, address["xyz"].split(","))
            component_id = address["componentId"]
            address_tested = address["address"]
            incident_id = address.get("incidentId", "")

            print(f"=============================================== \n"
                  f"Process monitoring on address={address_tested}")

            url = monitor_lidar(x, y, z)
            is_downloadable = download_first_mb(url)
            print(f"is_downloadable={is_downloadable}")

            if is_downloadable is False:
                monitoring_failed = True

            current_component_status = components_dict.get(component_id)

            if not current_component_status:
                print(f"Component {component_id} not found")
                continue

            # ----------------------------------------------------------------
            # CASE 1 : Monitoring KO + Component OPERATIONAL → Create incident
            # ----------------------------------------------------------------
            if current_component_status == "OPERATIONAL" and monitoring_failed:
                print(f"[CREATE] Incident for {address_tested}")

                incident_body = {
                    "name": f"Lidar unavailable on address {address_tested}",
                    "message": "Lidar link is not available",
                    "components": [component_id],
                    "status": "INVESTIGATING",
                    "notify": True,
                     "statuses": [{
                        "id": component_id,
                        "status": "PARTIALOUTAGE"
                    }]
                }

                incident_response = session.post(
                    f"{base_url_v1}/incidents",
                    json=incident_body,
                )
                incident_response.raise_for_status()

                new_incident_id = incident_response.json()["id"]
                address["incidentId"] = new_incident_id
                updated = True
                print(f"Incident created, incident_id={new_incident_id}")

            # ------------------------------------------------------------
            # CASE 2 : Monitoring KO + Component already on PARTIALOUTAGE
            # ------------------------------------------------------------
            elif current_component_status == "PARTIALOUTAGE" and monitoring_failed:
                print(f"[SKIP] {address_tested} already in PARTIALOUTAGE, incident is not created anymore")

            # ------------------------------------------------------------
            # CAS 3 : Monitoring OK + Component PARTIALOUTAGE → Résolution
            # ------------------------------------------------------------
            elif current_component_status == "PARTIALOUTAGE" and not monitoring_failed and incident_id:
                print(f"[RESOLVE] Incident {incident_id}")

                resolve_body = {
                    "name": f"Lidar is available on {layer}",
                    "message": f"Lidar available on address={address_tested}",
                    "started": datetime.now(timezone.utc).isoformat(),
                    "components": [component_id],
                    "status": "RESOLVED",
                    "impact": "NONE",
                    "notify": True,
                    "statuses": [{
                        "id": component_id,
                        "status": "OPERATIONAL"
                    }]
                }

                # CREATE INCENDETION RESOLUTION
                session.put(
                    f"{base_url_v1}/incidents/{incident_id}",
                    json=resolve_body,
                ).raise_for_status()

                # UPDATE COMPONENT STATUS TO OPERATIONAL
                session.put(
                    f"{base_url_v2}/components/{component_id}",
                    json={
                        "description": "Lidar is available on this area",
                        "status": "OPERATIONAL",
                    },
                ).raise_for_status()

                address["incidentId"] = None
                updated = True

                print("Incident resolved ...")

            # ------------------------------------------------------------
            # CAS 4 : Monitoring OK + Component OPERATIONAL → No action
            # ------------------------------------------------------------
            else:
                print("No action required, Monitoring OK, Component OPERATIONAL.")

        # Save json file and upload to s3 if incident was created / incident was resolved
        if updated:
            print("Update datatest on s3")
            with open(output_path, "w") as f:
                json.dump(data, f, indent=4)
            upload_config(s3_bucket, s3_conf_file_key, output_path)

if __name__ == '__main__':
    s3_bucket=sys.argv[1]
    s3_conf_file_key=sys.argv[2]
    # s3_bucket = "instatus-bucket"
    # s3_conf_file_key = "lidar/instatus-lidar-datatest.json"
    instatus_monitoring(s3_bucket, s3_conf_file_key)