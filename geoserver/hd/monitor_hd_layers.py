import os
import sys

import cv2
import requests
import json
import numpy as np
import pytesseract
from datetime import datetime, timezone

from PIL import Image

from lidar.s3_conf import download_fileconf_from_s3, upload_config

INSTATUS_API_KEY=os.environ["INSTATUS_API_KEY"]
INSTATUS_HD_LAYER_PAGE_ID=os.environ["INSTATUS_HD_LAYER_PAGE_ID"]
INSTATUS_BASE_URL_V1=f"https://api.instatus.com/v1/{INSTATUS_HD_LAYER_PAGE_ID}"
INSTATUS_BASE_URL_V2=f"https://api.instatus.com/v2/{INSTATUS_HD_LAYER_PAGE_ID}"

authorization_headers = {
        "Authorization": f"Bearer {INSTATUS_API_KEY}",
        "Content-Type": "application/json",
    }

def is_img_blank_or_black(img):
    if img is None:
        return True
    if np.all(img == 0) or np.all(img == 255):
        return True
    return None

def image_contains_failed_text(img):
    failed_keywords = ("failed", "wmts")

    # Si image PIL
    if isinstance(img, Image.Image):
        gray = img.convert("L")
    # Si image OpenCV (numpy array)
    elif isinstance(img, np.ndarray):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        # Format d'image non supporté
        return True

    custom_config = r'--oem 3 --psm 6'
    try_text = pytesseract.image_to_string(gray, config=custom_config)
    txt_norm = try_text.lower()
    return any(k in txt_norm for k in failed_keywords)

def is_server_returning_incorrect_image(url):
    server_response = requests.get(url)

    if server_response.status_code != 200:
        return True

    image_array = np.asarray(bytearray(server_response.content), dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)

    if image_contains_failed_text(image):
        print("Image contains failed text")
        return True

    if is_img_blank_or_black(image):
        print("Image is blank or black")
        return True

def create_instatus_incident(layer, component_id):
    with requests.Session() as session:
        session.headers.update(authorization_headers)

        incident_body = {
            "name": f"Layer unavailable: {layer}",
            "message": f"Layer {layer} is not returning valid image",
            "components": [component_id],
            "status": "INVESTIGATING",
            "notify": True,
            "statuses": [{
                "id": component_id,
                "status": "PARTIALOUTAGE"
            }]
        }

        incident_response = session.post(
            f"{INSTATUS_BASE_URL_V1}/incidents",
            json=incident_body,
        )
        incident_response.raise_for_status()

        new_incident_id = incident_response.json()["id"]
        print(f"Incident created, incident_id={new_incident_id}")
        return new_incident_id

def resolve_incident_and_update_component_status(layer, component_id, incident_id):
    with requests.Session() as session:
        session.headers.update(authorization_headers)
        resolve_body = {
            "name": f"Layer available: {layer}",
            "message": f"Layer {layer} is currently available",
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

        # RESOLVE INCIDENT
        session.put(
            f"{INSTATUS_BASE_URL_V1}/incidents/{incident_id}",
            json=resolve_body,
        ).raise_for_status()

        # UPDATE COMPONENT STATUS TO OPERATIONAL
        session.put(
            f"{INSTATUS_BASE_URL_V2}/components/{component_id}",
            json={
                "description": f"Layer {layer} is available",
                "status": "OPERATIONAL",
            },
        ).raise_for_status()
        print("Incident resolved ...")

def fetch_instatus_components_statuses():
    response = requests.get(f"{INSTATUS_BASE_URL_V2}/components?page=1&per_page=50", headers=authorization_headers)
    components_status = response.json()
    components_dict = {c["id"]: c["status"] for c in components_status}
    return components_dict

def instatus_hd_layers_monitoring(testdata_file):
    components_statuses = fetch_instatus_components_statuses()
    with open(testdata_file, "r") as f:
        data = json.load(f)

    updated = False

    for item in data:
        monitoring_failed = False
        url = item["url"]
        component_id = item["componentId"]
        address = item["address"]
        incident_id = item["incidentId"]
        layer = item["layer"]

        print(f"====================================================================== \n"
              f"Process monitoring on address={address}")

        if is_server_returning_incorrect_image(url):
            monitoring_failed = True

        current_component_status = components_statuses.get(component_id)

        if not current_component_status:
            print(f"Component {component_id} not found")
            continue

        # ----------------------------------------------------------------
        # CASE 1 : Monitoring KO + Component OPERATIONAL → Create incident
        # ----------------------------------------------------------------
        if current_component_status == "OPERATIONAL" and monitoring_failed:
            print(f"[CREATE] Incident for {address}")
            new_incident = create_instatus_incident(layer, component_id)
            item["incident_id"] = new_incident
            updated = True

        # ------------------------------------------------------------
        # CASE 2 : Monitoring KO + Component already on PARTIALOUTAGE
        # ------------------------------------------------------------
        elif current_component_status == "PARTIALOUTAGE" and monitoring_failed:
            print(f"[SKIP] {address} already in PARTIALOUTAGE, incident is not created anymore")

        # ------------------------------------------------------------
        # CAS 3 : Monitoring OK + Component PARTIALOUTAGE → Résolution
        # ------------------------------------------------------------
        elif current_component_status == "PARTIALOUTAGE" and not monitoring_failed and incident_id:
            print(f"[RESOLVE] Incident {incident_id}")
            resolve_incident_and_update_component_status(layer, component_id, incident_id)
            item["incident_id"] = None
            updated = True

        else:
            print("No action required, Monitoring OK, Component OPERATIONAL.")

        if updated:
            with open(testdata_file, "w") as f:
              json.dump(data, f, indent=2)

    return updated, testdata_file

def monitor_hd_layers(s3_bucket, s3_conf_file_key):
    output_path = s3_conf_file_key
    download_fileconf_from_s3(s3_bucket, s3_conf_file_key, output_path)
    is_updated, testdata_file= instatus_hd_layers_monitoring(output_path)

    if is_updated:
        upload_config(s3_bucket, s3_conf_file_key, testdata_file)

if __name__ == '__main__':
    monitor_hd_layers(sys.argv[1], sys.argv[2])





