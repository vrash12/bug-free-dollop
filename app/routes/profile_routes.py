# backend/app/routes/profile_routes.py
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from flask import Blueprint, jsonify, request, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy.exc import OperationalError

from .. import db
from ..models.user import User

profile_bp = Blueprint("profile", __name__)

ALLOWED_GENDER = {"male", "female", "other"}
ALLOWED_FITNESS_LEVEL = {"beginner", "intermediate", "advanced"}
ALLOWED_FITNESS_GOAL = {"lose_weight", "gain_muscle", "get_fitter"}


def _to_decimal(v):
  if v is None or v == "":
    return None
  try:
    return Decimal(str(v))
  except Exception:
    return None


def _parse_date(v):
  if not v:
    return None
  try:
    # expects "YYYY-MM-DD"
    return datetime.strptime(v, "%Y-%m-%d").date()
  except Exception:
    return None


@profile_bp.route("", methods=["GET"])
@jwt_required()
def get_profile():
  try:
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    if not user:
      return jsonify({"message": "user not found"}), 404
    return jsonify({"user": user.to_dict()}), 200
  except Exception as e:
    current_app.logger.exception(f"[profile/get] error: {e}")
    return jsonify({"message": "Internal server error"}), 500


@profile_bp.route("", methods=["PUT"])
@jwt_required()
def update_profile():
  """
  Save onboarding/profile fields to the logged-in user.

  Body example:
  {
    "gender": "male",
    "birth_date": "2001-10-31",
    "height_cm": 170,
    "weight_kg": 70,
    "target_weight_kg": 65,
    "fitness_level": "beginner",
    "fitness_goal": "lose_weight",
    "has_completed_onboarding": true
  }
  """
  data = request.get_json(silent=True) or {}

  try:
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    if not user:
      return jsonify({"message": "user not found"}), 404

    # ---- validate enums safely ----
    gender = data.get("gender")
    if gender is not None:
      if gender not in ALLOWED_GENDER:
        return jsonify({"message": "Invalid gender"}), 400
      user.gender = gender

    fitness_level = data.get("fitness_level")
    if fitness_level is not None:
      if fitness_level not in ALLOWED_FITNESS_LEVEL:
        return jsonify({"message": "Invalid fitness_level"}), 400
      user.fitness_level = fitness_level

    fitness_goal = data.get("fitness_goal")
    if fitness_goal is not None:
      if fitness_goal not in ALLOWED_FITNESS_GOAL:
        return jsonify({"message": "Invalid fitness_goal"}), 400
      user.fitness_goal = fitness_goal

    # ---- date ----
    if "birth_date" in data:
      bd = _parse_date(data.get("birth_date"))
      if data.get("birth_date") and bd is None:
        return jsonify({"message": "Invalid birth_date (use YYYY-MM-DD)"}), 400
      user.birth_date = bd

    # ---- numerics ----
    if "height_cm" in data:
      h = _to_decimal(data.get("height_cm"))
      if data.get("height_cm") is not None and h is None:
        return jsonify({"message": "Invalid height_cm"}), 400
      user.height_cm = h

    if "weight_kg" in data:
      w = _to_decimal(data.get("weight_kg"))
      if data.get("weight_kg") is not None and w is None:
        return jsonify({"message": "Invalid weight_kg"}), 400
      user.weight_kg = w

    if "target_weight_kg" in data:
      tw = _to_decimal(data.get("target_weight_kg"))
      if data.get("target_weight_kg") is not None and tw is None:
        return jsonify({"message": "Invalid target_weight_kg"}), 400
      user.target_weight_kg = tw

    # ---- completion flag ----
    if "has_completed_onboarding" in data:
      user.has_completed_onboarding = bool(data.get("has_completed_onboarding"))

    db.session.commit()
    return jsonify({"user": user.to_dict()}), 200

  except OperationalError as e:
    db.session.rollback()
    current_app.logger.exception(f"[profile/update] DB error: {e}")
    return jsonify({"message": "Database unavailable. Please try again."}), 503
  except Exception as e:
    db.session.rollback()
    current_app.logger.exception(f"[profile/update] error: {e}")
    return jsonify({"message": "Internal server error"}), 500
