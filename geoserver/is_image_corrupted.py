import numpy as np
from PIL import Image
import cv2
import pytesseract

from address_converter import convert_address_to_lat_lon, convert_lat_lon_to_xyz_coordinates
from geoserver.TileDownloader import TileDownloader


def retrieve_image_from_address(address, layer):
    lat, lon = convert_address_to_lat_lon(address)
    x,y,z = convert_lat_lon_to_xyz_coordinates(lat, lon, 20)
    tile_downloader  = TileDownloader()
    image, processing_time, url = tile_downloader.download(x, y, z, "geoserver", layer)
    return image, processing_time, url

def is_server_returning_incorrect_image(image):
    if image is None:
        return True

    if image_contains_failed_text(image):
        print("Image contains failed text")
        return True

    if is_img_blank_or_black(image):
        print("Image is blank or black")
        return True

    return False

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
