import numpy as np
from PIL import Image
import cv2
import pytesseract
import requests

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
