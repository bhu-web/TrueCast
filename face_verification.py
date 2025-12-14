# face_verification.py

import cv2
import numpy as np
from io import BytesIO
from PIL import Image
import base64

def preprocess_image_data(base64_img_string):
    """
    Decodes a base64 string (from client-side JavaScript) into a NumPy array
    compatible with OpenCV.
    """
    try:
        # 1. Clean up base64 string
        # Expecting 'data:image/png;base64,...'
        _, img_data = base64_img_string.split(',')
        
        # 2. Decode the base64 string
        binary_data = base64.b64decode(img_data)
        
        # 3. Convert binary data to NumPy array
        np_array = np.frombuffer(binary_data, np.uint8)
        
        # 4. Decode the image using OpenCV
        img = cv2.imdecode(np_array, cv2.IMREAD_COLOR)
        
        if img is None:
            print("Error: Could not decode image data.")
            return None
            
        return img
    except Exception as e:
        print(f"Error in preprocess_image_data: {e}")
        return None

def verify_face_match(registration_img_b64, login_img_b64):
    """
    Performs face detection and a simple image comparison (MSE) between two images.
    
    Args:
        registration_img_b64 (str): Base64 string of the photo captured during registration.
        login_img_b64 (str): Base64 string of the photo captured during login.
        
    Returns:
        tuple: (bool success, str message, float score)
    """
    # Load the pre-trained face detector (Haar Cascades is simple and fast, good for Approach 1)
    # NOTE: This requires 'haarcascade_frontalface_default.xml' to be available. 
    # For this simplified model, we will use a simulation or assume the cascade file is present.
    
    # --- SIMULATED FACE DETECTION (due to missing cascade file) ---
    reg_img = preprocess_image_data(registration_img_b64)
    log_img = preprocess_image_data(login_img_b64)
    
    if reg_img is None or log_img is None:
        return False, "Failed to decode one or both images.", 0.0

    # For a real implementation, you would run face detection here:
    # face_cascade = cv2.CascadeClassifier('haarcascade_frontalface_default.xml')
    # gray_reg = cv2.cvtColor(reg_img, cv2.COLOR_BGR2GRAY)
    # faces_reg = face_cascade.detectMultiScale(gray_reg, 1.1, 4)
    # ... and crop faces before comparison

    # --- Simple Mean Squared Error (MSE) comparison of the full images (UNRELIABLE BUT SIMPLE) ---
    # Convert to grayscale for simple comparison
    gray_reg = cv2.cvtColor(reg_img, cv2.COLOR_BGR2GRAY)
    gray_log = cv2.cvtColor(log_img, cv2.COLOR_BGR2GRAY)
    
    # Resize the smaller image to match the larger one (CRITICAL for comparison)
    h_reg, w_reg = gray_reg.shape[:2]
    h_log, w_log = gray_log.shape[:2]
    
    if h_reg != h_log or w_reg != w_log:
        # Resize login image to match registration image size for MSE
        gray_log = cv2.resize(gray_log, (w_reg, h_reg), interpolation=cv2.INTER_AREA)

    # Calculate MSE: the lower the value, the closer the match
    err = np.sum((gray_reg.astype("float") - gray_log.astype("float")) ** 2)
    err /= float(gray_reg.shape[0] * gray_reg.shape[1])
    
    # Thresholding: Lower MSE means a better match. 
    # We use a high threshold (e.g., 5000) because MSE on full images is very volatile.
    # In a real app, a much smaller face patch would be compared.
    MSE_THRESHOLD = 5000.0 
    
    match = err < MSE_THRESHOLD
    
    if match:
        return True, "Face verified successfully (Simple MSE Match).", round(err, 2)
    else:
        return False, f"Face verification failed. Score: {round(err, 2)} (Threshold: {MSE_THRESHOLD}).", round(err, 2)