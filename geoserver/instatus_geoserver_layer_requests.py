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
            "notify": True,
            "statuses": [
                {
                    "id": component_id,
                    "status": "PARTIALOUTAGE"
                }
            ]
        }

        # CREATE INCIDENT
        incident_response = session.post(
            f"{INSTATUS_BASE_URL_V1}/{INSTATUS_PAGE_ID}/incidents",
            json=incident_body,
        )
        incident_response.raise_for_status()

        new_incident_id = incident_response.json()["id"]
        print(f"Incident created, incident_id={new_incident_id}")
        return new_incident_id

def resolve_incident_and_update_component_status(layer, component_id, incident_id, INSTATUS_PAGE_ID):
    with requests.Session() as session:
        session.headers.update(authorization_headers)
        resolve_body = {
            "message": f"Layer {layer} is currently available",
            "started": datetime.now(timezone.utc).isoformat(),
            "components": [component_id],
            "status": "RESOLVED",
            "notify": True,
            "statuses": [
                {
                    "id": component_id,
                    "status": "OPERATIONAL"
                }
            ]
        }

        # RESOLVE INCIDENT
        session.post(
            f"{INSTATUS_BASE_URL_V1}/{INSTATUS_PAGE_ID}/incidents/{incident_id}/incident-updates",
            json=resolve_body,
        ).raise_for_status()

        print("Incident resolved ...")

def fetch_instatus_components_statuses(INSTATUS_PAGE_ID):
    response = requests.get(f"{INSTATUS_BASE_URL_V2}/{INSTATUS_PAGE_ID}/components?page=1&per_page=50", headers=authorization_headers)
    components_status = response.json()
    components_dict = {c["id"]: c["status"] for c in components_status}
    return components_dict


def fetch_active_incidents(INSTATUS_PAGE_ID):
    """
    Retrieve all active incidents (UNRESOLVED) and return
    un dict { component_id: incident_id } for concerning component.
    """
    print("Fetching active incidents from Instatus ...")
    active_incident_map = {}

    page = 1
    while True:
        response = requests.get(
            f"{INSTATUS_BASE_URL_V1}/{INSTATUS_PAGE_ID}/incidents",
            params={"page": page, "per_page": 100, "!status": "RESOLVED"}
        )
        response.raise_for_status()
        incidents = response.json()

        if not incidents:
            break

        for incident in incidents:
            incident_id = incident["id"]
            for component in incident.get("components", []):
                component_id = component["id"]
                print(f"Active incident found: incident_id={incident_id}, component_id={component_id}")
                active_incident_map[component_id] = incident_id

        if len(incidents) < 100:
            break

        page += 1

    print(f"Total active incidents mapped: {len(active_incident_map)}")
    return active_incident_map