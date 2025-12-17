# imports from flask
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
from flask import abort, redirect, render_template, request, send_from_directory, url_for, jsonify, current_app, g, make_response
from flask_login import current_user, login_user, logout_user
from flask.cli import AppGroup
from flask_login import current_user, login_required
from flask import current_app
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv
from api.jwt_authorize import token_required
# New Game Imports for Snake and Ladders
from api.websocket import init_websocket
from flask_cors import CORS
from flask import Flask, request, make_response, jsonify
from flask_cors import CORS
import os
from dotenv import load_dotenv
import jwt

# import API blueprints
from api.game import game_api
from api.boss_battle import boss_api
from api.admin import admin_api

# import "objects" from "this" project
from __init__ import app, db, login_manager

# API endpoints
from api.user import user_api 
from api.python_exec_api import python_exec_api
from api.javascript_exec_api import javascript_exec_api
from api.section import section_api
from api.pfp import pfp_api
from api.stock import stock_api
from api.analytics import analytics_api
from api.student import student_api
from api.groq_api import groq_api
from api.gemini_api import gemini_api
from api.microblog_api import microblog_api
from api.classroom_api import classroom_api
from hacks.joke import joke_api
from api.post import post_api
from api.snakes_game import snakes_game_api  # existing SNAKES GAME API
from api.study import study_api
from api.feedback_api import feedback_api

# 🔹 NEW: import the extended Snakes & Ladders blueprint
from api.snakes_extended import snakes_bp

# database Initialization functions
from model.user import User, initUsers
from model.user import Section
from model.github import GitHubUser
from model.feedback import Feedback
from api.analytics import get_date_range
from model.study import Study, initStudies
from model.classroom import Classroom
from model.post import Post, init_posts
from model.microblog import MicroBlog, Topic, init_microblogs
from hacks.jokes import initJokes
# 🔹 CHANGED: only import SnakesGameData, not initSnakesGame
from model.snakes_game import SnakesGameData  # NEW SNAKES GAME MODEL

# server only Views
import os
import requests

# Load environment variables
load_dotenv()

# ============================================================================
# CORS CONFIGURATION
# ============================================================================
CORS(
    app,
    supports_credentials=True,
    origins=[
        "http://localhost:4500",
        "http://localhost:3000",
        "http://localhost:8001",
    ],
    allow_headers=["Content-Type", "Authorization", "X-Origin"],
    expose_headers=["Set-Cookie"],
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
)

# ============================================================================
# APP CONFIGURATION
# ============================================================================
app.config['KASM_SERVER'] = os.getenv('KASM_SERVER')
app.config['KASM_API_KEY'] = os.getenv('KASM_API_KEY')
app.config['KASM_API_KEY_SECRET'] = os.getenv('KASM_API_KEY_SECRET')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-change-this')
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'jwt-secret-key-change-this')
app.config['DEFAULT_PASSWORD'] = os.getenv('DEFAULT_PASSWORD', '123Qwerty!')

# ============================================================================
# STATIC FILE SERVING - SERVE JEKYLL BUILD FROM FLASK
# ============================================================================

@app.route('/game/questions/questions.html')
def serve_questions():
    """Serve questions page - ensure user is authenticated"""
    # Check if user has valid JWT
    cookie_name = current_app.config.get("JWT_TOKEN_NAME", "jwt")
    token = request.cookies.get(cookie_name)
    
    if not token and not current_user.is_authenticated:
        # Redirect to login if no valid session
        return redirect(url_for('login', next=request.path))
    
    questions_dir = os.path.join(app.root_path, '..', 'frontend', '_includes', 'tailwind')
    return send_from_directory(questions_dir, 'questions.html')

@app.route('/js/questions_bank.js')
def serve_questions_bank():
    """Serve questions bank JS"""
    # Try _site first (Jekyll build location)
    js_dir = os.path.join(app.root_path, '..', 'frontend', '_site', 'hacks', 'snakes', 'questions')
    return send_from_directory(js_dir, 'questions_bank.js')

@app.route('/game/<path:filename>')
def serve_game(filename):
    """Serve game files from Jekyll build directory"""
    # Adjust this path to match your Jekyll _site output directory
    jekyll_build_dir = os.path.join(app.root_path, '..', 'frontend', '_site', 'game')
    return send_from_directory(jekyll_build_dir, filename)

@app.route('/js/<path:filename>')
def serve_js(filename):
    """Serve JavaScript files"""
    jekyll_build_dir = os.path.join(app.root_path, '..', 'frontend', '_site', 'js')
    return send_from_directory(jekyll_build_dir, filename)

@app.route('/css/<path:filename>')
def serve_css(filename):
    """Serve CSS files"""
    jekyll_build_dir = os.path.join(app.root_path, '..', 'frontend', '_site', 'css')
    return send_from_directory(jekyll_build_dir, filename)

@app.route('/assets/<path:filename>')
def serve_assets(filename):
    """Serve asset files"""
    jekyll_build_dir = os.path.join(app.root_path, '..', 'frontend', '_site', 'assets')
    return send_from_directory(jekyll_build_dir, filename)

# ============================================================================
# REGISTER API BLUEPRINTS
# ============================================================================
app.register_blueprint(python_exec_api)
app.register_blueprint(javascript_exec_api)
app.register_blueprint(user_api)
app.register_blueprint(section_api)
app.register_blueprint(pfp_api) 
app.register_blueprint(stock_api)
app.register_blueprint(groq_api)
app.register_blueprint(gemini_api)
app.register_blueprint(microblog_api)
app.register_blueprint(analytics_api)
app.register_blueprint(student_api)
app.register_blueprint(study_api)
app.register_blueprint(classroom_api)
app.register_blueprint(feedback_api)
app.register_blueprint(joke_api)
app.register_blueprint(post_api)
app.register_blueprint(game_api)
app.register_blueprint(boss_api)
app.register_blueprint(admin_api)
app.register_blueprint(snakes_game_api)

# 🔹 NEW: register the extended Snakes AP CSP blueprint (under /api/snakes)
app.register_blueprint(snakes_bp)

# ============================================================================
# DATABASE INITIALIZATION
# ============================================================================

with app.app_context():
    initJokes()
    SnakesGameData.initSnakesGame()

# ============================================================================
# FLASK-LOGIN CONFIGURATION
# ============================================================================

login_manager.login_view = "login"

@login_manager.unauthorized_handler
def unauthorized_callback():
    return redirect(url_for('login', next=request.path))


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.context_processor
def inject_user():
    return dict(current_user=current_user)

# ============================================================================
# JWT COOKIE HELPER
# ============================================================================

def set_jwt_cookie(response, token):
    """Centralized JWT cookie setting logic"""
    cookie_name = current_app.config.get("JWT_TOKEN_NAME", "jwt")
    
    # FIXED: More permissive settings for localhost
    response.set_cookie(
        cookie_name,
        token,
        max_age=43200,  # 12 hours
        secure=False,   # Must be False for HTTP localhost
        httponly=False, # CRITICAL: Allow JavaScript access
        path='/',       # Available to all paths
        samesite='None' if request.is_secure else 'Lax',  # Lax for localhost
        domain=None     # No domain restriction for localhost
    )
    print(f"✅ JWT cookie '{cookie_name}' set successfully")
    return response

# ============================================================================
# AUTHENTICATION ROUTES
# ============================================================================

def is_safe_url(target):
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    next_page = request.args.get('next', '') or request.form.get('next', '')

    if request.method == 'POST':
        user = User.query.filter_by(_uid=request.form['username']).first()
        if user and user.is_password(request.form['password']):
            # Flask-Login session login
            login_user(user, remember=True, duration=timedelta(hours=12))

            # Safety check on redirect target
            if not is_safe_url(next_page):
                return abort(400)

            # Create JWT token
            token = jwt.encode(
                {
                    "_uid": user._uid,
                    "role": user.role,
                    "exp": datetime.utcnow() + timedelta(hours=12)
                },
                current_app.config["SECRET_KEY"],
                algorithm="HS256"
            )

            response = make_response(redirect(next_page or url_for('index')))
            set_jwt_cookie(response, token)
            
            print(f"✅ User {user._uid} logged in successfully")
            return response
        else:
            error = 'Invalid username or password.'
            print(f"❌ Login failed for username: {request.form.get('username')}")

    return render_template("login.html", error=error, next=next_page)

@app.route('/studytracker')
def studytracker():
    return render_template("studytracker.html")
    
@app.route('/logout')
def logout():
    logout_user()
    response = make_response(redirect(url_for('index')))
    # Clear JWT cookie
    cookie_name = current_app.config.get("JWT_TOKEN_NAME", "jwt")
    response.set_cookie(cookie_name, '', expires=0)
    return response

# ============================================================================
# API HEALTH CHECK & ROOT
# ============================================================================

@app.route('/api/health', methods=['GET'])
def health_check():
    """API health check endpoint"""
    return jsonify({
        "status": "ok", 
        "message": "Backend is running",
        "timestamp": datetime.utcnow().isoformat()
    }), 200

@app.route('/api')
def api_root():
    """API root endpoint"""
    return jsonify({
        "message": "Flask Backend API", 
        "version": "1.0",
        "endpoints": {
            "health": "/api/health",
            "authenticate": "/api/authenticate",
            "user": "/api/user",
            "id": "/api/id"
        }
    }), 200

@app.route('/api/authenticate', methods=['POST'])
def authenticate():
    """Alternative authentication endpoint that returns user data"""
    try:
        # Try to get JWT from cookie
        cookie_name = current_app.config.get("JWT_TOKEN_NAME", "jwt")
        token = request.cookies.get(cookie_name)
        
        if not token:
            return jsonify({'error': 'Not authenticated'}), 401
        
        # Verify JWT
        try:
            payload = jwt.decode(
                token,
                current_app.config["SECRET_KEY"],
                algorithms=["HS256"]
            )
            
            # Get user from database
            user = User.query.filter_by(_uid=payload['_uid']).first()
            if not user:
                return jsonify({'error': 'User not found'}), 404
            
            return jsonify({
                'id': user.id,
                'uid': user._uid,
                'name': user._name,
                'role': user.role
            }), 200
            
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401
            
    except Exception as e:
        print(f"❌ Authentication error: {str(e)}")
        return jsonify({'error': 'Authentication failed'}), 500
    
# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(404)
def page_not_found(e):
    
    if request.path.startswith('/api/'):
        return jsonify({'error': 'API endpoint not found'}), 404
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Internal server error'}), 500
    return render_template('500.html'), 500

# ============================================================================
# SERVER-SIDE HTML ROUTES
# ============================================================================

@app.route('/')
def index():
    print("Home:", current_user)
    return render_template("index.html")

@app.route('/users/table2')
@login_required
def u2table():
    users = User.query.all()
    return render_template("u2table.html", user_data=users)

@app.route('/sections/')
@login_required
def sections():
    sections = Section.query.all()
    return render_template("sections.html", sections=sections)

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)

# ============================================================================
# GAME ROUTES
# ============================================================================

@app.route('/game-board')
@login_required
def game_board():
    """Serve the main game board - requires login"""
    jekyll_build_dir = os.path.join(app.root_path, '..', 'frontend', '_site', 'game')
    return send_from_directory(jekyll_build_dir, 'game-board-part2.html')

# ============================================================================
# USER MANAGEMENT ROUTES
# ============================================================================

@app.route('/users/delete/<int:user_id>', methods=['DELETE'])
@login_required
def delete_user(user_id):
    user = User.query.get(user_id)
    if user:
        user.delete()
        return jsonify({'message': 'User deleted successfully'}), 200
    return jsonify({'error': 'User not found'}), 404

@app.route('/users/reset_password/<int:user_id>', methods=['POST'])
@login_required
def reset_password(user_id):
    if current_user.role != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    if user.update({"password": app.config['DEFAULT_PASSWORD']}):
        return jsonify({'message': 'Password reset successfully'}), 200
    return jsonify({'error': 'Password reset failed'}), 500

@app.route('/update_user/<string:uid>', methods=['PUT'])
def update_user(uid):
    if current_user.role != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    print(f"Request Data: {data}")

    user = User.query.filter_by(_uid=uid).first()
    if user:
        print(f"Found user: {user.uid}")
        user.update(data)
        return jsonify({"message": "User updated successfully."}), 200
    else:
        print("User not found.")
        return jsonify({"message": "User not found."}), 404

# ============================================================================
# KASM INTEGRATION ROUTES
# ============================================================================

@app.route('/kasm_users')
def kasm_users():
    SERVER = current_app.config.get('KASM_SERVER')
    API_KEY = current_app.config.get('KASM_API_KEY')
    API_KEY_SECRET = current_app.config.get('KASM_API_KEY_SECRET')

    if not SERVER or not API_KEY or not API_KEY_SECRET:
        return render_template('error.html', message='KASM keys are missing'), 400

    try:
        url = f"{SERVER}/api/public/get_users"
        data = {
            "api_key": API_KEY,
            "api_key_secret": API_KEY_SECRET
        }

        response = requests.post(url, json=data, timeout=10)

        if response.status_code != 200:
            return render_template(
                'error.html', 
                message='Failed to get users', 
                code=response.status_code
            ), response.status_code

        users = response.json().get('users', [])

        for user in users:
            last_session = user.get('last_session')
            try:
                user['last_session'] = datetime.fromisoformat(last_session) if last_session else None
            except ValueError:
                user['last_session'] = None

        sorted_users = sorted(
            users, 
            key=lambda x: x['last_session'] or datetime.min, 
            reverse=True
        )

        return render_template('kasm_users.html', users=sorted_users)

    except requests.RequestException as e:
        return render_template(
            'error.html', 
            message=f"Error connecting to KASM API: {str(e)}"
        ), 500
        
@app.route('/delete_user/<user_id>', methods=['DELETE'])
def delete_user_kasm(user_id):
    if current_user.role != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403
    
    SERVER = current_app.config.get('KASM_SERVER')
    API_KEY = current_app.config.get('KASM_API_KEY')
    API_KEY_SECRET = current_app.config.get('KASM_API_KEY_SECRET')

    if not SERVER or not API_KEY or not API_KEY_SECRET:
        return {'message': 'KASM keys are missing'}, 400

    try:
        url = f"{SERVER}/api/public/delete_user"
        data = {
            "api_key": API_KEY,
            "api_key_secret": API_KEY_SECRET,
            "target_user": {"user_id": user_id},
            "force": False
        }
        response = requests.post(url, json=data)

        if response.status_code == 200:
            return {'message': 'User deleted successfully'}, 200
        else:
            return {'message': 'Failed to delete user'}, response.status_code

    except requests.RequestException as e:
        return {'message': 'Error connecting to KASM API', 'error': str(e)}, 500

# ============================================================================
# CLI COMMANDS
# ============================================================================

custom_cli = AppGroup('custom', help='Custom commands')

@custom_cli.command('generate_data')
def generate_data():
    initUsers()
    init_microblogs()
    SnakesGameData.initSnakesGame()

app.cli.add_command(custom_cli)

# ============================================================================
# RUN APPLICATION
# ============================================================================

if __name__ == "__main__":
    host = "0.0.0.0"
    port = int(os.getenv('FLASK_PORT', 8001))
    print(f"\n{'='*60}")
    print(f"🚀 Server running: http://localhost:{port}")
    print(f"📡 API endpoints: http://localhost:{port}/api")
    print(f"🎮 Game board: http://localhost:{port}/game-board")
    print(f"🔐 Login: http://localhost:{port}/login")
    print(f"{'='*60}\n")
    app.run(debug=True, host=host, port=port, use_reloader=False)
