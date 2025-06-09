from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from pymongo import MongoClient
from twilio.rest import Client as TwilioClient
from datetime import datetime, timedelta
import os
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import threading
import time
from bson.objectid import ObjectId
from math import radians, cos, sin, asin, sqrt

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(24))

# MongoDB setup
try:
    client = MongoClient(os.getenv('MONGODB_URI', 'mongodb://localhost:27017/'))
    db = client['medicycle']
    users_col = db['users']
    medicines_col = db['medicines']
    community_col = db['community']  # New collection for community shared medicines
except Exception as e:
    print(f"Failed to connect to MongoDB: {e}")
    raise

# Twilio setup
TWILIO_SID = "AC1f84b2e41ddbd3e1c187f11fc893cbf2"
TWILIO_AUTH = "5074d982d14b295d67be78747ecc2ff6"
TWILIO_NUMBER = "+17246095302"

if all([TWILIO_SID, TWILIO_AUTH, TWILIO_NUMBER]):
    twilio_client = TwilioClient(TWILIO_SID, TWILIO_AUTH)
else:
    print("Warning: Twilio credentials not found in environment variables")
    twilio_client = None

def check_expiring_medicines():
    """Check for medicines expiring within 7 days and send notifications"""
    while True:
        try:
            # Get all medicines
            medicines = medicines_col.find({})
            today = datetime.now()
            seven_days_later = today + timedelta(days=7)

            for medicine in medicines:
                exp_date = medicine['exp_date']
                if today <= exp_date <= seven_days_later:
                    # Get user's phone number
                    user = users_col.find_one({'email': medicine['user_email']})
                    if user and user.get('phone'):
                        days_until_expiry = (exp_date - today).days
                        message = f"⚠️ Your medicine '{medicine['name']}' is expiring in {days_until_expiry} days (on {exp_date.date()})."
                        send_whatsapp(user['phone'], message)
                        print(f"Sent expiry notification for {medicine['name']} to {user['phone']}")

        except Exception as e:
            print(f"Error in check_expiring_medicines: {e}")

        # Sleep for 24 hours
        time.sleep(24 * 60 * 60)

# Start the expiry checker in a background thread
expiry_checker = threading.Thread(target=check_expiring_medicines, daemon=True)
expiry_checker.start()

# -------------- ROUTES ----------------

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        try:
            data = request.get_json()
            if not all(k in data for k in ['email', 'password', 'phone']):
                return jsonify({'error': 'Missing required fields'}), 400

            email = data['email']
            password = data['password']
            phone = data['phone']
            latitude = data.get('latitude')
            longitude = data.get('longitude')

            if users_col.find_one({'email': email}):
                return jsonify({'error': 'Email already exists'}), 400

            hashed_password = generate_password_hash(password)
            user_doc = {
                'email': email,
                'password': hashed_password,
                'phone': phone
            }
            if latitude and longitude:
                user_doc['location'] = {
                    'type': 'Point',
                    'coordinates': [float(longitude), float(latitude)]
                }
            users_col.insert_one(user_doc)
            return jsonify({'message': 'User created successfully'}), 201
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        try:
            data = request.get_json()
            if not all(k in data for k in ['email', 'password']):
                return jsonify({'error': 'Missing required fields'}), 400

            user = users_col.find_one({'email': data['email']})
            if user and check_password_hash(user['password'], data['password']):
                session['user_email'] = user['email']
                return jsonify({'message': 'Login successful'}), 200
            return jsonify({'error': 'Invalid credentials'}), 401
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return render_template('login.html')

@app.route('/upload_form')
def upload_form():
    if 'user_email' not in session:
        return redirect('/')
    return render_template('upload_medicine.html')

@app.route('/upload_medicine', methods=['POST'])
def upload_medicine():
    if 'user_email' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        # Validate required fields
        required_fields = ['name', 'category', 'quantity', 'location', 'exp_date']
        if not all(field in request.form for field in required_fields):
            return jsonify({'error': 'Missing required fields'}), 400

        name = request.form['name']
        category = request.form['category']
        quantity = int(request.form['quantity'])
        location = request.form['location']
        exp_date_str = request.form['exp_date']
        exp_date = datetime.strptime(exp_date_str, '%Y-%m-%d')

        # Validate quantity
        if quantity <= 0:
            return jsonify({'error': 'Quantity must be positive'}), 400

        # Handle image upload
        image_path = ""
        image = request.files.get('image')
        if image:
            if not image.filename:
                return jsonify({'error': 'Invalid image file'}), 400
            upload_dir = os.path.join("static", "uploads")
            os.makedirs(upload_dir, exist_ok=True)
            image_path = os.path.join(upload_dir, image.filename)
            image.save(image_path)

        email = session['user_email']
        user = users_col.find_one({'email': email})
        if not user:
            return jsonify({'error': 'User not found'}), 404

        phone = user.get('phone')
        if not phone:
            return jsonify({'error': 'User phone number not found'}), 400

        medicines_col.insert_one({
            "user_email": email,
            "name": name,
            "category": category,
            "quantity": quantity,
            "location": location,
            "exp_date": exp_date,
            "image_path": image_path,
            "created_at": datetime.now()
        })

        # Check expiry and send notification
        if exp_date <= datetime.now() + timedelta(days=7):
            if twilio_client:
                send_whatsapp(phone, f"⚠️ Your medicine '{name}' is expiring on {exp_date.date()}.")
            else:
                print(f"Warning: Medicine '{name}' is expiring on {exp_date.date()} but Twilio is not configured")

        return jsonify({'message': 'Medicine uploaded successfully'}), 201

    except ValueError as e:
        return jsonify({'error': 'Invalid input format'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/get_medicines')
def get_medicines():
    if 'user_email' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        meds = list(medicines_col.find(
            {'user_email': session['user_email']}
        ))
        # Convert ObjectId to string for each medicine
        for med in meds:
            med['_id'] = str(med['_id'])
        return jsonify(meds)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/logout', methods=['POST'])
def logout():
    session.pop('user_email', None)
    return jsonify({'message': 'Logged out successfully'})

@app.route('/community')
def community():
    if 'user_email' not in session:
        return redirect('/login')
    return render_template('community.html')

@app.route('/get_community_medicines')
def get_community_medicines():
    if 'user_email' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        # Get filter parameters
        category = request.args.get('category')
        search = request.args.get('search')
        sort_by = request.args.get('sort_by', 'expiry')
        
        # Build query
        query = {}
        if category:
            query['category'] = category
        if search:
            query['name'] = {'$regex': search, '$options': 'i'}
        
        # Build sort
        sort_options = {
            'expiry': [('exp_date', 1)],
            'name': [('name', 1)],
            'date': [('shared_date', -1)]
        }
        sort = sort_options.get(sort_by, sort_options['expiry'])
        
        # Get medicines
        medicines = list(community_col.find(query, {'_id': 0}).sort(sort))
        return jsonify(medicines)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/share_to_community', methods=['POST'])
def share_to_community():
    if 'user_email' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        data = request.get_json()
        medicine_id = data.get('medicine_id')
        
        # Get the medicine details
        medicine = medicines_col.find_one({'_id': ObjectId(medicine_id)})
        if not medicine:
            return jsonify({'error': 'Medicine not found'}), 404
        
        # Get user details
        user = users_col.find_one({'email': session['user_email']})
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        # Check if medicine is already shared
        existing = community_col.find_one({
            'medicine_id': medicine_id,
            'user_email': session['user_email']
        })
        if existing:
            return jsonify({'error': 'Medicine already shared to community'}), 400
        
        # Add to community collection
        community_medicine = {
            'medicine_id': medicine_id,
            'user_email': session['user_email'],
            'name': medicine['name'],
            'category': medicine['category'],
            'quantity': medicine['quantity'],
            'location': medicine['location'],
            'exp_date': medicine['exp_date'],
            'image_path': medicine['image_path'],
            'shared_by': user.get('name', user['email']),
            'contact_info': user.get('phone', 'Contact information not available'),
            'shared_date': datetime.now()
        }
        
        community_col.insert_one(community_medicine)
        return jsonify({'message': 'Medicine shared to community successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/disposal_guide/<medicine_id>')
def disposal_guide(medicine_id):
    med = medicines_col.find_one({'_id': ObjectId(medicine_id)})
    if not med:
        return "Medicine not found", 404

    # Simple disposal steps based on category
    category = med.get('category', '').lower()
    if category == 'tablet' or category == 'capsule':
        steps = [
            "Remove medicine from original container.",
            "Mix with undesirable substance (e.g., coffee grounds, dirt).",
            "Place in a sealed bag/container.",
            "Throw in household trash."
        ]
    elif category == 'syrup' or category == 'liquid':
        steps = [
            "Pour into a sealable bag with absorbent material (e.g., cat litter).",
            "Seal the bag and throw in household trash."
        ]
    elif category == 'injection':
        steps = [
            "Place in a sharps container.",
            "Do not recap needles.",
            "Take to a pharmacy or authorized collection site."
        ]
    else:
        steps = [
            "Check local guidelines or ask your pharmacist.",
            "Do not flush medicines down the toilet."
        ]

    return render_template('disposal_guide.html', medicine=med, steps=steps)

def haversine(lon1, lat1, lon2, lat2):
    # Calculate the great circle distance between two points on the earth (specified in decimal degrees)
    lon1, lat1, lon2, lat2 = map(float, [lon1, lat1, lon2, lat2])
    # convert decimal degrees to radians
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    # haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    r = 6371  # Radius of earth in kilometers
    return c * r

@app.route('/sos', methods=['POST'])
def sos():
    data = request.get_json()
    medicine_name = data.get('medicine_name')
    location = data.get('location')
    notified = 0
    if location and ',' in location:
        lat, lon = location.split(',')
        # Find all users with a location within 5km
        for user in users_col.find({'location': {'$exists': True}}):
            user_loc = user['location']['coordinates']  # [lon, lat]
            dist = haversine(lon, lat, user_loc[0], user_loc[1])
            if dist <= 5 and user.get('phone'):
                try:
                    send_whatsapp(user['phone'], f"🚨 SOS: Someone nearby needs '{medicine_name}'. If you can help, please respond!")
                    notified += 1
                except Exception as e:
                    print(f"Failed to notify {user['phone']}: {e}")
    return jsonify({'message': f'SOS received for {medicine_name} at {location}. Notified {notified} nearby users.'})

@app.route('/get_leaderboard')
def get_leaderboard():
    try:
        # Aggregate medicines shared by each user
        pipeline = [
            {
                "$group": {
                    "_id": "$user_email",
                    "total_medicines": {"$sum": 1},
                    "medicines": {"$push": {
                        "name": "$name",
                        "category": "$category",
                        "shared_date": "$shared_date"
                    }}
                }
            },
            {
                "$lookup": {
                    "from": "users",
                    "localField": "_id",
                    "foreignField": "email",
                    "as": "user_info"
                }
            },
            {
                "$project": {
                    "email": "$_id",
                    "total_medicines": 1,
                    "medicines": 1,
                    "name": {"$arrayElemAt": ["$user_info.name", 0]},
                    "phone": {"$arrayElemAt": ["$user_info.phone", 0]}
                }
            },
            {
                "$sort": {"total_medicines": -1}
            },
            {
                "$limit": 10  # Get top 10 users
            }
        ]
        
        leaderboard = list(community_col.aggregate(pipeline))
        
        # Format the data
        formatted_leaderboard = []
        for i, user in enumerate(leaderboard, 1):
            formatted_leaderboard.append({
                "rank": i,
                "name": user.get("name", user["email"]),
                "total_medicines": user["total_medicines"],
                "medicines": user["medicines"]
            })
        
        return jsonify(formatted_leaderboard)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ---------- Helper ----------
def send_whatsapp(to_number, msg):
    if not twilio_client:
        print(f"Warning: Cannot send WhatsApp message - Twilio not configured")
        return
    try:
        twilio_client.messages.create(
            body=msg,
            from_=TWILIO_NUMBER,
            to=f'whatsapp:{to_number}'
        )
    except Exception as e:
        print(f"Error sending WhatsApp message: {e}")

if __name__ == '__main__':
    app.run(debug=os.getenv('FLASK_DEBUG', 'False').lower() == 'true')
