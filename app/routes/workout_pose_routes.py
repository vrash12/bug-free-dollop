# backend/app/routes/workouts_routes.py

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy import func

from .. import db
from ..models.user import User
from ..models.workout import Workout
from ..models.social import Achievement, UserAchievement

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
    if v is None or v == "":
        return None
    try:
        return int(v)
    except Exception:
        return None


def _compute_level_from_points(total_points: int) -> int:
    total_points = int(total_points or 0)
    return max(1, (total_points // LEVEL_STEP_POINTS) + 1)


def _workout_to_dict(w: Workout) -> Dict[str, Any]:
    # Prefer model helpers if present
    if hasattr(w, "to_summary_dict") and callable(getattr(w, "to_summary_dict")):
        return w.to_summary_dict()
    if hasattr(w, "to_dict") and callable(getattr(w, "to_dict")):
        return w.to_dict()

    return {
        "id": int(w.id),
        "user_id": int(getattr(w, "user_id", 0) or 0),
        "title": getattr(w, "title", None),
        "workout_date": w.workout_date.isoformat() if getattr(w, "workout_date", None) else None,
        "started_at": w.started_at.isoformat() if getattr(w, "started_at", None) else None,
        "ended_at": w.ended_at.isoformat() if getattr(w, "ended_at", None) else None,
        "total_duration_seconds": int(getattr(w, "total_duration_seconds", 0) or 0),
        "total_points_earned": int(getattr(w, "total_points_earned", 0) or 0),
        "total_reps": int(getattr(w, "total_reps", 0) or 0),
        "exercise_id": getattr(w, "exercise_id", None),
        "difficulty": getattr(w, "difficulty", None),
        "preset_id": getattr(w, "preset_id", None),
        "target_sets": getattr(w, "target_sets", None),
        "target_reps": getattr(w, "target_reps", None),
    }


def _update_streak(user: User, workout_day: date) -> None:
    """
    Updates:
      - users.last_active_date
      - users.current_streak_days
      - users.longest_streak_days
    based on workout_day (date).
    """
    last: Optional[date] = getattr(user, "last_active_date", None)

    if last == workout_day:
        return  # already counted today

    if last == (workout_day - timedelta(days=1)):
        user.current_streak_days = int(user.current_streak_days or 0) + 1
    else:
        user.current_streak_days = 1

    user.longest_streak_days = max(
        int(user.longest_streak_days or 0),
        int(user.current_streak_days or 0),
    )
    user.last_active_date = workout_day


def _already_unlocked(user_id: int, achievement_id: int) -> bool:
    return (
        UserAchievement.query.filter_by(user_id=user_id, achievement_id=achievement_id)
        .limit(1)
        .first()
        is not None
    )


def _unlock_achievement(user: User, ach: Achievement) -> Dict[str, Any]:
    ua = UserAchievement(
        user_id=int(user.id),
        achievement_id=int(ach.id),
        unlocked_at=datetime.utcnow(),
    )
    db.session.add(ua)

    reward = int(getattr(ach, "points_reward", 0) or 0)
    if reward > 0:
        user.total_points = int(user.total_points or 0) + reward

    return {
        "id": int(ach.id),
        "code": ach.code,
        "name": ach.name,
        "description": ach.description,
        "points_reward": reward,
        "unlocked_at": ua.unlocked_at.isoformat(),
    }


def _get_user_totals(user_id: int) -> Dict[str, int]:
    total_workouts = (
        db.session.query(func.count(Workout.id))
        .filter(Workout.user_id == user_id, Workout.ended_at.isnot(None))
        .scalar()
        or 0
    )

    total_reps = (
        db.session.query(func.coalesce(func.sum(Workout.total_reps), 0))
        .filter(Workout.user_id == user_id, Workout.ended_at.isnot(None))
        .scalar()
        or 0
    )

    return {
        "total_workouts": int(total_workouts),
        "total_reps": int(total_reps),
    }


def _evaluate_and_unlock_achievements(user: User, just_completed_first: bool) -> List[Dict[str, Any]]:
    """
    Unlocks achievements based on:
      - first_workout (only when first completion happens)
      - total_workouts
      - total_reps
      - total_points
      - streak_days
    Skips condition_type == custom (manual unlock).
    """
    unlocked: List[Dict[str, Any]] = []
    user_id = int(user.id)

    totals = _get_user_totals(user_id)
    total_workouts = totals["total_workouts"]
    total_reps = totals["total_reps"]
    total_points = int(user.total_points or 0)
    streak_days = int(user.current_streak_days or 0)

    active = Achievement.query.filter(Achievement.is_active.is_(True)).all()

    # Pass 1: handle first_workout explicitly
    for ach in active:
        if ach.condition_type != "first_workout":
            continue
        if not just_completed_first:
            continue
        if _already_unlocked(user_id, int(ach.id)):
            continue
        unlocked.append(_unlock_achievement(user, ach))

    # Pass 2+: handle threshold achievements; repeat a couple times because adding reward points
    # can trigger total_points achievements.
    for _ in range(3):
        changed = False
        total_points = int(user.total_points or 0)

        for ach in active:
            if _already_unlocked(user_id, int(ach.id)):
                continue

            ctype = (ach.condition_type or "").lower()
            cval = int(getattr(ach, "condition_value", 0) or 0)

            if ctype in ("custom", "first_workout"):
                continue

            ok = False
            if ctype == "total_workouts":
                ok = total_workouts >= cval
            elif ctype == "total_reps":
                ok = total_reps >= cval
            elif ctype == "total_points":
                ok = total_points >= cval
            elif ctype == "streak_days":
                ok = streak_days >= cval

            if ok:
                unlocked.append(_unlock_achievement(user, ach))
                changed = True

        if not changed:
            break

    return unlocked


# ------------------------------
# POST /api/workouts/start
# ------------------------------
@workouts_bp.route("/start", methods=["POST"])
@jwt_required()
def start_workout():
    user_id = int(get_jwt_identity())
    data = request.get_json() or {}

    title = (data.get("title") or "Workout session").strip()
    exercise_id = (data.get("exercise_id") or "unknown").strip().lower()
    difficulty = (data.get("difficulty") or None)
    preset_id = (data.get("preset_id") or None)

    target_sets = _safe_int_or_none(data.get("target_sets"))
    target_reps = _safe_int_or_none(data.get("target_reps"))

    try:
        w = Workout(
            user_id=user_id,
            title=title,
            workout_date=date.today(),          # ✅ required by your schema
            started_at=datetime.utcnow(),       # ✅ required by your schema
            ended_at=None,
            total_duration_seconds=0,
            total_points_earned=0,
            total_reps=0,
            exercise_id=exercise_id,
            difficulty=difficulty,
            preset_id=preset_id,
            target_sets=target_sets,
            target_reps=target_reps,
        )
        db.session.add(w)
        db.session.commit()
        return jsonify({"workout": _workout_to_dict(w)}), 201

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

    duration_seconds = max(0, _safe_int(data.get("total_duration_seconds"), 0))
    points_earned = max(0, _safe_int(data.get("total_points_earned"), 0))
    total_reps = max(0, _safe_int(data.get("total_reps"), 0))

    try:
        user = User.query.get(user_id)
        if not user:
            return jsonify({"message": "user not found"}), 404

        workout = Workout.query.filter_by(id=workout_id, user_id=user_id).first()
        if not workout:
            return jsonify({"message": "workout not found"}), 404

        # ✅ Idempotency: if already ended, never add points twice
        if workout.ended_at is not None:
            user.level = _compute_level_from_points(int(user.total_points or 0))
            db.session.commit()
            return jsonify(
                {
                    "message": "Workout already completed",
                    "workout": _workout_to_dict(workout),
                    "user": {
                        "total_points": int(user.total_points or 0),
                        "level": int(user.level or 1),
                        "current_streak_days": int(user.current_streak_days or 0),
                        "longest_streak_days": int(user.longest_streak_days or 0),
                    },
                    "unlocked_achievements": [],
                }
            ), 200

        # how many completed workouts BEFORE marking this one as completed?
        completed_before = (
            db.session.query(func.count(Workout.id))
            .filter(Workout.user_id == user_id, Workout.ended_at.isnot(None))
            .scalar()
            or 0
        )
        just_completed_first = int(completed_before) == 0

        # ✅ Update workout row to match your schema
        workout.total_duration_seconds = duration_seconds
        workout.total_points_earned = points_earned
        workout.total_reps = total_reps
        workout.ended_at = datetime.utcnow()

        # ✅ Update user points & streak
        user.total_points = int(user.total_points or 0) + points_earned

        wd = workout.workout_date or date.today()
        _update_streak(user, wd)

        # ✅ Unlock achievements (and add their points_reward to user.total_points)
        unlocked_now = _evaluate_and_unlock_achievements(user, just_completed_first)

        # ✅ level after points + achievement rewards
        user.level = _compute_level_from_points(int(user.total_points or 0))

        db.session.commit()

        return jsonify(
            {
                "message": "Workout completed",
                "workout": _workout_to_dict(workout),
                "user": {
                    "total_points": int(user.total_points or 0),
                    "level": int(user.level or 1),
                    "current_streak_days": int(user.current_streak_days or 0),
                    "longest_streak_days": int(user.longest_streak_days or 0),
                    "last_active_date": user.last_active_date.isoformat() if getattr(user, "last_active_date", None) else None,
                },
                "unlocked_achievements": unlocked_now,
            }
        ), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"message": "Failed to complete workout", "error": str(e)}), 500