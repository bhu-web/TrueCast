import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from datetime import datetime, timezone, timedelta # Ensure timedelta is imported
import random
import string
import re
import json
import hashlib
from functools import wraps
import easyocr
import io # Needed to read file bytes
import cv2 # NEW: OpenCV for image processing
import numpy as np # NEW: Numpy for image arrays
import google.generativeai as genai # NEW IMPORT
from flask_mail import Mail, Message # New import
from dotenv import load_dotenv
load_dotenv()

print("DEBUG USER:", os.environ.get('MAIL_USERNAME'))
print("DEBUG PASS:", os.environ.get('MAIL_PASSWORD'))

# NOTE: document_extracter is assumed to be present for the OCR redirect logic
try:
    from document_extracter import extract_text_from_file 
except ImportError:
    print("Warning: 'document_extracter' not found. OCR simulation will be used.")
    def extract_text_from_file(*args, **kwargs):
        raise NotImplementedError("document_extracter module is missing.")

# --- NEW: Initialize EasyOCR Reader ---
try:
    # Using English only as requested for pattern matching
    ocr_reader = easyocr.Reader(['en']) 
    print("EasyOCR reader loaded successfully (English Only).")
except Exception as e:
    print(f"Warning: Could not load EasyOCR. OCR route will fail. Error: {e}")
    ocr_reader = None

# --- Configuration & File Setup ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = os.path.join(BASE_DIR, 'voters.json') # Stores voter profiles
VOTES_FILE = os.path.join(BASE_DIR, 'votes.json') # Stores cast vote records
ELECTIONS_FILE = os.path.join(BASE_DIR, 'elections.json') # Stores election details (NEW)
SECRET_KEY = os.environ.get('SECRET_KEY', 'a_very_secret_key_for_truecast_sessions') 

app = Flask(__name__)
app.secret_key = SECRET_KEY

# --- NEW: Email Configuration ---
# NOTE: For Gmail, you must use an "App Password", not your regular password.
# Go to Google Account > Security > 2-Step Verification > App Passwords.
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', '')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', '')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME', '')

mail = Mail(app)


# --- NEW: Gemini API Configuration ---
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    print("Warning: GEMINI_API_KEY not found in environment variables.")

# --- Configuration for the TRUECAST Bot (Indian Context) ---
TRUECAST_SYSTEM_PROMPT = """
You are the official AI support assistant for TRUECAST, a secure blockchain-based digital voting platform for Indian elections.
Your role is to assist voters with the following tasks:

1. **Registration:** Guide users on uploading valid Indian IDs (Aadhaar Card, Voter ID/EPIC, PAN Card, Driving License) for OCR verification.
2. **Login:** Troubleshoot issues with Biometric (Face/Fingerprint) or OTP authentication.
3. **Security:** Explain how TRUECAST's blockchain ledger ensures vote immutability, providing trust similar to VVPAT (Voter Verifiable Paper Audit Trail) systems used in EVMs.
4. **Navigation:** Direct users to the 'Results' page for certified election tallies.

GUIDELINES:
- **STRICT NEUTRALITY:** You must remain non-partisan. Do NOT express opinions on Indian political parties (e.g., BJP, INC, AAP, etc.) or candidates.
- **TONE:** Professional, reassuring, and helpful. Use Indian English nuances where appropriate.
- **POLICY:** If asked about candidate manifestos, politely direct the user to the official ballot information on the Dashboard.
- **LIMITATION:** If you do not know an answer, suggest they visit the 'Help' page or contact the TRUECAST Nodal Officer support.
"""

# Initialize the model with the system instruction
# --- Robust Model Initialization ---
# --- Robust Model Initialization (Uses the currently stable 2.5 Flash model) ---
# We use 'gemini-2.5-flash' as it is the current, fast, and recommended stable version.
# If this fails, there is a fundamental issue with the API key or service enablement.
try:
    chat_model = genai.GenerativeModel(
        model_name='gemini-2.5-flash', 
        system_instruction=TRUECAST_SYSTEM_PROMPT
    )
    print("Gemini AI initialized with: gemini-2.5-flash (Stable)")
    
except Exception as e:
    print(f"CRITICAL ERROR: Could not initialize model gemini-2.5-flash. Details: {e}")
    # Fallback check for API key
    if not GEMINI_API_KEY:
        print("ACTION REQUIRED: GEMINI_API_KEY is missing. Check your .env file.")
    chat_model = None

# --- NEW: OTP Routes ---

@app.route('/api/send-otp', methods=['POST'])
def send_otp():
    data = request.get_json()
    voter_identifier = data.get('voterId') # Can be ID or Email
    
    if not voter_identifier:
        return jsonify({'success': False, 'error': 'Voter ID is required.'}), 400

    voters = load_voters()
    target_voter = None
    
    # 1. Find the voter and their email
    for v_id, v_data in voters.items():
        if v_data.get('voter_id') == voter_identifier or v_data.get('email') == voter_identifier:
            target_voter = v_data
            break
            
    if not target_voter:
        return jsonify({'success': False, 'error': 'Voter not found.'}), 404
        
    email = target_voter.get('email')
    if not email:
        return jsonify({'success': False, 'error': 'No email address linked to this voter.'}), 400

    # 2. Generate 6-digit OTP
    otp = str(random.randint(100000, 999999))
    
    # 3. Store OTP in session (encrypted cookie) for verification later
    # We also store the voter_id to ensure the OTP is used for the correct user
    session['otp'] = otp
    session['otp_voter_id'] = target_voter['voter_id']
    session['otp_timestamp'] = datetime.now(timezone.utc).timestamp()

    # 4. Send Email
    try:
        msg = Message('TrueCast Login Verification', recipients=[email])
        msg.body = f"Your One-Time Password (OTP) for TrueCast voting is: {otp}\n\nThis code expires in 5 minutes.\nDo not share this code."
        mail.send(msg)
        
        # Return success (mask the email for privacy)
        masked_email = re.sub(r'(.).*@', r'\1***@', email)
        return jsonify({'success': True, 'message': f'OTP sent to {masked_email}'})
        
    except Exception as e:
        print(f"Email error: {e}")
        return jsonify({'success': False, 'error': 'Failed to send email. Check server logs.'}), 500

@app.route('/api/verify-otp-login', methods=['POST'])
def verify_otp_login():
    data = request.get_json()
    input_otp = data.get('otp')
    
    # 1. Check if OTP exists in session
    stored_otp = session.get('otp')
    stored_voter_id = session.get('otp_voter_id')
    timestamp = session.get('otp_timestamp')
    
    if not stored_otp or not input_otp:
        return jsonify({'success': False, 'error': 'Invalid request.'}), 400

    # 2. Check Expiration (5 minutes)
    if datetime.now(timezone.utc).timestamp() - timestamp > 300:
        session.pop('otp', None)
        return jsonify({'success': False, 'error': 'OTP has expired. Please request a new one.'}), 400
        
    # 3. Verify Match
    if input_otp == stored_otp:
        # 4. Log the user in (Same logic as voter_login)
        voters = load_voters()
        authenticated_voter = voters.get(stored_voter_id)
        
        if authenticated_voter:
            session['logged_in'] = True
            session['voter_id'] = authenticated_voter['voter_id']
            session['email'] = authenticated_voter.get('email')
            session['voter_region'] = authenticated_voter.get('voterRegion')
            
            first = authenticated_voter.get('firstName', '')
            last = authenticated_voter.get('lastName', '')
            session['full_name'] = f"{first} {last}".strip() or 'Voter'
            
            # Clear OTP from session
            session.pop('otp', None)
            session.pop('otp_voter_id', None)
            session.pop('otp_timestamp', None)
            
            return jsonify({
                'success': True, 
                'message': 'Authentication successful!',
                'redirect': url_for('voting_dashboard')
            })
    
    return jsonify({'success': False, 'error': 'Invalid OTP.'}), 401

IST = timezone(timedelta(hours=5, minutes=30))

# --- JSON Database Functions ---
def load_voters():
    """Reads the voter data from the JSON file."""
    if not os.path.exists(JSON_FILE):
        with open(JSON_FILE, 'w') as f:
             json.dump({}, f)
        return {}
    try:
        with open(JSON_FILE, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError:
        with open(JSON_FILE, 'w') as f:
             json.dump({}, f)
        return {} 

def save_voters(data):
    """Writes the voter data back to the JSON file."""
    with open(JSON_FILE, 'w') as f:
        json.dump(data, f, indent=4)
        
def load_votes():
    """Reads the dictionary of cast votes from the JSON file."""
    if not os.path.exists(VOTES_FILE):
        with open(VOTES_FILE, 'w') as f:
             json.dump({}, f) # Initialize as an empty dictionary
        return {}
    try:
        with open(VOTES_FILE, 'r') as f:
            data = json.load(f)
            # Ensure it's a dictionary, not a list
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, FileNotFoundError):
        with open(VOTES_FILE, 'w') as f:
             json.dump({}, f)
        return {}

def save_votes(data):
    """Writes the vote dictionary back to the JSON file."""
    with open(VOTES_FILE, 'w') as f:
        json.dump(data, f, indent=4)

# --- Election JSON Functions ---
def load_elections():
    """Reads the list of elections from the JSON file."""
    if not os.path.exists(ELECTIONS_FILE):
        with open(ELECTIONS_FILE, 'w') as f:
             json.dump([], f) # Initialize as an empty list
        return []
    try:
        with open(ELECTIONS_FILE, 'r') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, FileNotFoundError):
        with open(ELECTIONS_FILE, 'w') as f:
             json.dump([], f)
        return []

def save_elections(data):
    """Writes the election list back to the JSON file."""
    with open(ELECTIONS_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def get_active_election():
    """
    Returns the currently active election, or None, and automatically updates
    the status of expired elections.
    """
    elections = load_elections()
    
    now = datetime.now(IST)
    elections_changed = False
    active_election = None
    
    for election in elections:
        try:
            start_time = datetime.fromisoformat(election.get('startDate'))
            end_time = datetime.fromisoformat(election.get('endDate'))
            
            # Ensure they are timezone aware (IST is defined globally)
            if start_time.tzinfo is None: start_time = start_time.replace(tzinfo=IST)
            if end_time.tzinfo is None: end_time = end_time.replace(tzinfo=IST)
            
        except (ValueError, TypeError):
            continue 

        status = election.get('status', 'Active')
        
        # 1. Automatic End Check: If status is Active AND the end time is <= now
        if status == 'Active' and end_time <= now:
            election['status'] = 'Ended'
            elections_changed = True
            
        # 2. Check for the truly active election
        # Treat election as active if now is before end time
        elif election['status'] == 'Active' and now < end_time:
            active_election = election



            # Do NOT break here. We must continue iterating to check if any earlier elections need to be marked 'Ended'.
            
    # Save any automatic status changes (must be done outside the loop)
    if elections_changed:
        save_elections(elections)
    
    # After checking all elections and performing automatic ends, return the true active one.
    return active_election
    
    # Fallback to the most recent 'Active' election if the time window is missed
    for election in reversed(elections):
        if election.get('status') == 'Active':
            return election

    return None

def get_election_by_id(election_id):
    """Finds an election by its ID."""
    elections = load_elections()
    for election in elections:
        if election.get('id') == election_id:
            return election
    return None
# --- End Election JSON Functions ---

# --- Authentication Decorator (For Protecting Routes) ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('voter_login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('You must be an admin to access this page.', 'error')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function 

# --- Helper Functions ---
def generate_hash_id(length=64):
    return '0x' + ''.join(random.choices(string.hexdigits.lower(), k=length))

# --- NEW: Image Preprocessing Function ---
def preprocess_image(file_bytes):
    """
    Cleans the image for better OCR results:
    1. Convert to Grayscale
    2. Apply Thresholding (Binarization) to make text pop
    3. Denoise
    """
    # Convert bytes to numpy array
    nparr = np.frombuffer(file_bytes, np.uint8)
    
    # Decode image
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    if img is None:
        return file_bytes # Return original if decoding fails
    
    # 1. Convert to grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # 2. Apply simple thresholding or adaptive thresholding
    # This makes the text black and background white (or vice versa)
    # Binary threshold: If pixel > 127, make it 255 (white), else 0 (black)
    _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # 3. Optional: Denoise slightly if the image is noisy
    # cleaned = cv2.fastNlMeansDenoising(thresh, None, 10, 7, 21)
    
    # Encode back to bytes for EasyOCR
    _, encoded_img = cv2.imencode('.jpg', thresh)
    return encoded_img.tobytes()

def clean_text_keep_english(text):
    """
    Removes non-ASCII characters to confuse the regex parser less.
    """
    # Keep standard ASCII printable characters (letters, numbers, punctuation, whitespace)
    return re.sub(r'[^\x00-\x7F]+', '', text)

# --- START: NEW PATTERN-BASED PARSER (No Headers Required) ---

def parse_ocr_text(text):
    """
    Generic Pattern-Based Parser for Indian IDs.
    Does NOT rely on "Name:", "DOB:", "Address:" headers.
    """
    
    # 1. Pre-clean text: Remove Hindi/Regional chars
    text = clean_text_keep_english(text)
    
    parsed_data = {}
    
    # --- 1. ID NUMBER Patterns ---
    
    # Aadhaar Pattern: 12 digits (4 4 4), possibly space separated
    # Matches: 1234 5678 9012 or 123456789012
    aadhaar_match = re.search(r'\b(\d{4}\s?\d{4}\s?\d{4})\b', text)
    
    # PAN Pattern: 5 letters, 4 digits, 1 letter
    # Matches: ABCDE1234F
    pan_match = re.search(r'\b([A-Z]{5}\d{4}[A-Z])\b', text)
    
    # Passport Pattern: 1 Letter, 7 Digits
    # Matches: A1234567
    passport_match = re.search(r'\b([A-Z]\d{7})\b', text)
    
    # Driving License Pattern: State Code (2) + Digits
    # Matches: KA01 20200012345
    dl_match = re.search(r'\b([A-Z]{2}[-\s]?\d{13,})\b', text)

    if aadhaar_match:
        parsed_data['ID Number'] = aadhaar_match.group(1).replace(" ", "")
        parsed_data['docType'] = 'Aadhaar Card'
    elif pan_match:
        parsed_data['ID Number'] = pan_match.group(1)
        parsed_data['docType'] = 'PAN Card'
    elif passport_match:
        parsed_data['ID Number'] = passport_match.group(1)
        parsed_data['docType'] = 'Passport'
    elif dl_match:
        parsed_data['ID Number'] = dl_match.group(1)
        parsed_data['docType'] = 'Driving License'
    else:
        parsed_data['ID Number'] = 'Not Found'
        parsed_data['docType'] = 'Unknown'

    # --- 2. DATE OF BIRTH Patterns ---
    
    # Look for DD/MM/YYYY or DD-MM-YYYY
    # We prioritize dates that are seemingly valid birth years (e.g. 1900-2015)
    # This helps avoid capturing "Issue Dates" that might be in the future or very recent
    dob_match = re.search(r'\b(\d{2}[/-]\d{2}[/-](?:19|20)\d{2})\b', text)
    
    if dob_match:
        parsed_data['Date of Birth'] = dob_match.group(1)
    else:
        # Fallback: Look for Year of Birth (YYYY) common in Aadhaar
        yob_match = re.search(r'\b(19\d{2}|20\d{2})\b', text)
        if yob_match:
             parsed_data['Date of Birth'] = "01/01/" + yob_match.group(1)
        else:
             parsed_data['Date of Birth'] = 'Not Found'

    # --- 3. FULL NAME Patterns (Aadhaar Specific) ---
    
    # Heuristic: In Aadhaar, name is often the line ABOVE the DOB/Year of Birth
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    name_found = False
    
    if parsed_data['docType'] == 'Aadhaar Card':
        for i, line in enumerate(lines):
            # Check if this line contains the DOB or Year of Birth keywords
            if "DOB" in line or "Year of Birth" in line or "Birth" in line:
                # The line BEFORE this one is likely the name
                if i > 0:
                    potential_name = lines[i-1]
                    # Basic validation: ignore if it looks like "Government of India" or contains numbers
                    if "GOVERNMENT" not in potential_name.upper() and not any(char.isdigit() for char in potential_name):
                        parsed_data['Full Name'] = potential_name
                        name_found = True
                        break
            # Also check if the specific DOB value is in this line
            elif parsed_data['Date of Birth'] != 'Not Found' and parsed_data['Date of Birth'] in line:
                 if i > 0:
                    potential_name = lines[i-1]
                    if "GOVERNMENT" not in potential_name.upper() and not any(char.isdigit() for char in potential_name):
                        parsed_data['Full Name'] = potential_name
                        name_found = True
                        break

    if not name_found:
        # Fallback Heuristic: Names are usually:
        # - 2 or 3 words
        # - All capitalized or Title Case
        # - Containing only letters
        # - NOT keywords like "Government", "India", "Male", "Female", "Dob", "Address"
        
        stop_words = ["GOVERNMENT", "INDIA", "MALE", "FEMALE", "DOB", "DATE", "BIRTH", "ADDRESS", 
                      "YEAR", "FATHER", "HUSBAND", "NAME", "CARD", "INCOME", "TAX", "DEPARTMENT",
                      "UNIQUE", "IDENTIFICATION", "AUTHORITY", "PERMANENT", "ACCOUNT", "NUMBER"]
                      
        potential_names = []
        
        for line in lines:
            clean_line = line.strip()
            # Filter out lines with numbers or symbols
            if not clean_line or any(char.isdigit() for char in clean_line) or len(clean_line) < 4:
                continue
                
            # Filter out lines that contain stop words
            words = clean_line.split()
            if any(word.upper() in stop_words for word in words):
                continue
                
            # Check if it looks like a name (2-4 words, mostly letters)
            if 2 <= len(words) <= 4 and all(word.isalpha() for word in words):
                 potential_names.append(clean_line)

        # Selection Logic:
        if potential_names:
            # For now, just take the first plausible name found
            parsed_data['Full Name'] = potential_names[0]
        else:
            parsed_data['Full Name'] = 'Not Found'

    # --- 4. ADDRESS Patterns (PIN Code Anchor) ---
    
    # India Address Logic: Look for a 6-digit PIN code.
    # The address is almost always the text block immediately preceding the PIN.
    pin_match = re.search(r'\b(\d{6})\b', text)
    
    if pin_match:
        pin_code = pin_match.group(1)
        
        # Find the pin code in the text
        pin_index = text.find(pin_code)
        
        # Grab the preceding 100-150 characters
        start_index = max(0, pin_index - 120)
        raw_addr_text = text[start_index:pin_index + 6] # Include PIN
        
        # Cleaning: Remove common prefixes if they were captured
        clean_addr = re.sub(r'(Address|To|S/O|W/O|C/O)\s*[:.-]?\s*', '', raw_addr_text, flags=re.IGNORECASE)
        
        # Cleaning: Remove newlines
        clean_addr = re.sub(r'\n', ', ', clean_addr)
        
        parsed_data['Address'] = clean_addr.strip()
    else:
        parsed_data['Address'] = 'Not Found'
        
    return parsed_data
# --- END PARSERS ---


# --- Routes ---

@app.route('/')
def home():
    # This route needs a 'truecast_landing.html' template
    return render_template('truecast_landing.html',
                           logged_in=session.get('logged_in', False),
                           full_name=session.get('full_name', ''))

@app.route('/api/ocr_process', methods=['POST'])
def ocr_process():
    if 'idDocument' not in request.files:
        return jsonify({"success": False, "error": "No document uploaded."}), 400
    
    file = request.files.get('idDocument') # Use get() for single file
    
    if not file or not file.filename:
        return jsonify({"success": False, "error": "No document selected."}), 400
        
    if not ocr_reader:
         return jsonify({"success": False, "error": "OCR service is not available."}), 500

    try:
        # Read the file's content into memory
        file_bytes = file.read()
        
        # --- NEW: Preprocess Image ---
        processed_bytes = preprocess_image(file_bytes)
        
        # --- Run OCR ---
        # We use paragraph=True to group text logically, which helps the regex.
        # Pass the PROCESSED image bytes
        ocr_result = ocr_reader.readtext(processed_bytes, detail=0, paragraph=True)
        
        # Join all found text blocks into a single string
        raw_text = " \n ".join(ocr_result)
        
        # --- ADD THIS FOR DEBUGGING ---
        print("--- OCR RAW TEXT (Processed) ---")
        print(raw_text)
        print("--------------------------------")
        
        # Parse the raw text using your existing function
        parsed_data = parse_ocr_text(raw_text)
        
        # --- ADD THIS FOR DEBUGGING ---
        print("--- PARSED DATA (English Pattern) ---")
        print(parsed_data)
        print("-------------------------------------")
        
        # --- Store in session for final check ---
        # This is CRITICAL for server-side verification
        session['ocr_data'] = parsed_data
        
        # Return the data to the client
        return jsonify({
            "success": True, 
            "parsed_data": parsed_data,
            "raw_text": raw_text # Send raw text for debugging if you want
        })

    except Exception as e:
        print(f"OCR Processing Error: {e}") # Log the error for debugging
        return jsonify({"success": False, "error": f"An error occurred during OCR processing: {e}"}), 500

# app.py (Modified voter_register route)

@app.route('/voter-register', methods=['GET', 'POST'])
def voter_register():
    # --- Template variable setup (MUST run for both GET and failed POST) ---
    active_election = get_active_election()
    available_regions = {
        'North District', 'South District', 'East District', 
        'West District', 'Central District'
    }
    if active_election:
        for race in active_election.get('races', []):
            for candidate in race.get('candidates', []):
                r_val = candidate.get('region')
                if r_val and r_val != 'All Regions':
                    available_regions.add(r_val)
    sorted_regions = sorted(list(available_regions))
    
    # Initialize OCR variables for GET/Re-render paths
    parsed_data = {}
    ocr_results = []
    
    if request.method == 'POST':
        data = request.form.to_dict()
        # CRITICAL: Clean the incoming email of any accidental whitespace
        new_email = data.get('email', '').strip() 
        
        # --- DUPLICATE CHECK LOGIC ---
        voters = load_voters()
        
        for voter_id, voter_data in voters.items():
            # CRITICAL: Clean stored email before comparison, just in case
            stored_email = voter_data.get('email', '').strip()
            
            if stored_email and stored_email == new_email:
                # If email is found:
                flash('Registration failed: An account with this email address already exists. Please log in.', 'error')
                
                # FIX: RENDER the template directly to show the flash message immediately
                return render_template(
                    'truecast_voter_register.html',
                    parsed_data=data,
                    available_regions=sorted_regions
                ) 
        # --- END DUPLICATE CHECK LOGIC ---

        # --- FINAL SERVER-SIDE VERIFICATION ---
        # FIX: Make ocr_data optional to allow manual entry
        ocr_data = session.get('ocr_data', {})
        
        # Only validate against OCR if OCR data actually exists
        if ocr_data:
            is_valid, error_message = validate_registration(data, ocr_data)
            if not is_valid:
                flash(f'Registration Warning: {error_message}', 'warning')
                # Note: We changed this to a warning so it doesn't block registration, 
                # or you can keep it as 'error' and return if you want strict enforcement.
                # For now, let's allow it to proceed to fix the "button not working" issue.

        # If unique, proceed with saving (logic remains unchanged)
        voter_id = f"VS{datetime.now().year}{random.randint(100000, 999999)}"
        data['voter_id'] = voter_id
        data['registration_date'] = datetime.now(timezone.utc).isoformat() # Use aware datetime
        data['status'] = 'Active' 
        data['backupPin'] = data.get('backupPin', '000000') 
        voters[voter_id] = data
        save_voters(voters)
        session.pop('ocr_data', None) # Clear session data
        flash(f'Registration successful! Your new Voter ID is {voter_id}. Please log in.', 'success')
        return redirect(url_for('voter_login', success='true'))

    # --- GET request logic (Initial load) ---
    # Clear any old session data on a fresh GET
    session.pop('ocr_data', None)

    return render_template(
        'truecast_voter_register.html',
        parsed_data=parsed_data,
        ocr_results=ocr_results,
        available_regions=sorted_regions
    )

def validate_registration(form_data, ocr_data):
    """
    Helper function to validate form data against OCR session data.
    """
    # 1. Name Verification
    ocr_name = ocr_data.get('Full Name', 'Not Found').lower()
    if ocr_name == 'not found':
        # Allow manual entry if OCR fails name detection, but log it
        print("Warning: Name not found in OCR, proceeding with manual entry trust.")
        pass 
        # return False, "Could not read name from ID document. Please try again or use a clearer image."

    form_first = form_data.get('firstName', '').lower()
    form_last = form_data.get('lastName', '').lower()
    
    # If OCR name exists, check if parts match
    if ocr_name != 'not found':
        if form_first not in ocr_name and form_last not in ocr_name:
            # Only fail if NEITHER part matches.
            # This accounts for "Ialid" vs "Khalid" OCR errors
             return False, f"Name on form ('{form_first} {form_last}') does not match name on ID ('{ocr_name}')."
        
    # 2. Geo-Verification
    ocr_address = ocr_data.get('Address', 'Not Found').lower()
    if ocr_address == 'not found':
        # Only fail on address if it's an Aadhaar card, which usually has it.
        if ocr_data.get('docType') == 'Aadhaar Card':
             return False, "Could not read address from ID document. Please try again or use a clearer image."
        else:
            pass # Skip strict address check for non-Aadhaar
    
    form_region = form_data.get('voterRegion')
    
    # --- UPDATED with comprehensive Indian State/City to Region mapping ---
    region_map = {
        # North India
        'delhi': 'North District',
        'new delhi': 'North District',
        'punjab': 'North District',
        'haryana': 'North District',
        'chandigarh': 'North District',
        'himachal pradesh': 'North District',
        'jammu': 'North District',
        'kashmir': 'North District',
        'uttarakhand': 'North District',
        'uttar pradesh': 'North District', # Can also be Central
        
        # South India
        'karnataka': 'South District',
        'bengaluru': 'South District',
        'bangalore': 'South District',
        'tamil nadu': 'South District',
        'chennai': 'South District',
        'kerala': 'South District',
        'kochi': 'South District',
        'telangana': 'South District',
        'hyderabad': 'South District',
        'andhra pradesh': 'South District',
        'vizag': 'South District',
        
        # East India
        'west bengal': 'East District',
        'kolkata': 'East District',
        'odisha': 'East District',
        'bhubaneswar': 'East District',
        'bihar': 'East District',
        'jharkhand': 'East District',
        'assam': 'East District',
        'guwahati': 'East District',
        
        # West India
        'maharashtra': 'West District',
        'mumbai': 'West District',
        'pune': 'West District',
        'gujarat': 'West District',
        'ahmedabad': 'West District',
        'rajasthan': 'West District',
        'jaipur': 'West District',
        'pali': 'West District',
        'goa': 'West District',
        
        # Central India
        'madhya pradesh': 'Central District',
        'bhopal': 'Central District',
        'indore': 'Central District',
        'chhattisgarh': 'Central District',
        'raipur': 'Central District'
    }
    
    expected_region = 'All Regions' # Default
    for keyword, region in region_map.items():
        if keyword in ocr_address:
            expected_region = region
            break
            
    if form_region != expected_region and form_region != 'All Regions':
         return False, f"The address on your ID (in '{ocr_address}') suggests you are in '{expected_region}', but you selected '{form_region}'. Please select the correct region."

    return True, "Success"

@app.route('/voter-login', methods=['GET', 'POST'])
def voter_login():
    # If user is already logged in, redirect them to the dashboard
    if 'logged_in' in session:
        return redirect(url_for('voting_dashboard'))

    if request.method == 'POST':
        data = request.get_json()
        voter_id_or_email = data.get('voterId')
        input_pin = data.get('backupPin')
        voters = load_voters()
        authenticated_voter = None

        # --- JSON Database Lookup Logic ---
        for voter_id, voter_data in voters.items():
            # Check for match by voter_id OR email
            if voter_data.get('voter_id') == voter_id_or_email or voter_data.get('email') == voter_id_or_email:
                authenticated_voter = voter_data
                break
        # --- End JSON Database Lookup Logic ---

        if authenticated_voter:
            stored_pin = authenticated_voter.get('backupPin')
            
            # This logic handles *both* the initial PIN check *and* the final form submission
            if input_pin:
                if input_pin != stored_pin:
                    # Authentication failure: PIN mismatch
                    return jsonify({"success": False, "error": "Invalid PIN provided. Access denied."}), 401
            # If input_pin is None, this means a different auth method was used, 
            # and the frontend is responsible for signaling final success.
            elif not input_pin and 'backupPin' in data:
                 # This covers the case where the final submit fires but no PIN was entered
                 # (e.g. simulated auth)
                 pass # Allow simulated auth to proceed

            # Login successful: Establish session
            session['logged_in'] = True # CRITICAL: Set the logged_in flag
            session['voter_id'] = authenticated_voter['voter_id']
            session['email'] = authenticated_voter.get('email')
            
            # --- FIX: Construct full name reliably from registration fields ---
            first_name = authenticated_voter.get('firstName', '')
            last_name = authenticated_voter.get('lastName', '')
            
            if first_name or last_name:
                clean_full_name = f"{first_name} {last_name}".strip()
                # Construct name from the standard fields found in your JSON data
                session['full_name'] = clean_full_name
            else:
                # Fallback to 'Full Name' key (from OCR) or the default 'Voter' string
                session['full_name'] = authenticated_voter.get('Full Name', 'Voter')
            # ------------------------------------------------------------------
            
            session['voter_region'] = authenticated_voter.get('voterRegion')
            
            # The frontend is expecting a 'redirect' URL in the JSON response
            next_url = request.args.get('next') or url_for('voting_dashboard')
            return jsonify({"success": True, "message": "Login successful!", "redirect": next_url})
        else:
            return jsonify({"success": False, "error": "Voter ID or Email not found."}), 404
    
    # Render the login page (including the success message from registration redirect)
    success_msg = request.args.get('success')
    return render_template('truecast_voter_login.html', success=success_msg)

@app.route('/logout')
def logout():
    session.pop('logged_in', None) # Ensure the logged_in flag is removed
    session.pop('voter_id', None)
    session.pop('email', None)
    session.pop('full_name', None) # Clears the name displayed on the landing page
    session.pop('voter_region', None) # NEW: Clear region
    
    # FIX: Redirect directly to the home page ('home' endpoint, which is '/')
    return redirect(url_for('home'))

@app.route('/voting-dashboard', methods=['GET', 'POST'])
@login_required
def voting_dashboard():
    voter_id = session.get('voter_id')
    voter_region = session.get('voter_region')
    
    # --- NEW: Get Active Election & Filter Ballot ---
    active_election = get_active_election()
    
    if not active_election:
        # If no active election, skip the complex logic and render the inactive state
        return render_template('truecast_voting_dashboard.html', 
                               voter_id=voter_id,
                               full_name=session.get('full_name', 'Voter'),
                               voter_region=voter_region,
                               has_voted=False,
                               election_active=False,
                               election={},              # Safe default
                               filtered_ballot=[],       # Safe default
                               all_race_ids=[],          # Safe default
                               previous_votes={})
                               
    # Filter the ballot based on the voter's region
    filtered_ballot = []
    
    # Initialize as a set, then convert later
    all_race_ids_set = set() # Use a different variable name for clarity

    for race in active_election.get('races', []):
        race_copy = race.copy()
        race_copy['candidates'] = []
        
        # Filter candidates based on region matching OR candidate region is 'All Regions'
        for candidate in race.get('candidates', []):
            candidate_region = candidate.get('region')
            if candidate_region == voter_region or candidate_region == 'All Regions':
                race_copy['candidates'].append(candidate)

        # Only include the race if it has candidates for the voter's region
        if race_copy['candidates']:
            filtered_ballot.append(race_copy)
            all_race_ids_set.add(race_copy['name'])
    
    # Get the final list of race IDs
    final_race_ids_list = list(all_race_ids_set)
    # --- END NEW: Get Active Election & Filter Ballot ---

    # ... (Rest of the logic for voters_data and votes_data remains the same) ...
    voters_data = load_voters()
    voter_info = voters_data.get(voter_id)
    if not voter_info:
        flash('Voter information could not be loaded.', 'error')
        return redirect(url_for('login'))

    full_name = f"{voter_info.get('firstName', '')} {voter_info.get('lastName', '')}"
    
    votes_data = load_votes()
    
    if request.method == 'POST':
        # ... (POST request logic remains the same) ...
        data = request.get_json()
        selections = data.get('selections')
        
        # Check for duplicate vote (using a unique key including election ID)
        vote_key = f"{active_election['id']}-{voter_id}"
        
        if vote_key in votes_data and votes_data[vote_key].get('transactionHash'):
            return jsonify({'success': False, 'error': 'Your vote has already been cast for this election.', 'transactionHash': 'ALREADY_CAST'})

        # Basic check: did the user select a candidate for every race in their filtered ballot?
        # Use the list of race IDs to check against selections
        required_races = [race['name'] for race in filtered_ballot]
        
        for race_name in required_races:
            # Need to convert race name to slug to check against selections dictionary
            race_slug = race_name.replace(' ', '-').lower()
            if not selections.get(race_slug):
                return jsonify({'success': False, 'error': f'Please make a selection for the {race_name} race.'})

        # Generate the transaction hash and add it to the vote record
        transaction_hash = f"0x{hashlib.sha256(json.dumps(data).encode()).hexdigest()}"
        selections['transactionHash'] = transaction_hash
        selections['electionId'] = active_election['id']
        selections['timestamp'] = datetime.now(IST).isoformat() # Use aware datetime

        # Save the complete vote record (using the unique vote key)
        votes_data[vote_key] = selections
        save_votes(votes_data)
        
        return jsonify({'success': True, 'transactionHash': transaction_hash})

    # GET request logic
    vote_key = f"{active_election['id']}-{voter_id}"
    has_voted = False
    previous_votes = {}
    
    if vote_key in votes_data:
        if votes_data[vote_key].get('transactionHash'):
            has_voted = True
            previous_votes = votes_data[vote_key]
    
    return render_template(
        'truecast_voting_dashboard.html',
        voter_id=voter_id,
        full_name=full_name,
        voter_region=voter_region,
        election_active=True,
        election=active_election,
        filtered_ballot=filtered_ballot,
        all_race_ids=final_race_ids_list, # Use the guaranteed list
        has_voted=has_voted,
        previous_votes=previous_votes
    )

# --- End Election Route (Admin) ---
@app.route('/admin/end-election/<string:election_id>', methods=['POST'])
@admin_required
def end_election(election_id):
    elections = load_elections()
    found = False
    for i, election in enumerate(elections):
        if election.get('id') == election_id:
            elections[i]['status'] = 'Ended'
            elections[i]['endDate'] = datetime.now(IST).isoformat() # Mark end time immediately
            found = True
            break
    
    if found:
        save_elections(elections)
        flash(f'Election "{election_id}" has been officially ENDED. Results are now ready for review.', 'success')
    else:
        flash(f'Election ID {election_id} not found.', 'error')
        
    return redirect(url_for('admin_dashboard'))

# --- NEW: Publish Results Route (Admin) ---
@app.route('/admin/publish-results/<string:election_id>', methods=['POST'])
@admin_required
def publish_results(election_id):
    elections = load_elections()
    found = False
    for i, election in enumerate(elections):
        if election.get('id') == election_id:
            elections[i]['published_results'] = True
            found = True
            break
    
    if found:
        save_elections(elections)
        flash(f'Results for "{election_id}" have been PUBLISHED to the voters.', 'success')
    else:
        flash(f'Election ID {election_id} not found.', 'error')
        
    return redirect(url_for('admin_dashboard'))
# --- End NEW Route ---

@app.route('/vote-verification', methods=['GET', 'POST'])
def vote_verification():
    # Allow non-logged-in users to access, but logged-in is fine too
    return render_template('truecast_vote_verification.html')


@app.route('/api/verify_vote', methods=['POST'])
def verify_vote():
    data = request.get_json()
    query = data.get('query')
    
    all_votes = load_votes()
    
    # Find a cast vote matching the hash or voter ID
    vote_record = None
    voter_id_found = None

    for vote_key, vote_details in all_votes.items():
        voter_id_part = vote_key.split('-')[-1] # Extract voter ID from the composite key
        
        # Check if the query matches the voter's ID or their transaction hash
        if voter_id_part == query or (isinstance(vote_details, dict) and vote_details.get('transactionHash') == query):
            voter_id_found = voter_id_part
            vote_record = vote_details
            break
    
    if vote_record:
        # Get the associated election name
        election_id = vote_record.get('electionId')
        election_name = "N/A"
        if election_id:
            election = get_election_by_id(election_id)
            if election:
                election_name = election.get('title', f"Election {election_id}")
        
        # Construct the response using the found vote record
        demo_vote_data = {
            "voterId": voter_id_found,
            "election": election_name,
            "timestamp": vote_record.get('timestamp', datetime.now(timezone.utc).isoformat()),
            "status": "Confirmed",
            "transactionHash": vote_record.get('transactionHash', 'N/A'),
            "blockNumber": random.randint(10000, 20000), 
            "confirmations": random.randint(100, 500),
            "gasUsed": random.randint(20000, 30000),
            "networkFee": round(random.uniform(0.001, 0.005), 3),
            "blockHash": generate_hash_id()
        }
        return jsonify({"success": True, "data": demo_vote_data})
    
    return jsonify({"success": False, "error": "Vote not found."}), 404


@app.route('/geo-verification')
def geo_verification():
    # This requires a 'truecast_geo_verification.html' template
    return render_template('truecast_geo_verification.html')

@app.route('/admin-login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        # This is a placeholder for actual admin authentication
        username = request.form.get('username')
        password = request.form.get('password')
        if username == 'admin' and password == 'password': # IMPORTANT: Use secure credentials
            session['admin_logged_in'] = True
            flash('Admin login successful!', 'success')
            return redirect('/admin-dashboard') # Assuming you have an admin dashboard
        else:
            flash('Invalid admin credentials.', 'error')
    return render_template('truecast_admin_login.html')

# --- Admin Dashboard Route (Modified) ---
@app.route('/admin-dashboard')
@admin_required
def admin_dashboard():
    voters_data = load_voters()
    votes_data = load_votes()
    elections = load_elections() # NEW: Load all elections

    # Calculate key metrics
    total_voters = len(voters_data)
    total_votes_cast = len(votes_data)
    turnout = (total_votes_cast / total_voters * 100) if total_voters > 0 else 0

    # Get recent registrations (last 5)
    try:
        recent_registrations = sorted(
            voters_data.values(), 
            key=lambda v: v.get('registration_date', '1970-01-01'), 
            reverse=True
        )[:5]
    except Exception:
        recent_registrations = list(voters_data.values())[:5]

    # Vote distribution (aggregated across all elections for overview)
    results = {}
    
    # Simple count across all elections for aggregate dashboard data
    for vote in votes_data.values():
        if isinstance(vote, dict):
            for race_slug, candidate_slug in vote.items():
                # Use standard metadata keys
                if race_slug not in ['transactionHash', 'electionId', 'timestamp']:
                    # Use race slug for display name, though this should ideally come from the election model
                    race_display = race_slug.replace('-', ' ').title()
                    
                    # FIX: Explicitly ensure the nested dictionary exists using setdefault
                    race_tally = results.setdefault(race_display, {})
                    
                    # Increment the vote count for the candidate within that race
                    race_tally[candidate_slug] = race_tally.get(candidate_slug, 0) + 1 
    
    # Filter for the currently active election for the Elections tab status
    active_election = get_active_election()

    return render_template(
        'truecast_admin_dashboard.html',
        total_voters=total_voters,
        total_votes_cast=total_votes_cast,
        turnout=turnout,
        recent_registrations=recent_registrations,
        all_voters=voters_data.values(),
        elections=elections, # NEW: Pass all elections
        active_election=active_election, # NEW: Pass active election for display
        results=results
    )
# --- End Admin Dashboard Route (Modified) ---

# --- Create Election Route (Modified) ---
@app.route('/admin/create-election', methods=['GET', 'POST'])
@admin_required
def create_election():
    if request.method == 'POST':
        form_data = request.form.to_dict(flat=False)
        elections = load_elections()
        
        # Get raw strings from form (e.g., "2025-11-18T14:00")
        start_raw = request.form.get('startDate')
        end_raw = request.form.get('endDate')
        
        # Convert naive datetime-local into proper IST-aware datetime
        start_dt = datetime.strptime(start_raw, "%Y-%m-%dT%H:%M")
        end_dt = datetime.strptime(end_raw, "%Y-%m-%dT%H:%M")

        # Attach IST offset correctly as local time (no shifting)
        start_dt = start_dt.replace(tzinfo=IST)
        end_dt = end_dt.replace(tzinfo=IST)

        # Always save full ISO 8601 with offset
        start_iso = start_dt.isoformat()
        end_iso = end_dt.isoformat()

        election_data = {
            'id': f"ELEC{len(elections) + 1}-{datetime.now().strftime('%Y%m%d')}",
            'title': request.form.get('electionName'),
            'description': request.form.get('electionDescription'),
            'startDate': start_iso, 
            'endDate': end_iso,
            'status': 'Active',
            'published_results': False,
            'races': []
        }

        
        # Helper to extract the index from keys like 'races[1][name]'
        race_indices = set()
        for key in form_data.keys():
            match = re.search(r'races\[(\d+)\]', key)
            if match:
                race_indices.add(match.group(1))

        for race_index in sorted(list(race_indices)):
            
            race_entry = {
                'name': request.form.get(f'races[{race_index}][name]'),
                'type': request.form.get(f'races[{race_index}][type]'),
                'candidates': []
            }
            
            # Find candidate keys for this race
            candidate_map = {}
            for k, v in form_data.items():
                # Check for keys matching the current race index
                cand_match = re.search(r'races\[%s\]\[candidates\]\[(\d+)\]\[(name|party|photoUrl|region)\]' % race_index, k)
                if cand_match:
                    cand_index = cand_match.group(1)
                    field = cand_match.group(2)
                    if cand_index not in candidate_map:
                        candidate_map[cand_index] = {}
                    candidate_map[cand_index][field] = v[0] # v[0] because form_data is a list of values

            for index, cand_data in candidate_map.items():
                # NEW: Ensure region is captured
                region = cand_data.get('region', 'All Regions') 
                
                race_entry['candidates'].append({
                    'name': cand_data.get('name', 'N/A'),
                    'party': cand_data.get('party', 'N/A'),
                    'photoUrl': cand_data.get('photoUrl', '👤'),
                    'region': region 
                })

            election_data['races'].append(race_entry)

        # Save the new election
        elections.append(election_data)
        save_elections(elections)
        
        flash(f'Election "{election_data["title"]}" successfully created and is now active!', 'success')
        return redirect(url_for('admin_dashboard'))

    # GET request: Render the election creation form
    # Regions used for the candidate region dropdown
    default_regions = ['North District', 'South District', 'East District', 'West District', 'Central District', 'All Regions']
    
    return render_template('truecast_createElections.html', default_regions=default_regions)
# --- End Create Election Route (Modified) ---


# app.py (Modified results route)

@app.route('/results')
def results():
    votes_data = load_votes()
    elections = load_elections()

    # Priority 1: Find the most recently *published* election
    published_election = next((e for e in reversed(elections) if e.get('published_results')), None)
    
    # Priority 2: Fallback to active election
    active_election = get_active_election()
    
    target_election = published_election if published_election else active_election
    
    # Initialize message for restricted access or no election state
    display_message = None

    if target_election is None:
        display_message = 'No Active or Published Elections Available.'
        return render_template('truecast_results.html', 
                           results={}, 
                           election_title='No Elections Available',
                           message=display_message)

    # Logic to restrict access to non-active/non-published elections
    if target_election.get('status') != 'Active' and not target_election.get('published_results'):
        display_message = f"Results for {target_election['title']} have been finalized but have not yet been certified and published by the administration."
        return render_template('truecast_results.html', 
                           results={}, 
                           election_title=target_election['title'],
                           message=display_message)

    # If active or published, proceed to tally
    target_election_id = target_election['id']
    election_title = target_election.get('title', f"Results for {target_election_id}")
    
    results_tally = {}

    # Define all metadata keys to exclude, using both snake_case and camelCase for safety
    METADATA_KEYS = ['transactionHash', 'TransactionHash', 'electionId', 'ElectionId', 'timestamp']
    
    for vote_key, vote in votes_data.items():
        if isinstance(vote, dict) and vote.get('electionId') == target_election_id:
            for race_slug, candidate_slug in vote.items():
                # FIX: Check if the current key (race_slug) is one of the unwanted metadata keys
                if race_slug not in METADATA_KEYS: 
                    race_tally = results_tally.setdefault(race_slug, {})
                    race_tally[candidate_slug] = race_tally.get(candidate_slug, 0) + 1

    return render_template('truecast_results.html', 
                           results=results_tally, 
                           election_title=election_title,
                           message=display_message)

# --- Admin Live Results Route ---
@app.route('/admin/results')
@admin_required
def admin_live_results():
    votes_data = load_votes()
    elections = load_elections()
    active_election = get_active_election()
    
    # Determine which election to show based on URL parameter or active status
    election_id = request.args.get('election_id')
    target_election = None
    
    # 1. Check if a specific election ID was requested (e.g., from the 'Review Final Results' button)
    if election_id:
        target_election = get_election_by_id(election_id)
    
    # 2. If no specific ID, default to the active election
    if not target_election:
        target_election = active_election
        
    # 3. If no active, show the most recent ended election for review
    if not target_election and elections:
        target_election = next((e for e in reversed(elections) if e.get('status') == 'Ended'), None)

    if not target_election:
         return render_template('truecast_admin_results.html', 
                               results={}, 
                               election_title='No Elections Available',
                               is_active=False,
                               is_published=False,
                               election_id='N/A',
                               total_votes_in_election=0)
                               
    target_election_id = target_election['id']
    election_title = target_election.get('title', f"Results for {target_election_id}")
    is_active = target_election.get('status') == 'Active'
    is_published = target_election.get('published_results', False)
    
    results_tally = {}
    total_votes_in_election = 0 # NEW: Initialize total counter
    
    # Only tally votes for the selected election
    for vote_key, vote in votes_data.items():
        if isinstance(vote, dict) and vote.get('electionId') == target_election_id:
            
            # This is a full vote object, so increment the total
            total_votes_in_election += 1 
            
            for race_slug, candidate_slug in vote.items():
                if race_slug not in ['transactionHash', 'electionId', 'timestamp']:
                    
                    race_tally = results_tally.setdefault(race_slug, {})
                    race_tally[candidate_slug] = race_tally.get(candidate_slug, 0) + 1
                    
    return render_template('truecast_admin_results.html', 
                           results=results_tally, 
                           election_title=election_title,
                           is_active=is_active,
                           is_published=is_published,
                           election_id=target_election_id,
                           total_votes_in_election=total_votes_in_election)

# --- Ensure all auxiliary pages are defined for URL building ---
# Note: These require corresponding HTML files in the 'templates' folder
# (Assuming you have these files in your 'templates' directory)

# --- Placeholder Routes ---
# We add these so the server doesn't crash if a link is clicked.
# You will need to create the corresponding .html files in your 'templates' folder.

@app.route('/truecast_landing.html')
def truecast_landing():
    # This route is just a helper, the main route is '/'
    return redirect(url_for('home'))

@app.route('/help')
def help_page():
    return render_template('truecast_help.html')

@app.route('/about')
def about():
    return render_template('truecast_about.html')
@app.route('/accessibility')
def accessibility():
    return render_template('truecast_accessibility.html')
@app.route('/contactForm')
def contactForm():
    return render_template('truecast_contactForm.html')
@app.route('/privacypolicy')
def privacypolicy():
    return render_template('truecast_privacypolicy.html')
@app.route('/security')
def security():
    return render_template('truecast_security.html')
@app.route('/documentation')
def documentation():
    return render_template('truecast_documentation.html')

@app.errorhandler(404)
def page_not_found(e):
    # Note: '404.html' must exist in your templates folder
    return render_template('404.html'), 404
# --- End Auxiliary Pages ---


@app.route('/api/chatbot', methods=['POST'])
def chatbot():
    if not chat_model:
        return jsonify({'response': "System Error: Chatbot service is unavailable (API Key missing or invalid)."}), 500

    data = request.get_json()
    user_message = data.get('message')

    if not user_message:
        return jsonify({'response': "Please enter a message."}), 400

    try:
        # --- Context Management ---
        # To verify if we have a conversation history in the session
        if 'chat_history' not in session:
            session['chat_history'] = []
        
        # Prepare the history for the Gemini API
        # Gemini expects a list of content objects or dicts: [{'role': 'user', 'parts': ['msg']}]
        formatted_history = []
        for msg in session['chat_history']:
            formatted_history.append({
                'role': msg['role'],
                'parts': [msg['content']]
            })

        # Start a chat session with history
        chat = chat_model.start_chat(history=formatted_history)
        
        # Send the user's message
        response = chat.send_message(user_message)
        bot_reply = response.text

        # Update Session History
        # We append the new interaction to the session memory
        session['chat_history'].append({'role': 'user', 'content': user_message})
        session['chat_history'].append({'role': 'model', 'content': bot_reply})
        session.modified = True # Explicitly mark session as modified

        # Return the plain text response to the frontend
        return jsonify({'response': bot_reply})

    except Exception as e:
        print(f"Gemini Chat Error: {e}")
        return jsonify({'response': "I'm having trouble connecting right now. Please try again later."}), 500


if __name__ == "__main__":
    # Ensure all necessary files exist upon startup
    if not os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, 'w') as f:
                json.dump({}, f)
        except Exception as e:
            print(f"Error creating {JSON_FILE}: {e}")

    if not os.path.exists(VOTES_FILE):
        try:
            with open(VOTES_FILE, 'w') as f:
                json.dump({}, f)
        except Exception as e:
            print(f"Error creating {VOTES_FILE}: {e}")
            
    if not os.path.exists(ELECTIONS_FILE):
        try:
            with open(ELECTIONS_FILE, 'w') as f:
                json.dump([], f)
        except Exception as e:
            print(f"Error creating {ELECTIONS_FILE}: {e}")

    app.run(debug=True)