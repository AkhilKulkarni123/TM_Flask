from flask import Flask
from flask_login import LoginManager
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_socketio import SocketIO
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Setup of key Flask object (app)
app = Flask(__name__)

# Initialize Flask-SocketIO for real-time multiplayer
# CORS is set to * to allow all origins for socket connections
# async_mode is set to 'eventlet' for production (gunicorn with eventlet worker)
# Falls back to 'threading' in development if eventlet is not available
try:
    import eventlet
    eventlet.monkey_patch()
    async_mode = 'eventlet'
except ImportError:
    async_mode = 'threading'

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode=async_mode,
    logger=False,
    engineio_logger=False,
    ping_timeout=60,
    ping_interval=25
)

# Configure Flask Port, default to 8301 which is same as Docker setup
app.config['FLASK_PORT'] = int(os.environ.get('FLASK_PORT') or 8306)

# Configure Flask to handle JSON with UTF-8 encoding versus default ASCII
app.config['JSON_AS_ASCII'] = False  # Allow emojis, non-ASCII characters in JSON responses

# Initialize Flask-Login object
login_manager = LoginManager()
login_manager.init_app(app)

# NOTE: CORS is configured in main.py with full settings
# Do not initialize CORS here to avoid duplicate configuration

# Admin Defaults
app.config['ADMIN_USER'] = os.environ.get('ADMIN_USER') or 'Admin Name'
app.config['ADMIN_UID'] = os.environ.get('ADMIN_UID') or 'admin'
app.config['ADMIN_PASSWORD'] = os.environ.get('ADMIN_PASSWORD') or os.environ.get('DEFAULT_PASSWORD') or 'password'
app.config['ADMIN_PFP'] = os.environ.get('ADMIN_PFP') or 'default.png'
# Default User Defaults
app.config['DEFAULT_USER'] = os.environ.get('DEFAULT_USER') or 'User Name'
app.config['DEFAULT_UID'] = os.environ.get('DEFAULT_UID') or 'user'
app.config['DEFAULT_USER_PASSWORD'] = os.environ.get('DEFAULT_USER_PASSWORD') or os.environ.get('DEFAULT_PASSWORD') or 'password'
app.config['DEFAULT_USER_PFP'] = os.environ.get('DEFAULT_USER_PFP') or 'default.png'
# Reset Defaults
app.config['DEFAULT_PASSWORD'] = os.environ.get('DEFAULT_PASSWORD') or 'password'
app.config['DEFAULT_PFP'] = os.environ.get('DEFAULT_PFP') or 'default.png'

# Browser settings
SECRET_KEY = os.environ.get('SECRET_KEY') or 'SECRET_KEY'  # secret key for session management
SESSION_COOKIE_NAME = os.environ.get('SESSION_COOKIE_NAME') or 'sess_python_flask'
JWT_TOKEN_NAME = os.environ.get('JWT_TOKEN_NAME') or 'jwt_python_flask'
app.config['SECRET_KEY'] = SECRET_KEY
app.config['SESSION_COOKIE_NAME'] = SESSION_COOKIE_NAME
app.config['JWT_TOKEN_NAME'] = JWT_TOKEN_NAME

# Database settings
dbName = 'user_management'
DB_ENDPOINT = os.environ.get('DB_ENDPOINT') or None
DB_USERNAME = os.environ.get('DB_USERNAME') or None
DB_PASSWORD = os.environ.get('DB_PASSWORD') or None
if DB_ENDPOINT and DB_USERNAME and DB_PASSWORD:
   # Production - Use MySQL
   DB_PORT = '3306'
   DB_NAME = dbName
   dbString = f'mysql+pymysql://{DB_USERNAME}:{DB_PASSWORD}@{DB_ENDPOINT}:{DB_PORT}'
   dbURI =  dbString + '/' + dbName
   backupURI = None  # MySQL backup would require a different approach
else:
   # Development - Use SQLite
   dbString = 'sqlite:///volumes/'
   dbURI = dbString + dbName + '.db'
   backupURI = dbString + dbName + '_bak.db'

# Set database configuration in Flask app
app.config['DB_ENDPOINT'] = DB_ENDPOINT
app.config['DB_USERNAME'] = DB_USERNAME
app.config['DB_PASSWORD'] = DB_PASSWORD
app.config['SQLALCHEMY_DATABASE_NAME'] = dbName
app.config['SQLALCHEMY_DATABASE_STRING'] = dbString
app.config['SQLALCHEMY_DATABASE_URI'] = dbURI
app.config['SQLALCHEMY_BACKUP_URI'] = backupURI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Image upload settings
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # maximum size of uploaded content
app.config['UPLOAD_EXTENSIONS'] = ['.jpg', '.png', '.gif']  # supported file types
app.config['UPLOAD_FOLDER'] = os.path.join(app.instance_path, 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Data folder for shared file-based storage
app.config['DATA_FOLDER'] = os.path.join(app.instance_path, 'data')
os.makedirs(app.config['DATA_FOLDER'], exist_ok=True)

# GITHUB settings
app.config['GITHUB_API_URL'] = 'https://api.github.com'
app.config['GITHUB_TOKEN'] = os.environ.get('GITHUB_TOKEN') or None
app.config['GITHUB_TARGET_TYPE'] = os.environ.get('GITHUB_TARGET_TYPE') or 'user'
app.config['GITHUB_TARGET_NAME'] = os.environ.get('GITHUB_TARGET_NAME') or 'open-coding-society'

# Gemini API settings
app.config['GEMINI_SERVER'] = os.environ.get('GEMINI_SERVER') or 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent'
app.config['GEMINI_API_KEY'] = os.environ.get('GEMINI_API_KEY') or None

# KASM settings
app.config['KASM_SERVER'] = os.environ.get('KASM_SERVER') or 'https://kasm.opencodingsociety.com'
app.config['KASM_API_KEY'] = os.environ.get('KASM_API_KEY') or None
app.config['KASM_API_KEY_SECRET'] = os.environ.get('KASM_API_KEY_SECRET') or None

# GROQ settings
app.config['GROQ_API_KEY'] = os.environ.get('GROQ_API_KEY')

# ---------------------------------------------------------------------
# New: initialise SnakesGameData table (no blueprint import here)
# ---------------------------------------------------------------------

from model.snakes_game import SnakesGameData

with app.app_context():
    SnakesGameData.initSnakesGame()
