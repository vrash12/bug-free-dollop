# backend/app/routes/auth_routes.py
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required
from sqlalchemy import or_
from sqlalchemy.exc import OperationalError

from .. import db
from ..models.user import User

auth_bp = Blueprint("auth", __name__)

# IMPORTANT: helps you confirm which file Flask is actually loading
print("[auth_routes] LOADED FROM:", __file__)


@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}

    email = (data.get("email") or "").strip().lower()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not email or not username or not password:
        return jsonify({"message": "email, username and password are required"}), 400

    if len(password) < 6:
        return jsonify({"message": "password must be at least 6 characters"}), 400

    try:
        if User.query.filter_by(email=email).first():
            return jsonify({"message": "email already in use"}), 400

        if User.query.filter_by(username=username).first():
            return jsonify({"message": "username already in use"}), 400

    except OperationalError as e:
        current_app.logger.exception(f"[auth/register] DB unavailable: {e}")
        return jsonify({"message": "Database unavailable. Please try again."}), 503

    user = User(email=email, username=username, display_name=username)
    user.set_password(password)

    try:
        db.session.add(user)
        db.session.commit()

        access_token = create_access_token(identity=str(user.id))
        return jsonify({"token": access_token, "user": user.to_dict()}), 201

    except OperationalError as e:
        db.session.rollback()
        current_app.logger.exception(f"[auth/register] DB error: {e}")
        return jsonify({"message": "Database unavailable. Please try again."}), 503

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception(f"Registration Error: {e}")
        return jsonify({"message": "Internal server error"}), 500

#E

@auth_bp.route("/login", methods=["POST"])
def login():
    """
    Accepts:
      - { "email": "...", "password": "..." }
      - { "username": "...", "password": "..." }
      - { "identifier": "...", "password": "..." }  # email or username
    """
    data = request.get_json(silent=True) or {}

    identifier = (
        data.get("identifier")
        or data.get("email")
        or data.get("username")
        or ""
    ).strip()
    password = data.get("password") or ""  # do NOT strip passwords

    # Debug the incoming payload (without printing the password)
    current_app.logger.info(f"[auth/login] identifier='{identifier}' keys={list(data.keys())}")

    if not identifier or not password:
        return jsonify({"message": "identifier and password are required"}), 400

    user = User.query.filter(
        or_(
            User.email == identifier.lower(),
            User.username == identifier,
        )
    ).first()

    if not user:
        current_app.logger.info(f"[auth/login] user NOT found for '{identifier}'")
        return jsonify({"message": "invalid credentials"}), 401

    if not user.check_password(password):
        current_app.logger.info(f"[auth/login] bad password for user_id={user.id} identifier='{identifier}'")
        return jsonify({"message": "invalid credentials"}), 401

    access_token = create_access_token(identity=str(user.id))
    return jsonify({"token": access_token, "user": user.to_dict()}), 200


@auth_bp.route("/me", methods=["GET"])
@jwt_required()
def me():
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    if not user:
        return jsonify({"message": "user not found"}), 404
    return jsonify({"user": user.to_dict()}), 200


@auth_bp.route("/face/verify", methods=["POST"])
@jwt_required()
def verify_face_disabled():
    """
    Face verification was removed (cv2/numpy/face_recognition deleted).
    Keep the route so the frontend doesn't crash, but return a clear response.
    """
    return (
        jsonify(
            {
                "match": False,
                "distance": None,
                "message": "Face verification has been removed from the backend.",
            }
        ),
        410,
    )
