"""
JWT authorization decorator used across the API.

This module defines the ``token_required`` decorator for endpoints that
require authentication via a JSON Web Token stored in a cookie.  It
decodes the JWT using the application's secret key, loads the
corresponding user from the database and ensures that the user has
appropriate permissions.  On success, it stores the current user in
Flask's global ``g`` object for downstream use.  On failure, it
returns a JSON error response with an appropriate HTTP status code.

The implementation is based on the original TM_Flask project.  It
supports passing a list of roles or a single role string to restrict
access to users with that role.
"""

from functools import wraps
import jwt
from flask import request, current_app, g
from model.user import User


def token_required(roles=None):
    """Decorator that validates the JWT in the request cookie.

    Usage:

        @token_required()          # any authenticated user
        @token_required("Admin")   # only Admin role
        @token_required(["Admin", "Editor"])  # multiple roles

    If a valid token is present, the corresponding user is loaded and
    stored in ``g.current_user``.  Otherwise, the decorated function
    returns a JSON error response.

    Args:
        roles (str|list|tuple|None): allowed role or roles.  If None,
            any authenticated user is allowed.
    """

    def decorator(func_to_guard):
        @wraps(func_to_guard)
        def decorated(*args, **kwargs):
            # Extract token from cookie
            token = request.cookies.get(current_app.config["JWT_TOKEN_NAME"])
            if not token:
                return {
                    "message": "Authentication Token is missing!",
                    "data": None,
                    "error": "Unauthorized",
                }, 401
            try:
                # Decode the token
                data = jwt.decode(
                    token,
                    current_app.config["SECRET_KEY"],
                    algorithms=["HS256"],
                )
                # Look up the user by UID
                current_user = User.query.filter_by(_uid=data.get("_uid")).first()
                if current_user is None:
                    return {
                        "message": "Invalid Authentication token!",
                        "data": None,
                        "error": "Unauthorized",
                    }, 401
                # If roles are specified, ensure the user has permission
                if roles:
                    allowed = roles
                    # roles can be a single string or a collection
                    if isinstance(allowed, (list, tuple, set)):
                        if current_user.role not in allowed:
                            return {
                                "message": f"Insufficient permissions. Required roles: {allowed}",
                                "data": None,
                                "error": "Forbidden",
                            }, 403
                    else:
                        # roles is a single string
                        if current_user.role != allowed:
                            return {
                                "message": f"Insufficient permissions. Required role: {allowed}",
                                "data": None,
                                "error": "Forbidden",
                            }, 403
                # Store the user in Flask's g for downstream use
                g.current_user = current_user
            except jwt.ExpiredSignatureError:
                return {
                    "message": "Authentication token expired",
                    "data": None,
                    "error": "Unauthorized",
                }, 401
            except jwt.InvalidTokenError:
                return {
                    "message": "Invalid authentication token",
                    "data": None,
                    "error": "Unauthorized",
                }, 401
            except Exception as e:
                return {
                    "message": "Unable to validate authentication token",
                    "data": None,
                    "error": str(e),
                }, 401

            # CORS preflight requests should be allowed through
            if request.method == 'OPTIONS':
                return ('', 200)
            # Success: call the decorated function
            return func_to_guard(*args, **kwargs)

        return decorated

    return decorator
