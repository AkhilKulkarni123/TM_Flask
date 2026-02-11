"""REST endpoints for social bootstrap and chat uploads."""

from __future__ import annotations

import imghdr
import os
import uuid

import jwt
from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user

from model.user import User
from socketio_handlers import social_core


social_api = Blueprint("social_api", __name__, url_prefix="/api/social")

ALLOWED_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _resolve_authenticated_user():
    try:
        if current_user and getattr(current_user, "is_authenticated", False):
            user = User.query.get(int(current_user.id))
            if user:
                return user
    except Exception:
        pass

    token_name = current_app.config.get("JWT_TOKEN_NAME", "jwt")
    token = request.cookies.get(token_name)
    if not token:
        return None

    try:
        payload = jwt.decode(token, current_app.config["SECRET_KEY"], algorithms=["HS256"])
        uid = payload.get("_uid")
        if not uid:
            return None
        return User.query.filter_by(_uid=uid).first()
    except Exception:
        return None


def _validate_upload_file(file_storage):
    if file_storage is None:
        return False, "Missing image file", None

    raw_name = file_storage.filename or ""
    ext = os.path.splitext(raw_name)[1].lower()
    mime = (file_storage.mimetype or "").lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False, "Unsupported image extension", None
    if mime not in ALLOWED_MIME:
        return False, "Unsupported image type", None

    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_IMAGE_BYTES:
        return False, "Image exceeds 5MB limit", None

    probe = file_storage.stream.read(512)
    file_storage.stream.seek(0)
    detected = imghdr.what(None, h=probe)
    if detected not in {"png", "jpeg", "webp", "gif"}:
        return False, "Invalid image payload", None

    normalized_ext = ALLOWED_MIME[mime]
    if normalized_ext == ".jpg" and ext == ".jpeg":
        normalized_ext = ".jpg"
    return True, "", normalized_ext


@social_api.route("/bootstrap", methods=["GET"])
def social_bootstrap():
    user = _resolve_authenticated_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    party = social_core.get_party_for_user(user.id)

    return jsonify(
        {
            "user": social_core.user_summary(user.id),
            "friends_state": social_core.get_friends_state(user.id),
            "party_state": {
                "party": social_core.serialize_party(party, user.id) if party else None,
                "incoming_invites": social_core.get_pending_party_invites_for_user(user.id),
            },
            "chat_list": {
                "conversations": social_core.get_chat_list(user.id),
            },
            "presence": social_core.get_presence_snapshot(user.id),
        }
    ), 200


@social_api.route("/upload-image", methods=["POST"])
def social_upload_image():
    user = _resolve_authenticated_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    file_storage = request.files.get("image")
    ok, message, extension = _validate_upload_file(file_storage)
    if not ok:
        return jsonify({"error": message}), 400

    upload_root = current_app.config.get("UPLOAD_FOLDER")
    if not upload_root:
        return jsonify({"error": "Upload folder is not configured"}), 500

    chat_dir = os.path.join(upload_root, "social_chat")
    os.makedirs(chat_dir, exist_ok=True)

    filename = f"{uuid.uuid4().hex}{extension}"
    destination = os.path.join(chat_dir, filename)
    file_storage.save(destination)

    return jsonify({"image_url": f"/uploads/social_chat/{filename}"}), 201
