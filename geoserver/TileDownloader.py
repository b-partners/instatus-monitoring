import requests
import numpy as np
import cv2
import time
import json
from collections import OrderedDict
import mercantile
from pyproj import Proj, transform

def xyz_to_bbox(x, y, z):
    epsg_4326 = Proj(init='epsg:4326')
    epsg_3857 = Proj(init='epsg:3857')

    tile = [x, y, z]
    bbox = mercantile.bounds(*tile)

    minx, miny = transform(epsg_4326, epsg_3857, bbox[0], bbox[1])
    maxx, maxy = transform(epsg_4326, epsg_3857, bbox[2], bbox[3])

    bbox = OrderedDict([
        ('minx', minx),
        ('miny', miny),
        ('maxx', maxx),
        ('maxy', maxy)
    ])

    json_data = json.dumps(bbox)
    print(f"Bounding box: {json_data}")
    return json_data

class TileDownloader:

    def __init__(self):
        self.geoserver = "http://35.181.83.111/geoserver/cite/wms"

    def download(self, xtile, ytile, zoom, server, layer):
        if server.lower() == "geoserver":
            bbox = xyz_to_bbox(xtile, ytile, zoom)
            if isinstance(bbox, str):
                bbox = json.loads(bbox)

            minx = bbox["minx"]
            miny = bbox["miny"]
            maxx = bbox["maxx"]
            maxy = bbox["maxy"]
            params = {
                "layers": layer,
                "format": "image/jpeg",
                "width": 1024,
                "height": 1024,
                "bbox": f"{minx},{miny},{maxx},{maxy}",
                "srs": "EPSG:3857",
                "transparent": "true",
                "service": "WMS",
                "request": "GetMap",
                "version": "1.3.0"
            }
            url = requests.get(url=self.geoserver, params=params).url

        print(f"URL: {url}")
        start_time = time.time()
        response = requests.get(url)
        processing_time = time.time() - start_time

        if response.status_code != 200:
            print(f"Failed to fetch image: {response.status_code}")
            return None

        image_array = np.asarray(bytearray(response.content), dtype=np.uint8)
        image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        return image , processing_time, url