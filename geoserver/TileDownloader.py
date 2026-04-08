import requests
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

    def download(self, ytile, xtile, zoom, server, layer):
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
        return url