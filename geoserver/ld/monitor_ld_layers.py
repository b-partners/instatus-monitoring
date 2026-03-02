import os
import sys

import json

from geoserver.hd.monitor_hd_layers import monitor_geoserver_layer
from geoserver.instatus_geoserver_layer_requests import fetch_instatus_components_statuses, create_instatus_incident, \
    resolve_incident_and_update_component_status

from lidar.s3_conf import download_fileconf_from_s3, upload_config

INSTATUS_LD_LAYER_PAGE_ID=os.environ["INSTATUS_LD_LAYER_PAGE_ID"]

def instatus_ld_layers_monitoring(testdata_file):
    components_statuses = fetch_instatus_components_statuses(INSTATUS_LD_LAYER_PAGE_ID)
    with open(testdata_file, "r") as f:
        data = json.load(f)

    updated = False

    for item in data:
        component_id = item["componentId"]
        address = item["address"]
        incident_id = item["incidentId"]
        layer = item["layer"]

        print(f"====================================================================== \n"
              f"Process monitoring on address={address}")

        monitoring_failed, processing_time = monitor_geoserver_layer(address, "FLUX_IGN_2023_20CM")

        current_component_status = components_statuses.get(component_id)

        if not current_component_status:
            print(f"Component {component_id} not found")
            continue

        # ----------------------------------------------------------------
        # CASE 1 : Monitoring KO + Component OPERATIONAL → Create incident
        # ----------------------------------------------------------------
        if current_component_status == "OPERATIONAL" and monitoring_failed:
            print(f"[CREATE] Incident for {address}")
            new_incident = create_instatus_incident(layer, component_id, processing_time, INSTATUS_LD_LAYER_PAGE_ID)
            item["incidentId"] = new_incident
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
            resolve_incident_and_update_component_status(layer, component_id, processing_time, incident_id, INSTATUS_LD_LAYER_PAGE_ID)
            item["incidentId"] = None
            updated = True

        else:
            print("No action required, Monitoring OK, Component OPERATIONAL.")

        if updated:
            with open(testdata_file, "w") as f:
                json.dump(data, f, indent=2)

    return updated, testdata_file

def monitor_ld_layers(s3_bucket, s3_conf_file_key):
    output_path = s3_conf_file_key
    download_fileconf_from_s3(s3_bucket, s3_conf_file_key, output_path)
    is_updated, testdata_file= instatus_ld_layers_monitoring(output_path)

    if is_updated:
        upload_config(s3_bucket, s3_conf_file_key, testdata_file)

if __name__ == '__main__':
    monitor_ld_layers(sys.argv[1], sys.argv[2])





