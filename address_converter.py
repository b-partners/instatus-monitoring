import os

import mercantile
import requests


def convert_address_to_lat_lon(address):
    google_base_url = "https://maps.googleapis.com/maps/api/geocode/json"
    key = os.getenv("GOOGLE_API_KEY")

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