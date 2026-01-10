# backend/app/routes/dashboard_routes.py
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from .. import db
from ..models.user import User
from ..models.workout import Workout
from ..models.user_daily_stats import UserDailyStats

# Blueprint for dashboard-related endpoints
dashboard_bp = Blueprint("dashboard", __name__)

# Blueprint for workout-related endpoints
workout_bp = Blueprint("workouts", __name__)


# -------------------------
# Helpers
# -------------------------
def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_int_or_none(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_or_create_daily_stats(user_id: int, stat_date: date) -> UserDailyStats:
    row = UserDailyStats.query.filter_by(user_id=user_id, stat_date=stat_date).first()
    if row:
        return row

    row = UserDailyStats(
        user_id=user_id,
        stat_date=stat_date,
        total_points=0,
        total_workouts=0,
        total_duration_seconds=0,
        total_reps=0,
    )
    db.session.add(row)
    return row


def _update_user_streak(user: User, activity_date: date) -> None:
    """
    Best-effort streak update.
    Works even if your User model doesn't have last_active_date (won't crash).
    """
    last_active = getattr(user, "last_active_date", None)
    current = int(getattr(user, "current_streak_days", 0) or 0)
    longest = int(getattr(user, "longest_streak_days", 0) or 0)

    if last_active == activity_date:
        new_current = max(current, 1)
    elif last_active == (activity_date - timedelta(days=1)):
        new_current = (current if current > 0 else 1) + 1
    else:
        new_current = 1

    setattr(user, "current_streak_days", new_current)
    setattr(user, "longest_streak_days", max(longest, new_current))

    if hasattr(user, "last_active_date"):
        setattr(user, "last_active_date", activity_date)


def _update_user_level_from_points(user: User) -> None:
    """
    Simple, stable leveling: +1 level per 1000 points (minimum level 1).
    If your app has a different rule, replace this function.
    """
    if not hasattr(user, "level"):
        return

    total_points = int(getattr(user, "total_points", 0) or 0)
    computed_level = max(1, 1 + (total_points // 1000))
    current_level = int(getattr(user, "level", 1) or 1)
    setattr(user, "level", max(current_level, computed_level))


# -------------------------
# DASHBOARD OVERVIEW
# -------------------------
@dashboard_bp.route("/overview", methods=["GET"])
@jwt_required()
def dashboard_overview():
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    if not user:
        return jsonify({"message": "user not found"}), 404

    today = date.today()
    week_start = today - timedelta(days=6)

    today_stats = UserDailyStats.query.filter_by(user_id=user.id, stat_date=today).first()
    today_dict = {
        "points": int(today_stats.total_points) if today_stats else 0,
        "workouts": int(today_stats.total_workouts) if today_stats else 0,
        "duration_seconds": int(today_stats.total_duration_seconds) if today_stats else 0,
        "reps": int(today_stats.total_reps) if today_stats else 0,
    }

    stats_rows = (
        UserDailyStats.query.filter(
            UserDailyStats.user_id == user.id,
            UserDailyStats.stat_date >= week_start,
            UserDailyStats.stat_date <= today,
        )
        .order_by(UserDailyStats.stat_date.asc())
        .all()
    )

    stats_by_date = {row.stat_date: row for row in stats_rows}
    by_day = []
    total_points = total_workouts = total_duration_seconds = total_reps = 0

    for i in range(7):
        d = week_start + timedelta(days=i)
        row = stats_by_date.get(d)

        p = int(row.total_points) if row else 0
        w = int(row.total_workouts) if row else 0
        dur = int(row.total_duration_seconds) if row else 0
        r = int(row.total_reps) if row else 0

        total_points += p
        total_workouts += w
        total_duration_seconds += dur
        total_reps += r

        by_day.append(
            {
                "date": d.isoformat(),
                "points": p,
                "workouts": w,
                "duration_seconds": dur,
                "reps": r,
            }
        )

    last7days = {
        "total_points": total_points,
        "total_workouts": total_workouts,
        "total_duration_seconds": total_duration_seconds,
        "total_reps": total_reps,
        "by_day": by_day,
    }

    streak = {
        "current_streak_days": int(getattr(user, "current_streak_days", 0) or 0),
        "longest_streak_days": int(getattr(user, "longest_streak_days", 0) or 0),
    }

    return (
        jsonify(
            {
                "user": user.to_dict(),
                "today": today_dict,
                "last7days": last7days,
                "streak": streak,
            }
        ),
        200,
    )


# -------------------------
# RECENT WORKOUTS
# -------------------------
@workout_bp.route("/recent", methods=["GET"])
@jwt_required()
def recent_workouts():
    user_id = int(get_jwt_identity())

    limit = _safe_int(request.args.get("limit", 5), default=5)
    if limit <= 0:
        limit = 5

    rows = (
        Workout.query.filter_by(user_id=user_id)
        .order_by(Workout.workout_date.desc(), Workout.started_at.desc())
        .limit(limit)
        .all()
    )

    return jsonify({"workouts": [w.to_summary_dict() for w in rows]}), 200


# -------------------------
# START WORKOUT
# -------------------------
@workout_bp.route("/start", methods=["POST"])
@jwt_required()
def start_workout():
    current_user_id = int(get_jwt_identity())
    data = request.get_json() or {}

    title = (data.get("title") or "Workout").strip()

    exercise_id = (data.get("exercise_id") or "").strip() or None
    difficulty = (data.get("difficulty") or "").strip() or None
    preset_id = (data.get("preset_id") or "").strip() or None

    target_sets = _safe_int_or_none(data.get("target_sets"))
    target_reps = _safe_int_or_none(data.get("target_reps"))

    now = datetime.utcnow()
    workout = Workout(
        user_id=current_user_id,
        title=title,
        workout_date=now.date(),
        started_at=now,
        total_duration_seconds=0,
        total_points_earned=0,
        exercise_id=exercise_id,
        difficulty=difficulty,
        preset_id=preset_id,
        target_sets=target_sets,
        target_reps=target_reps,
        total_reps=0,
    )
    db.session.add(workout)
    db.session.commit()

    return jsonify({"workout": workout.to_summary_dict()}), 201


# -------------------------
# COMPLETE WORKOUT
# -------------------------
@workout_bp.route("/complete", methods=["POST"])
@jwt_required()
def complete_workout():
    current_user_id = int(get_jwt_identity())
    data = request.get_json() or {}

    workout_id = data.get("workout_id")
    if workout_id is None:
        return jsonify({"message": "workout_id is required"}), 400

    try:
        workout_id = int(workout_id)
    except (TypeError, ValueError):
        return jsonify({"message": "workout_id must be an integer"}), 400

    workout = Workout.query.filter_by(id=workout_id, user_id=current_user_id).first()
    if not workout:
        return jsonify({"message": "Workout not found"}), 404

    # Idempotency: if already completed, return current data without re-awarding points/stats
    if workout.ended_at is not None:
        user = User.query.get(current_user_id)
        return (
            jsonify(
                {
                    "message": "Workout already completed",
                    "workout": workout.to_summary_dict(),
                    "user": user.to_dict() if user else None,
                }
            ),
            200,
        )

    total_duration_seconds = _safe_int(data.get("total_duration_seconds"), default=0)
    total_points_earned = _safe_int(data.get("total_points_earned"), default=0)
    total_reps = _safe_int(data.get("total_reps"), default=0)

    now = datetime.utcnow()
    today = now.date()

    try:
        # finalize workout
        workout.ended_at = now
        workout.total_duration_seconds = total_duration_seconds
        workout.total_points_earned = total_points_earned
        workout.total_reps = total_reps

        if getattr(workout, "calories_estimate", None) is None:
            # keep it non-null if your schema expects it
            workout.calories_estimate = 0

        # update user totals + streak
        user = User.query.get(current_user_id)
        if not user:
            return jsonify({"message": "user not found"}), 404

        user.total_points = int(getattr(user, "total_points", 0) or 0) + total_points_earned
        _update_user_level_from_points(user)
        _update_user_streak(user, today)

        # update daily stats
        stats = _get_or_create_daily_stats(user.id, today)
        stats.total_points = int(getattr(stats, "total_points", 0) or 0) + total_points_earned
        stats.total_workouts = int(getattr(stats, "total_workouts", 0) or 0) + 1
        stats.total_duration_seconds = int(getattr(stats, "total_duration_seconds", 0) or 0) + total_duration_seconds
        stats.total_reps = int(getattr(stats, "total_reps", 0) or 0) + total_reps

        db.session.commit()

        return (
            jsonify(
                {
                    "message": "Workout completed",
                    "workout": workout.to_summary_dict(),
                    "user": user.to_dict(),
                    "today": {
                        "date": today.isoformat(),
                        "points": int(stats.total_points or 0),
                        "workouts": int(stats.total_workouts or 0),
                        "duration_seconds": int(stats.total_duration_seconds or 0),
                        "reps": int(stats.total_reps or 0),
                    },
                }
            ),
            200,
        )

    except Exception as e:
        db.session.rollback()
        return jsonify({"message": "Failed to complete workout", "error": str(e)}), 500


# -------------------------
# POSE ANALYSIS (REMOVED)
# -------------------------
@workout_bp.route("/<exercise_id>/analyze_frame", methods=["POST"])
@jwt_required()
def analyze_exercise_frame(exercise_id: str):
    """
    This endpoint used to do server-side CV/ML (cv2/numpy/mediapipe).
    You removed ML dependencies, so this endpoint is now intentionally disabled.

    Your app should do pose/rep detection on-device and only call:
      - POST /workouts/start
      - POST /workouts/complete
    """
    return (
        jsonify(
            {
                "message": "Pose analysis has been removed from the backend. Do pose/rep detection on-device and only send workout totals to /workouts/complete.",
                "exercise_id": (exercise_id or "").lower(),
            }
        ),
        410,
    )
