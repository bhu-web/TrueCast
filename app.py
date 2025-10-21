import os
from flask import Flask, render_template, request, jsonify, redirect, url_for
import random
import string
from datetime import datetime
import re # NEW: Import for regular expressions (used in OCR parsing)
import json # NEW: Import for handling JSON data for structured output
from document_extracter import extract_text_from_file  # add this import



# In-memory state for demonstration
# This will be reset every time the server restarts
results_approved = False
# MODIFIED: In-memory store for simulated OCR text by file hash (since we can't save the actual file)
# In a real app, this would be a full OCR service call
SIMULATED_OCR_DATA = {
    # MODIFIED: Updated the raw text format to better match the regex in parse_ocr_text
    "sample-id-1234.jpg": 
        "ID CARD\nCOUNTRY: UNITED STATES\nID NUMBER: US123456789\nFULL NAME: JOHN MICHAEL SMITH\nDATE OF BIRTH: 01/05/1990\nADDRESS: 123 Demo St, New York, NY 10001",
    "sample-id-5678.pdf": 
        "VOTER REGISTRATION DOCUMENT\nFULL NAME: JANE M. DOE\nDOB: 12-15-1985\nID NUMERO: JMD851215\nCOUNTRY: CANADA"
}
# --- END SIMULATION SETUP ---

app = Flask(__name__)

# Helper function for demo purposes
def generate_hash_id(length=64):
    return '0x' + ''.join(random.choices(string.hexdigits.lower(), k=length))

# NEW FUNCTION: Parses raw OCR text into structured fields
def parse_ocr_text(text):
    """Uses regex patterns to extract key fields from raw OCR text."""
    # MODIFIED: Patterns for demo to extract key voter data
    patterns = {
        'Full Name': r'(?:NAME|FULL NAME|NOM)[:\s]*([A-Z\s\.]{5,50})', 
        'Date of Birth': r'(?:DOB|BIRTH DATE)[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{4})', 
        'ID Number': r'(?:ID NUMBER|NO|NUMERO)[:.\s]*([A-Z0-9]{6,20})', 
        'Country': r'(?:COUNTRY|PAYS)[:\s]*([A-Z\s]{3,20})',
    }
    
    parsed_data = {}
    for field, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            value = match.group(1).strip()
            if field == 'Full Name':
                value = ' '.join(value.split())
            
            parsed_data[field] = value
        else:
            parsed_data[field] = 'Not Found'
            
    return parsed_data

# NEW ROUTE: Simulates the file upload, OCR, and returns parsed data
@app.route('/api/ocr_process', methods=['POST'])
def ocr_process():
    files = request.files.getlist('idDocument')
    if not files or not files[0].filename:
        return jsonify({"success": False, "error": "No document uploaded."}), 400

    upload = files[0]
    filepath = os.path.join("uploads", upload.filename)
    os.makedirs("uploads", exist_ok=True)
    upload.save(filepath)

    try:
        raw_text = extract_text_from_file(filepath, api_key="AIzaSyAbRziB-Q0bgTR4RgolvofxMmsOrinLJB0")
        parsed_data = parse_ocr_text(raw_text)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    return redirect(url_for(
        'voter_register',
        success_ocr="true",
        parsed_data=json.dumps(parsed_data),
        ocr_results=json.dumps([raw_text])
    ))

@app.route('/')
def home():
    return render_template('truecast_landing.html')

# MODIFIED: Updated to handle the results from the OCR simulation 
# via query parameters, as we did in the database version.
@app.route('/voter-register', methods=['GET', 'POST'])
def voter_register():
    if request.method == 'POST':
        # MODIFIED: The form submits to /api/ocr_simulate, so this POST route is simplified
        data = request.form.to_dict()
        
        # Example to show data being saved in a real world scenario
        print(f"Simulating final registration save for: {data.get('email')}")
        
        # After successful final registration, clear params and show success
        return render_template('truecast_voter_register.html', success="Voter registration simulated.")

    # Handle GET request (initial load or redirect after failed/successful OCR)
    success_ocr = request.args.get('success_ocr')
    success_msg = request.args.get('success')
    error_msg = request.args.get('error')

    # Load data passed via query parameters after the OCR step
    parsed_data = {}
    ocr_results = []
    
    if success_ocr:
        try:
            parsed_data = json.loads(request.args.get('parsed_data', '{}'))
            # OCR results is a list containing the raw text
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
        voter_id = data.get('voterId')
        # Demo logic: Simulate login
        if voter_id:
            print(f"Simulating login for voter: {voter_id}")
            return jsonify({"success": True, "message": "Login successful!"})
        else:
            return jsonify({"success": False, "error": "Voter ID not found."}), 404
    return render_template('truecast_voter_login.html')

@app.route('/voting-dashboard', methods=['GET', 'POST'])
def voting_dashboard():
    # MODIFIED: Removed in-memory vote storage to simplify demo, just returns success
    if request.method == 'POST':
        data = request.get_json()
        voter_id = data.get('voterId')
        selections = data.get('selections')
        
        # Demo logic: Simulate vote casting
        print(f"Simulating vote cast by {voter_id} with selections: {selections}")
        
        transaction_hash = generate_hash_id()
        return jsonify({"success": True, "transactionHash": transaction_hash})
    return render_template('truecast_voting_dashboard.html')

@app.route('/vote-verification')
def vote_verification():
    return render_template('truecast_vote_verification.html')

@app.route('/api/verify_vote', methods=['POST'])
def verify_vote():
    # Demo logic for vote verification
    data = request.get_json()
    query = data.get('query')
    print(f"Simulating vote verification for query: {query}")
    
    demo_vote_data = {
        "voterId": "DEMO-VOTER-123",
        "selections": {"President": "Candidate A", "Mayor": "Candidate X"},
        "transactionHash": "0x4b7c8d9e2a3f4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c",
        "blockNumber": 15432,
        "confirmations": 250,
        "timestamp": datetime.now().isoformat(),
        "blockHash": generate_hash_id()
    }
    
    if query:
        return jsonify({"success": True, "data": demo_vote_data})
    
    return jsonify({"success": False, "error": "Vote not found."}), 404

@app.route('/geo-verification')
def geo_verification():
    return render_template('truecast_geo_verification.html')

@app.route('/admin-login')
def admin_login():
    return render_template('truecast_admin_login.html')

@app.route('/admin-dashboard')
def admin_dashboard():
    return render_template('truecast_admin_dashboard.html')

# MODIFIED: Added implementation for the results approval route
@app.route('/api/admin/approve-results', methods=['POST'])
def approve_results():
    global results_approved
    results_approved = True
    print("Admin has approved the results.")
    return jsonify({"success": True, "message": "Results approved successfully!"})

@app.route('/results')
def results():
    return render_template('truecast_results.html')

@app.route('/api/results')
def get_results():
    global results_approved
    if results_approved:
        # Hardcoded demo results
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
        # MODIFIED: Consistent error response for frontend check
        return jsonify({"success": False, "message": "Results are not yet approved by admin."}), 200

@app.route('/help')
def help_page():
    return render_template('truecast_help_faq.html')

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
    # NOTE: You need a truecast_404.html template for this to work
    return render_template('404.html'), 404

if __name__ == "__main__":
    app.run(debug=True)
