import requests
import json
import os
import re
import base64

def ocr_space_file(filename, overlay=False, api_key='helloworld'):
    """OCR.space API request with local file"""
    payload = {
        'isOverlayRequired': overlay,
        'apikey': api_key,
        'language': 'eng',
    }
    
    with open(filename, 'rb') as f:
        files = {'filename': f}
        response = requests.post(
            'https://api.ocr.space/parse/image',
            files=files,
            data=payload,
        )
    
    return response.json()

def main():
    filename = "test1.jpg"
    
    if not os.path.exists(filename):
        print(f"File {filename} not found!")
        return
    
    print("Using online OCR API...")
    result = ocr_space_file(filename)
    
    if result['IsErroredOnProcessing']:
        print("OCR API error:", result['ErrorMessage'])
        return
    
    # Extract text
    text = ''
    for item in result['ParsedResults']:
        text += item['ParsedText'] + '\n'
    
    print("Extracted Text:")
    print("=" * 40)
    print(text.strip())
    print("=" * 40)
    
    # Simple parsing
    patterns = {
        'name': r'NAME[:\s]*([A-Z\s]{5,30})',
        'dob': r'DOB[:\s]*(\d{2}[/-]\d{2}[/-]\d{4})',
        'id_number': r'NO[.\s]*([A-Z0-9]{6,12})',
    }
    
    print("\nParsed Information:")
    for field, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            print(f"{field.upper():<12}: {match.group(1)}")

# document_extracter.py
def extract_text_from_file(filename, api_key='helloworld'):
    result = ocr_space_file(filename, api_key=api_key)

    # result is a dict from requests.json()
    if result.get('IsErroredOnProcessing'):
        raise ValueError(f"OCR API error: {result.get('ErrorMessage')}")

    # Collect all text from the parsed results
    text = ''
    for item in result.get('ParsedResults', []):
        text += item.get('ParsedText', '') + '\n'

    return text.strip()   # ✅ return just the extracted string





if __name__ == "__main__":
    main()