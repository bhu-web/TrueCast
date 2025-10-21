import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from datetime import datetime
import random
import string
import re
import json
import hashlib
from functools import wraps

# NOTE: document_extracter is assumed to be present for the OCR redirect logic
try:
    from document_extracter import extract_text_from_file 
except ImportError:
    print("Warning: 'document_extracter' not found. OCR simulation will be used.")
    def extract_text_from_file(*args, **kwargs):
        raise NotImplementedError("document_extracter module is missing.")


# --- Configuration & File Setup ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = os.path.join(BASE_DIR, 'voters.json') # Stores voter profiles
VOTES_FILE = os.path.join(BASE_DIR, 'votes.json') # Stores cast vote records (NEW)
SECRET_KEY = os.environ.get('SECRET_KEY', 'a_very_secret_key_for_truecast_sessions') 

app = Flask(__name__)
app.secret_key = SECRET_KEY

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
        
# --- Vote JSON Functions ---
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
# --- End NEW Vote JSON Functions ---

# --- Authentication Decorator (For Protecting Routes) ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'voter_id' not in session:
            # Redirect to login if user is not in session
            return redirect(url_for('voter_login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# --- Helper Functions ---
def generate_hash_id(length=64):
    return '0x' + ''.join(random.choices(string.hexdigits.lower(), k=length))

def parse_ocr_text(text):
    """
    CORRECTED REGEX for clean extraction.
    """
    patterns = {
        'Full Name': r'(?:FULL NAME|NAME)[:\s]*(.*?)(?:DATE OF BIRTH|DOB|ADDRESS|ID NUMERO)', 
        'Date of Birth': r'(?:DATE OF BIRTH|DOB)[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{4})', 
        'ID Number': r'(?:ID NUMBER|ID NUMERO|NO)[:.\s]*([A-Z0-9]{6,20})', 
        'Country': r'(?:COUNTRY|PAYS)[:\s]*(.*?)(?:\n|ID NUMBER|ID NUMERO)', 
    }
    
    parsed_data = {}
    for field, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE | re.DOTALL) 
        if match:
            value = match.group(1).strip()
            
            if field == 'Full Name':
                value = re.sub(r'ID CARD|VOTER REGISTRATION DOCUMENT|ID NUMBER.*', '', value, flags=re.IGNORECASE).strip()
                value = ' '.join(value.split())
            
            parsed_data[field] = value
        else:
            parsed_data[field] = 'Not Found'
            
    return parsed_data

# In-memory state for demonstration
results_approved = False
SIMULATED_OCR_DATA = {
    "sample-id-1234.jpg": 
        "ID CARD\nCOUNTRY: UNITED STATES\nID NUMBER: US123456789\nFULL NAME: JOHN MICHAEL SMITH\nDATE OF BIRTH: 01/05/1990\nADDRESS: 123 Demo St, New York, NY 10001",
    "sample-id-5678.pdf": 
        "VOTER REGISTRATION DOCUMENT\nFULL NAME: JANE M. DOE\nDOB: 12-15-1985\nID NUMERO: JMD851215\nCOUNTRY: CANADA"
}

# --- Routes ---

@app.route('/')
def home():
    return render_template('truecast_landing.html')

@app.route('/api/ocr_process', methods=['POST'])
def ocr_process():
    files = request.files.getlist('idDocument')
    if not files or not files[0].filename:
        return jsonify({"success": False, "error": "No document uploaded."}), 400

    upload = files[0]
    simulated_raw_text = SIMULATED_OCR_DATA.get(
        upload.filename, 
        SIMULATED_OCR_DATA["sample-id-1234.jpg"]
    )

    try:
        parsed_data = parse_ocr_text(simulated_raw_text)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    return redirect(url_for(
        'voter_register',
        success_ocr="true",
        parsed_data=json.dumps(parsed_data),
        ocr_results=json.dumps([simulated_raw_text])
    ))

@app.route('/voter-register', methods=['GET', 'POST'])
def voter_register():
    if request.method == 'POST':
        data = request.form.to_dict()
        
        # --- JSON Database Insertion Logic (Final Save) ---
        voters = load_voters()
        voter_id = f"VS{datetime.now().year}{random.randint(100000, 999999)}"

        # Prepare the data
        data['voter_id'] = voter_id
        data['registration_date'] = datetime.utcnow().isoformat()
        data['status'] = 'Active' 
        data['backupPin'] = data.get('backupPin', '000000') 

        # Add to the dictionary and save
        voters[voter_id] = data
        save_voters(voters)
        # --- End JSON Database Insertion Logic ---
        
        success_msg = f"Registration complete! Your ID is {voter_id}. Use this and your PIN to log in."
        # After saving, we now redirect to the login page for the final step.
        return redirect(url_for('voter_login', success=success_msg))

    # Handle GET request (initial load or redirect after OCR)
    success_ocr = request.args.get('success_ocr')
    success_msg = request.args.get('success')
    error_msg = request.args.get('error')

    parsed_data = {}
    ocr_results = []
    
    if success_ocr:
        try:
            parsed_data = json.loads(request.args.get('parsed_data', '{}'))
            ocr_results = json.loads(request.args.get('ocr_results', '[]'))
            success_msg = "Identity document processed successfully. Please review."
        except json.JSONDecodeError:
            error_msg = error_msg or "Error decoding OCR data after processing."

    return render_template(
        'truecast_voter_register.html', 
        success=success_msg, 
        error=error_msg,
        parsed_data=parsed_data,
        ocr_results=ocr_results
    )

@app.route('/voter-login', methods=['GET', 'POST'])
def voter_login():
    if request.method == 'POST':
        data = request.get_json()
        voter_id_or_email = data.get('voterId')
        
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
            # Login successful: Establish session
            session['voter_id'] = authenticated_voter['voter_id']
            session['email'] = authenticated_voter['email']
            session['full_name'] = authenticated_voter.get('Full Name', 'Voter')
            
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
    session.pop('voter_id', None)
    session.pop('email', None)
    session.pop('full_name', None)
    return redirect(url_for('voter_login'))


@app.route('/voting-dashboard', methods=['GET', 'POST'])
@login_required
def voting_dashboard():
    voter_id = session.get('voter_id')
    if not voter_id:
        flash('Voter not found in session. Please log in again.', 'error')
        return redirect(url_for('login'))

    voters_data = load_voters()
    voter_info = voters_data.get(voter_id)
    if not voter_info:
        flash('Voter information could not be loaded.', 'error')
        return redirect(url_for('login'))

    full_name = f"{voter_info.get('firstName', '')} {voter_info.get('lastName', '')}"
    
    votes_data = load_votes()
    
    if request.method == 'POST':
        data = request.get_json()
        selections = data.get('selections')
        
        if voter_id in votes_data and any(votes_data[voter_id].values()):
            return jsonify({'success': False, 'error': 'Your vote has already been cast.', 'transactionHash': 'ALREADY_CAST'})

        if not all(selections.values()):
            return jsonify({'success': False, 'error': 'Please make a selection for all items.'})

        # Generate the transaction hash and add it to the vote record
        transaction_hash = f"0x{hashlib.sha256(json.dumps(data).encode()).hexdigest()}"
        selections['transactionHash'] = transaction_hash

        # Add the complete vote record (including hash) to the data
        votes_data[voter_id] = selections
        save_votes(votes_data)
        
        return jsonify({'success': True, 'transactionHash': transaction_hash})

    # GET request logic
    has_voted = False
    previous_votes = {}
    
    if voter_id in votes_data:
        # A user has voted only if their vote entry is not empty
        if any(votes_data[voter_id].values()):
            has_voted = True
            previous_votes = votes_data[voter_id]
        else:
            # Entry exists but is empty, meaning they are registered to vote but haven't yet.
            has_voted = False
    
    return render_template(
        'truecast_voting_dashboard.html',
        voter_id=voter_id,
        full_name=full_name,
        has_voted=has_voted,
        previous_votes=previous_votes
    )


@app.route('/vote-verification', methods=['GET', 'POST'])
@login_required
def vote_verification():
    return render_template('truecast_vote_verification.html')

@app.route('/api/verify_vote', methods=['POST'])
def verify_vote():
    data = request.get_json()
    query = data.get('query')
    
    all_votes = load_votes()
    
    # Find a cast vote matching the hash or voter ID
    vote_record = None
    voter_id_found = None

    for voter_id, vote_details in all_votes.items():
        # Check if the query matches the voter's ID or their transaction hash
        if voter_id == query or (isinstance(vote_details, dict) and vote_details.get('transactionHash') == query):
            voter_id_found = voter_id
            vote_record = vote_details
            break
    
    if vote_record:
        # Construct the response using the found vote record
        demo_vote_data = {
            "voterId": voter_id_found,
            "election": "2025 General Election",
            "timestamp": datetime.now().isoformat(), # Placeholder timestamp
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

@app.route('/admin-dashboard')
@login_required 
def admin_dashboard():
    return render_template('truecast_admin_dashboard.html')

@app.route('/api/admin/approve-results', methods=['POST'])
def approve_results():
    global results_approved
    results_approved = True
    print("Admin has approved the results.")
    return jsonify({"success": True, "message": "Results approved successfully!"})

@app.route('/results')
def results():
    votes_data = load_votes()
    
    # Simple logic to count votes
    results = {
        'mayor': {},
        'council': {},
        'proposition': {}
    }
    
    for vote in votes_data.values():
        if isinstance(vote, dict):
            for race, candidate in vote.items():
                if race in results:
                    results[race][candidate] = results[race].get(candidate, 0) + 1

    return render_template('truecast_results.html', results=results)


@app.route('/api/results')
def get_results():
    global results_approved
    
    if results_approved:
        demo_results = {
            "Mayor of Central City": {
                "Sarah Johnson": 1250,
                "Michael Chen": 980,
                "Elena Rodriguez": 720
            },
            "City Council - District 3": {
                "David Kim": 2100,
                "Maria Santos": 1500
            },
            "Proposition A - School Funding": {
                "YES": 2500,
                "NO": 1000
            }
        }
        return jsonify({"success": True, "results": demo_results})
    else:
        return jsonify({"success": False, "message": "Results are not yet approved by admin."}), 200

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
    return render_template('404.html'), 404

if __name__ == "__main__":
    if not os.path.exists(VOTES_FILE):
        try:
            with open(VOTES_FILE, 'w') as f:
                json.dump({}, f) # Ensure it's created as a dictionary
            print(f"Created empty {VOTES_FILE}")
        except Exception as e:
            print(f"Error creating {VOTES_FILE}: {e}")
            
    app.run(debug=True)