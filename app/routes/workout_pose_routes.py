# backend/app/routes/workouts_routes.py

from datetime import datetime
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity

from .. import db
from ..models.user import User
from ..models.workout import Workout  # <-- make sure this exists in your project
from ..models.social import Achievement, UserAchievement

from flask import Blueprint

# Pose endpoints were removed (on-device ML now), but keep blueprint so app can import/register it
workout_pose_bp = Blueprint("workout_pose", __name__)


workouts_bp = Blueprint("workouts", __name__)

LEVEL_STEP_POINTS = 1000


# ------------------------------
# Helpers
# ------------------------------
def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _safe_int_or_none(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except Exception:
        return None


def _workout_to_dict(w: Workout) -> Dict[str, Any]:
    # Use model's to_dict if you have it
    if hasattr(w, "to_dict") and callable(getattr(w, "to_dict")):
        return w.to_dict()

    # Fallback minimal dict (match frontend needs)
    return {
        "id": w.id,
        "user_id": getattr(w, "user_id", None),
        "title": getattr(w, "title", None),
        "exercise_id": getattr(w, "exercise_id", None),
        "difficulty": getattr(w, "difficulty", None),
        "preset_id": getattr(w, "preset_id", None),
        "target_sets": getattr(w, "target_sets", None),
        "target_reps": getattr(w, "target_reps", None),
        "status": getattr(w, "status", None),
        "total_duration_seconds": getattr(w, "total_duration_seconds", None),
        "total_points_earned": getattr(w, "total_points_earned", None),
        "total_reps": getattr(w, "total_reps", None),
        "started_at": getattr(w, "started_at", None).isoformat() if getattr(w, "started_at", None) else None,
        "completed_at": getattr(w, "completed_at", None).isoformat() if getattr(w, "completed_at", None) else None,
    }


def _compute_level_from_points(total_points: int) -> int:
    total_points = int(total_points or 0)
    return max(1, (total_points // LEVEL_STEP_POINTS) + 1)


def _unlock_achievement_if_needed(user_id: int, code: str) -> Optional[Dict[str, Any]]:
    """
    Unlock achievement by code if active and not already unlocked.
    Returns achievement dict if newly unlocked, else None.
    """
    ach = Achievement.query.filter(
        Achievement.code == code,
        Achievement.is_active.is_(True),
    ).first()

    if not ach:
        return None

    exists = UserAchievement.query.filter_by(
        user_id=user_id,
        achievement_id=ach.id,
    ).first()
    if exists:
        return None

    ua = UserAchievement(
        user_id=user_id,
        achievement_id=ach.id,
        unlocked_at=datetime.utcnow(),
    )
    db.session.add(ua)

    return {
        "id": ach.id,
        "code": ach.code,
        "name": ach.name,
        "description": ach.description,
        "points_reward": int(ach.points_reward or 0),
        "unlocked_at": ua.unlocked_at.isoformat(),
    }


# ------------------------------
# POST /api/workouts/start
# ------------------------------
@workouts_bp.route("/start", methods=["POST"])
@jwt_required()
def start_workout():
    user_id = int(get_jwt_identity())
    data = request.get_json() or {}

    title = data.get("title") or "Workout session"
    exercise_id = data.get("exercise_id") or "unknown"
    difficulty = data.get("difficulty")  # can be None
    preset_id = data.get("preset_id")    # can be None
    target_sets = _safe_int_or_none(data.get("target_sets"))
    target_reps = _safe_int_or_none(data.get("target_reps"))

    try:
        workout = Workout(
            user_id=user_id,
            title=title,
            exercise_id=exercise_id,
            difficulty=difficulty,
            preset_id=preset_id,
            target_sets=target_sets,
            target_reps=target_reps,
            status="active",
            started_at=datetime.utcnow(),
        )
        db.session.add(workout)
        db.session.commit()

        return jsonify({"workout": _workout_to_dict(workout)}), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"message": "Failed to start workout", "error": str(e)}), 500


# ------------------------------
# POST /api/workouts/complete
# ------------------------------
@workouts_bp.route("/complete", methods=["POST"])
@jwt_required()
def complete_workout():
    """
    Expected body from your app:
    {
      "workout_id": 123,
      "total_duration_seconds": 120,
      "total_points_earned": 50,
      "total_reps": 10
    }
    """
    user_id = int(get_jwt_identity())
    data = request.get_json() or {}

    workout_id = _safe_int_or_none(data.get("workout_id"))
    if not workout_id:
        return jsonify({"message": "workout_id is required"}), 400

    duration_seconds = _safe_int(data.get("total_duration_seconds"), 0)
    points_earned = _safe_int(data.get("total_points_earned"), 0)
    total_reps = _safe_int(data.get("total_reps"), 0)

    try:
        user = User.query.get(user_id)
        if not user:
            return jsonify({"message": "user not found"}), 404

        workout = Workout.query.filter_by(id=workout_id, user_id=user_id).first()
        if not workout:
            return jsonify({"message": "workout not found"}), 404

        # Idempotency: if already completed, don't add points again
        if getattr(workout, "status", None) == "completed":
            # keep level synced (optional)
            user_total = int(user.total_points or 0)
            user.level = _compute_level_from_points(user_total)
            db.session.commit()

            return jsonify(
                {
                    "message": "Workout already completed",
                    "workout": _workout_to_dict(workout),
                    "user": {"total_points": int(user.total_points or 0), "level": int(user.level or 1)},
                    "unlocked_achievements": [],
                }
            ), 200

        # Mark workout completed
        workout.status = "completed"
        workout.completed_at = datetime.utcnow()

        # Store stats (adjust if your model names differ)
        workout.total_duration_seconds = max(0, duration_seconds)
        workout.total_points_earned = max(0, points_earned)
        workout.total_reps = max(0, total_reps)

        # Update user points + level
        user.total_points = int(user.total_points or 0) + max(0, points_earned)
        user.level = _compute_level_from_points(int(user.total_points or 0))

        unlocked_now = []

        # Optional: unlock "first_workout" when first completed workout is done
        # (only if you have Achievement.code == "first_workout")
        completed_count = (
            Workout.query.filter_by(user_id=user_id, status="completed").count()
        )
        if completed_count == 0:
            # This workout is about to become the first completed one
            unlocked = _unlock_achievement_if_needed(user_id, "first_workout")
            if unlocked:
                unlocked_now.append(unlocked)

        db.session.commit()

        return jsonify(
            {
                "message": "Workout completed",
                "workout": _workout_to_dict(workout),
                "user": {"total_points": int(user.total_points or 0), "level": int(user.level or 1)},
                "unlocked_achievements": unlocked_now,
            }
        ), 200

    except Exception as e:
        db.session.rollback()
        # IMPORTANT: Always return a response (prevents your current TypeError)
        return jsonify({"message": "Failed to complete workout", "error": str(e)}), 500
