import csv
import json
import os

import requests

from geoserver.TileDownloader import TileDownloader
from lidar.s3_conf import download_fileconf_from_s3, upload_config
import mercantile

# TODO: Tester sur des adresses à rajouter : Suisse, luxembourg
dataset = [
    {
        "address": "46.2194790,6.2238254",
        "componentName": "SUISSE",
        "layer": "SUISSE",
    },
    {
        "address": "44 Bisserweg, 1238 Grund Luxembourg",
        "componentName": "LUXEMBOURG",
        "layer": "LUXEMBOURG",
    },
]

api_key = os.getenv('INSTATUS_API_KEY')
INSTATUS_HD_IMAGES_PAGE_ID = os.getenv('INSTATUS_HD_IMAGES_PAGE_ID')
INSTATUS_LD_IMAGES_PAGE_ID = os.getenv('INSTATUS_LD_IMAGES_PAGE_ID')
INSTATUS_HD_POINTSCLOUD_PAGE_ID = os.getenv('INSTATUS_HD_POINTSCLOUD_PAGE_ID')
s3_bucket = "instatus-bucket"

def update_csv_and_dataset(dataset):
    address_csv_path = 'instatus-tested-addresses/instatus-addresses.csv'

    with open(address_csv_path, 'a', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=['Addresses'], quoting=csv.QUOTE_ALL, lineterminator='\n')


        for data in dataset:
            writer.writerow({"Addresses": data["address"]})

    print("Update hd dataset and upload config to s3")
    hd_file_path = update_hd_dataset(dataset)
    upload_config(s3_bucket, hd_file_path, hd_file_path)

    print("Update ld dataset and upload config to s3")
    ld_file_path = update_ld_dataset(dataset)
    upload_config(s3_bucket, ld_file_path, ld_file_path)

    print("Update lidar dataset and upload config to s3")
    lidar_file_path = update_lidar_dataset(dataset)
    upload_config(s3_bucket, lidar_file_path, lidar_file_path)

def create_instatus_component(page_id, component_name):
    url = f"https://api.instatus.com/v1/{page_id}/components"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "name": component_name,
        "status": "OPERATIONAL",
        "showUptime": True
    }

    response = requests.post(url, json=payload, headers=headers)

    if not response.ok:
        print(f"Instatus error {response.status_code}: {response.text}")
        response.raise_for_status()

    return response.json()["id"]


def update_hd_dataset(dataset):
    s3_bucket = 'instatus-bucket'
    s3_conf_file_key = 'geoserver/hd/instatus-hd-datatest.json'
    output_path = 'geoserver/hd/instatus-hd-datatest.json'

    download_fileconf_from_s3(s3_bucket, s3_conf_file_key, output_path)

    with open(output_path, 'r') as f:
        source_data = json.load(f)
        for data in dataset:
            address = data["address"]
            layer = data["layer"]
            component_name = data["componentName"]
            component_id = create_instatus_component(INSTATUS_HD_IMAGES_PAGE_ID, component_name)
            url = generate_geoserver_request_url_from_address(address, layer)
            new_data = {
                "url": url,
                "address": address,
                "layer": layer,
                "componentId": component_id,
            }

            source_data.append(new_data)

    with open(output_path, 'w') as f:
        json.dump(source_data, f, indent=4)

    return output_path

def update_ld_dataset(dataset):
    s3_bucket = 'instatus-bucket'
    s3_conf_file_key = 'geoserver/ld/instatus-ld-datatest.json'
    output_path = 'geoserver/ld/instatus-ld-datatest.json'

    download_fileconf_from_s3(s3_bucket, s3_conf_file_key, output_path)

    with open(output_path, 'r') as f:
        source_data = json.load(f)
        print(source_data)
        for data in dataset:
            address = data["address"]
            layer = data["layer"]
            component_id = create_instatus_component(INSTATUS_LD_IMAGES_PAGE_ID, layer)
            url = generate_geoserver_request_url_from_address(address, "FLUX_IGN_2023_20CM")
            new_data = {
                "url": url,
                "address": address,
                "layer": layer,
                "componentId": component_id,
            }

            source_data.append(new_data)

    with open(output_path, 'w') as f:
        json.dump(source_data, f, indent=4)

    return output_path

def update_lidar_dataset(dataset):
    s3_bucket = 'instatus-bucket'
    s3_conf_file_key = 'lidar/instatus-lidar-datatest.json'
    output_path = 'lidar/instatus-lidar-datatest.json'

    download_fileconf_from_s3(s3_bucket, s3_conf_file_key, output_path)

    with open(output_path, 'r') as f:
        source_data = json.load(f)
        for data in dataset:
            component_name = data["componentName"]
            component_id = create_instatus_component(INSTATUS_HD_POINTSCLOUD_PAGE_ID, component_name)
            address = data["address"]
            layer = data["layer"]
            xyz = generate_xyz_from_address(address)
            new_data = {
                "address": address,
                "layer": layer,
                "componentId": component_id,
                "xyz": xyz
            }

            source_data.append(new_data)

    with open(output_path, 'w') as f:
        json.dump(source_data, f, indent=4)

    return output_path

def convert_address_to_lat_lon(address, key):
    google_base_url = "https://maps.googleapis.com/maps/api/geocode/json"

    response = requests.get(google_base_url, params={"address": address, "key": key})
    if response.status_code == 200:
        data = response.json()
        if data.get("results"):
            location = data["results"][0]["geometry"]["location"]
            return location["lat"], location["lng"]
        else:
            print("Adresse non trouvée.")
            return None
    else:
        print(f"Erreur API : {response.status_code}")
        return None

def convert_lat_lon_to_xyz_coordinates(lat, lng, zoom):
    tile = mercantile.tile(lng, lat, zoom)
    return tile.x, tile.y, tile.z

def generate_xyz_from_address(address):
    lat, lon = convert_address_to_lat_lon(address, os.environ['GOOGLE_API_KEY'])
    x,y,z = convert_lat_lon_to_xyz_coordinates(lat, lon, 20)
    return f"{x},{y},{z}"

def generate_geoserver_request_url_from_address(address, layer):
    tile_downloader = TileDownloader()
    xyz = generate_xyz_from_address(address)
    x, y, z = xyz.split(",")
    url = tile_downloader.download(int(y),int(x),int(z),"geoserver", layer)
    return url

if __name__ == '__main__':
    update_csv_and_dataset(dataset)