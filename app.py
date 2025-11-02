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
VOTES_FILE = os.path.join(BASE_DIR, 'votes.json') # Stores cast vote records
ELECTIONS_FILE = os.path.join(BASE_DIR, 'elections.json') # Stores election details (NEW)
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
    """Returns the currently active election, or None."""
    elections = load_elections()
    # Note: using utcnow() for compatibility with existing code.
    now = datetime.utcnow()
    
    for election in elections:
        # Check for valid date formats before parsing
        try:
            start_time = datetime.fromisoformat(election.get('startDate'))
            end_time = datetime.fromisoformat(election.get('endDate'))
        except (ValueError, TypeError):
            continue 
            
        status = election.get('status', 'Active')

        if status == 'Active' and start_time <= now and end_time > now:
            return election
    
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
    # Pass session status and user data to the landing page template
    return render_template('truecast_landing.html',
                           logged_in=session.get('logged_in', False),
                           full_name=session.get('full_name', ''))

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

# app.py (Modified voter_register route)

# app.py (Modified voter_register route)

@app.route('/voter-register', methods=['GET', 'POST'])
def voter_register():
    # --- Template variable setup (MUST run for both GET and failed POST) ---
    active_election = get_active_election()
    available_regions = set()
    if active_election:
        for race in active_election.get('races', []):
            for candidate in race.get('candidates', []):
                if candidate.get('region'):
                    available_regions.add(candidate['region'])
    
    if not available_regions:
        available_regions = {
            'North District', 'South District', 'East District', 
            'West District', 'Central District', 'All Regions'
        }
    
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
                    parsed_data=parsed_data,
                    ocr_results=ocr_results,
                    available_regions=sorted_regions
                ) 
        # --- END DUPLICATE CHECK LOGIC ---

        # If unique, proceed with saving (logic remains unchanged)
        voter_id = f"VS{datetime.now().year}{random.randint(100000, 999999)}"
        data['voter_id'] = voter_id
        data['registration_date'] = datetime.utcnow().isoformat()
        data['status'] = 'Active' 
        data['backupPin'] = data.get('backupPin', '000000') 
        voters[voter_id] = data
        save_voters(voters)
        flash(f'Registration successful! Your new Voter ID is {voter_id}. Please log in.', 'success')
        return redirect(url_for('voter_login', success='true'))

    # --- GET request logic (Initial load OR load after OCR redirect) ---
    parsed_data_json = request.args.get('parsed_data', '{}')
    ocr_results_json = request.args.get('ocr_results', '[]')
    
    try:
        if 'success_ocr' in request.args:
            parsed_data = json.loads(parsed_data_json)
            ocr_results = json.loads(ocr_results_json)
    except json.JSONDecodeError:
        pass

    return render_template(
        'truecast_voter_register.html',
        parsed_data=parsed_data,
        ocr_results=ocr_results,
        available_regions=sorted_regions
    )

@app.route('/voter-login', methods=['GET', 'POST'])
def voter_login():
    # If user is already logged in, redirect them to the dashboard
    if 'logged_in' in session:
        return redirect(url_for('voting_dashboard'))

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
            session['logged_in'] = True # CRITICAL: Set the logged_in flag
            session['voter_id'] = authenticated_voter['voter_id']
            session['email'] = authenticated_voter.get('email')
            
            # --- FIX: Construct full name reliably from registration fields ---
            first_name = authenticated_voter.get('firstName', '')
            last_name = authenticated_voter.get('lastName', '')
            
            if first_name or last_name:
                # Construct name from the standard fields found in your JSON data
                session['full_name'] = f"{first_name} {last_name}".strip()
            else:
                # Fallback to 'Full Name' key (from OCR) or the default 'Voter' string
                session['full_name'] = authenticated_voter.get('Full Name', 'Voter')
            # ------------------------------------------------------------------
            
            session['voter_region'] = authenticated_voter.get('voterRegion')
            
            # The frontend is expecting a 'redirect' URL in the JSON response
            next_url = request.args.get('next') or url_for('voting_dashboard')
            return jsonify({"success": True, "message": "Login successful!", "redirect": next_url})
    
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
        selections['timestamp'] = datetime.utcnow().isoformat()

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
            elections[i]['endDate'] = datetime.utcnow().isoformat() # Mark end time immediately
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
            "timestamp": vote_record.get('timestamp', datetime.now().isoformat()),
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
                if race_slug not in ['transactionhash', 'electionid', 'timestamp']:
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
        
        # Structure the data
        election_data = {
            'id': f"ELEC{len(elections) + 1}-{datetime.now().strftime('%Y%m%d')}",
            'title': request.form.get('electionName'),
            'description': request.form.get('electionDescription'),
            'startDate': request.form.get('startDate'),
            'endDate': request.form.get('endDate'),
            'status': 'Active', # Default new election status
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
                    candidate_map[cand_index][field] = v[0]

            for index, cand_data in candidate_map.items():
                # NEW: Ensure region is captured
                region = cand_data.get('region', 'All Regions') 
                # Slugify candidate name for use as vote value
                candidate_name_slug = cand_data.get('name', 'N/A').replace(' ', '-').lower()
                
                race_entry['candidates'].append({
                    'name': cand_data.get('name', 'N/A'),
                    'slug': candidate_name_slug,
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
                               election_id='N/A')
                               
    target_election_id = target_election['id']
    election_title = target_election.get('title', f"Results for {target_election_id}")
    is_active = target_election.get('status') == 'Active'
    is_published = target_election.get('published_results', False)
    
    results_tally = {}
    total_votes_in_election = 0 # NEW: Initialize total counter
    # Only tally votes for the selected election
    for vote_key, vote in votes_data.items():
        if isinstance(vote, dict) and vote.get('electionId') == target_election_id:
            for race_slug, candidate_slug in vote.items():
                if race_slug not in ['transactionhash', 'electionid', 'timestamp']:
                    
                    race_tally = results_tally.setdefault(race_slug, {})
                    race_tally[candidate_slug] = race_tally.get(candidate_slug, 0) + 1
                    
                    total_votes_in_election += 1 # CRITICAL: Increment the total for every valid vote
    return render_template('truecast_admin_results.html', 
                           results=results_tally,
                           election_title=election_title,
                           is_active=is_active,
                           is_published=is_published,
                           election_id=target_election_id,
                           total_votes_in_election=total_votes_in_election)

# --- Ensure all auxiliary pages are defined for URL building ---
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


if __name__ == "__main__":
    # Ensure all necessary files exist upon startup
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