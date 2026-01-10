# backend/app/routes/profile_routes.py
from datetime import datetime
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from .. import db
from  ..models.user import User

profile_bp = Blueprint("profile", __name__)

@profile_bp.route("", methods=["PUT"])
@jwt_required()
def update_profile():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({"message": "user not found"}), 404

    data = request.get_json() or {}

    gender = data.get("gender")
    birth_date = data.get("birth_date")  # "YYYY-MM-DD"
    height_cm = data.get("height_cm")
    weight_kg = data.get("weight_kg")
    target_weight_kg = data.get("target_weight_kg")
    fitness_level = data.get("fitness_level")
    fitness_goal = data.get("fitness_goal")

    # minimal validation, you can tighten this later
    if gender in ["male", "female", "other"]:
        user.gender = gender

    if birth_date:
        try:
            user.birth_date = datetime.fromisoformat(birth_date).date()
        except ValueError:
            return jsonify({"message": "invalid birth_date"}), 400

    if height_cm is not None:
        user.height_cm = float(height_cm)
    if weight_kg is not None:
        user.weight_kg = float(weight_kg)
    if target_weight_kg is not None:
        user.target_weight_kg = float(target_weight_kg)

    if fitness_level in ["beginner", "intermediate", "advanced"]:
        user.fitness_level = fitness_level

    if fitness_goal in ["lose_weight", "gain_muscle", "get_fitter"]:
        user.fitness_goal = fitness_goal

    # mark onboarding complete
    user.has_completed_onboarding = True

    db.session.commit()

    return jsonify({"user": user.to_dict()}), 200
