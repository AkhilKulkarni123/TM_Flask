from flask import Blueprint, request, jsonify, make_response
from flask_restful import Api, Resource
from model.user import User
import jwt
import datetime
from functools import wraps
import os

authenticate_api = Blueprint('authenticate_api', __name__, url_prefix='/api')
api = Api(authenticate_api)

SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'jwt-secret-key-change-this')

def token_required(f):
    """Decorator to require valid JWT token"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        
        # Check for token in cookies first
        if 'jwt' in request.cookies:
            token = request.cookies.get('jwt')
        # Then check Authorization header
        elif 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
        
        if not token:
            return jsonify({'message': 'Token is missing'}), 401
        
        try:
            data = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
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
            }, SECRET_KEY, algorithm="HS256")
            
            # Create response
            response = make_response(jsonify({
                'message': 'Login successful',
                'user': user.read(),
                'token': token
            }), 200)
            
            # Set cookie
            response.set_cookie(
                'jwt',
                token,
                max_age=86400,  # 24 hours
                secure=False,  # Set to True in production with HTTPS
                httponly=True,
                samesite='Lax'
            )
            
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
        response = make_response(jsonify({'message': 'Logout successful'}), 200)
        response.set_cookie('jwt', '', max_age=0)
        return response

# Register endpoints
api.add_resource(Authenticate, '/authenticate')
api.add_resource(GetUser, '/id')
api.add_resource(Logout, '/logout')