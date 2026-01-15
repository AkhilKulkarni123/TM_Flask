from flask import Blueprint, request, jsonify, make_response, current_app
from flask_restful import Api, Resource
from model.user import User
import jwt
import datetime
from functools import wraps
import os

authenticate_api = Blueprint('authenticate_api', __name__, url_prefix='/api')
api = Api(authenticate_api)

def is_production_request():
    """Check if this is a production request by examining headers and scheme.
    
    When behind nginx proxy, request.host may show localhost, so we check:
    1. X-Forwarded-Proto header (set by nginx for HTTPS)
    2. Request scheme
    3. Host header as fallback
    """
    # Check X-Forwarded-Proto header (nginx sets this for HTTPS)
    forwarded_proto = request.headers.get('X-Forwarded-Proto', '')
    if forwarded_proto == 'https':
        return True
    
    # Check request scheme
    if request.scheme == 'https':
        return True
    
    # Fallback: check host (for direct access without proxy)
    host = request.host.lower()
    if not (host.startswith('localhost') or host.startswith('127.0.0.1')):
        return True
    
    return False

def set_jwt_cookie(response, token):
    """Centralized JWT cookie setting logic for cross-origin support"""
    cookie_name = current_app.config.get("JWT_TOKEN_NAME", "jwt")
    
    # Detect if running in production or development
    is_production = is_production_request()
    
    if is_production:
        # Production: secure cookies for cross-domain HTTPS
        response.set_cookie(
            cookie_name,
            token,
            max_age=86400,  # 24 hours
            secure=True,    # Required for HTTPS
            httponly=True,  # Prevent XSS access
            path='/',
            samesite='None' # Required for cross-domain cookies
        )
    else:
        # Development: permissive settings for localhost
        response.set_cookie(
            cookie_name,
            token,
            max_age=86400,  # 24 hours
            secure=False,   # Allow HTTP for localhost
            httponly=False, # Allow JavaScript access for debugging
            path='/',
            samesite='Lax'  # Default for same-site requests
        )
    
    return response

def token_required(f):
    """Decorator to require valid JWT token"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        cookie_name = current_app.config.get("JWT_TOKEN_NAME", "jwt")
        
        # Check for token in cookies first
        if cookie_name in request.cookies:
            token = request.cookies.get(cookie_name)
        # Then check Authorization header
        elif 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
        
        if not token:
            return jsonify({'message': 'Token is missing'}), 401
        
        try:
            data = jwt.decode(token, current_app.config["SECRET_KEY"], algorithms=["HS256"])
            current_user = User.query.filter_by(_uid=data['_uid']).first()
            if not current_user:
                return jsonify({'message': 'User not found'}), 401
        except jwt.ExpiredSignatureError:
            return jsonify({'message': 'Token has expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'message': 'Token is invalid'}), 401
        except Exception as e:
            return jsonify({'message': f'Token validation error: {str(e)}'}), 401
        
        return f(current_user, *args, **kwargs)
    
    return decorated

class Authenticate(Resource):
    """User login authentication"""
    
    def post(self):
        try:
            data = request.get_json()
            
            if not data:
                return {'message': 'No data provided'}, 400
            
            uid = data.get('uid')
            password = data.get('password')
            
            if not uid or not password:
                return {'message': 'Username and password are required'}, 400
            
            # Find user
            user = User.query.filter_by(_uid=uid).first()
            
            if not user:
                return {'message': 'Invalid credentials'}, 401
            
            # Check password
            if not user.is_password(password):
                return {'message': 'Invalid credentials'}, 401
            
            # Generate JWT token
            token = jwt.encode({
                '_uid': user._uid,
                'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
            }, current_app.config["SECRET_KEY"], algorithm="HS256")
            
            # Create response
            response = make_response(jsonify({
                'message': 'Login successful',
                'user': user.read(),
                'token': token
            }), 200)
            
            # Set cookie with proper cross-origin settings
            set_jwt_cookie(response, token)
            
            return response
            
        except Exception as e:
            return {'message': f'Login error: {str(e)}'}, 500

class GetUser(Resource):
    """Get current logged-in user"""
    
    @token_required
    def get(self, current_user):
        try:
            return jsonify({
                'user': current_user.read()
            })
        except Exception as e:
            return {'message': f'Error getting user: {str(e)}'}, 500

class Logout(Resource):
    """User logout"""
    
    def post(self):
        cookie_name = current_app.config.get("JWT_TOKEN_NAME", "jwt")
        response = make_response(jsonify({'message': 'Logout successful'}), 200)
        response.set_cookie(cookie_name, '', max_age=0, path='/')
        return response

# Register endpoints
api.add_resource(Authenticate, '/authenticate')
api.add_resource(GetUser, '/id')
api.add_resource(Logout, '/logout')