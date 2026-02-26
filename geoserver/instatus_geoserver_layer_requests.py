import os

import requests
from datetime import datetime, timezone


INSTATUS_API_KEY=os.environ["INSTATUS_API_KEY"]
INSTATUS_BASE_URL_V1=f"https://api.instatus.com/v1"
INSTATUS_BASE_URL_V2=f"https://api.instatus.com/v2"

authorization_headers = {
        "Authorization": f"Bearer {INSTATUS_API_KEY}",
        "Content-Type": "application/json",
    }

def create_instatus_incident(layer, component_id, INSTATUS_PAGE_ID):
    with requests.Session() as session:
        session.headers.update(authorization_headers)

        incident_body = {
            "name": f"Layer unavailable: {layer}",
            "message": f"Layer {layer} is not returning valid image",
            "components": [component_id],
            "status": "INVESTIGATING",
            "notify": True
        }

        # CREATE INCIDENT
        incident_response = session.post(
            f"{INSTATUS_BASE_URL_V1}/{INSTATUS_PAGE_ID}/incidents",
            json=incident_body,
        )
        incident_response.raise_for_status()

        # UPDATE COMPONENT STATUS TO PARTIAL OUTAGE
        session.put(
            f"{INSTATUS_BASE_URL_V2}/{INSTATUS_PAGE_ID}/components/{component_id}",
            json={
                "description": f"Layer {layer} is unavailable on this area",
                "status": "PARTIALOUTAGE",
            },
        ).raise_for_status()

        new_incident_id = incident_response.json()["id"]
        print(f"Incident created, incident_id={new_incident_id}")
        return new_incident_id

def resolve_incident_and_update_component_status(layer, component_id, incident_id, INSTATUS_PAGE_ID):
    with requests.Session() as session:
        session.headers.update(authorization_headers)
        resolve_body = {
            "name": f"Layer available: {layer}",
            "message": f"Layer {layer} is currently available",
            "started": datetime.now(timezone.utc).isoformat(),
            "components": [component_id],
            "status": "RESOLVED",
            "notify": True
        }

        # RESOLVE INCIDENT
        session.put(
            f"{INSTATUS_BASE_URL_V1}/{INSTATUS_PAGE_ID}/incidents/{incident_id}",
            json=resolve_body,
        ).raise_for_status()

        # UPDATE COMPONENT STATUS TO OPERATIONAL
        session.put(
            f"{INSTATUS_BASE_URL_V2}/{INSTATUS_PAGE_ID}/components/{component_id}",
            json={
                "description": f"Layer {layer} is available",
                "status": "OPERATIONAL",
            },
        ).raise_for_status()
        print("Incident resolved ...")

def fetch_instatus_components_statuses(INSTATUS_PAGE_ID):
    response = requests.get(f"{INSTATUS_BASE_URL_V2}/{INSTATUS_PAGE_ID}/components?page=1&per_page=50", headers=authorization_headers)
    components_status = response.json()
    components_dict = {c["id"]: c["status"] for c in components_status}
    return components_dict