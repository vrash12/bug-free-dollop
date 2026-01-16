# backend/app/routes/exercise_routes.py

from collections import defaultdict

from flask import Blueprint, jsonify
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy import and_, or_

from ..models.workout import Workout

exercises_bp = Blueprint("exercises", __name__)

# -------------------------------------------------------------------
# Minimal metadata for each exercise.
# IMPORTANT: keep points_per_rep in sync with your frontend EXERCISE_PRESETS.basePointsPerRep
# (pushup=10, situp=8, squat=9, switch-lunges=11, dips=12, shoulder-taps=7, russian-twist=8,
#  pike-pushup=13, burpees=15, high-knees=6)
# -------------------------------------------------------------------
EXERCISES = {
    "pushup": {
        "id": "pushup",
        "name": "Pushup",
        "display_name": "Pushups",  # used for legacy title matching
        "level": "Intermediate",
        "focus": "Chest • Triceps • Core",
        "equipment": "Bodyweight",
        "points_per_rep": 10,
    },
    "situp": {
        "id": "situp",
        "name": "Situp",
        "display_name": "Situps",
        "level": "Beginner",
        "focus": "Core strength",
        "equipment": "Bodyweight",
        "points_per_rep": 8,
    },
    "squat": {
        "id": "squat",
        "name": "Squat",
        "display_name": "Squats",
        "level": "Intermediate",
        "focus": "Legs • Glutes • Core",
        "equipment": "Bodyweight",
        "points_per_rep": 9,
    },
    "switch-lunges": {
        "id": "switch-lunges",
        "name": "Switch Lunges",
        "display_name": "Switch Lunges",
        "level": "Intermediate",
        "focus": "Legs • Power • Conditioning",
        "equipment": "Bodyweight",
        "points_per_rep": 11,
    },
    "dips": {
        "id": "dips",
        "name": "Dips",
        "display_name": "Dips",
        "level": "Intermediate",
        "focus": "Triceps • Chest",
        "equipment": "Chair / Bench",
        "points_per_rep": 12,
    },
    "shoulder-taps": {
        "id": "shoulder-taps",
        "name": "Shoulder Taps",
        "display_name": "Shoulder Taps",
        "level": "Beginner",
        "focus": "Core • Anti-rotation",
        "equipment": "Bodyweight",
        "points_per_rep": 7,
    },
    "russian-twist": {
        "id": "russian-twist",
        "name": "Russian Twist",
        "display_name": "Russian Twists",
        "level": "Beginner",
        "focus": "Obliques • Core",
        "equipment": "Bodyweight",
        "points_per_rep": 8,
    },
    "pike-pushup": {
        "id": "pike-pushup",
        "name": "Pike Pushup",
        "display_name": "Pike Pushups",
        "level": "Advanced",
        "focus": "Shoulders • Triceps",
        "equipment": "Bodyweight",
        "points_per_rep": 13,
    },
    "burpees": {
        "id": "burpees",
        "name": "Burpees",
        "display_name": "Burpees",
        "level": "Advanced",
        "focus": "Full-body conditioning",
        "equipment": "Bodyweight",
        "points_per_rep": 15,
    },
    "high-knees": {
        "id": "high-knees",
        "name": "High Knees",
        "display_name": "High Knees",
        "level": "Beginner",
        "focus": "Cardio • Hip flexors",
        "equipment": "Bodyweight",
        "points_per_rep": 6,
    },
}


def _get_exercise(exercise_id: str):
    exercise_id = (exercise_id or "").lower()
    return exercise_id, EXERCISES.get(exercise_id)


@exercises_bp.route("/", methods=["GET"])
def list_exercises():
    """
    Public: list all exercises with basic metadata.

    GET /api/exercises
    """
    exercises = list(EXERCISES.values())
    exercises.sort(key=lambda x: (x.get("name") or x.get("id") or ""))
    return jsonify({"exercises": exercises}), 200


@exercises_bp.route("/<exercise_id>", methods=["GET"])
def get_exercise(exercise_id):
    """
    Public: get metadata for a single exercise.

    GET /api/exercises/<exercise_id>
    """
    exercise_id, exercise = _get_exercise(exercise_id)
    if not exercise:
        return jsonify({"message": "Exercise not found"}), 404
    return jsonify({"exercise": exercise}), 200


@exercises_bp.route("/<exercise_id>/dashboard", methods=["GET"])
@jwt_required()
def exercise_dashboard(exercise_id):
    """
    Per-exercise dashboard for the current user.

    GET /api/exercises/<exercise_id>/dashboard

    NEW: Prefer matching workouts by Workout.exercise_id (more reliable),
         but keep a legacy fallback for older rows using the title prefix.
    """
    current_user_id = int(get_jwt_identity())
    exercise_id, exercise = _get_exercise(exercise_id)
    if not exercise:
        return jsonify({"message": "Exercise not found"}), 404

    display_name = exercise.get("display_name") or exercise["name"]

    # Prefer Workout.exercise_id match; fallback to title prefix for older data
    q = (
        Workout.query.filter(
            Workout.user_id == current_user_id,
            or_(
                Workout.exercise_id == exercise_id,
                and_(
                    Workout.exercise_id.is_(None),
                    Workout.title.ilike(f"{display_name}%"),
                ),
            ),
        )
        .order_by(Workout.workout_date.desc(), Workout.started_at.desc())
    )

    workouts = q.all()

    total_sessions = len(workouts)
    total_points = sum((w.total_points_earned or 0) for w in workouts)
    total_duration = sum((w.total_duration_seconds or 0) for w in workouts)
    total_reps = sum((w.total_reps or 0) for w in workouts)

    avg_points = int(total_points / total_sessions) if total_sessions else 0
    avg_duration = int(total_duration / total_sessions) if total_sessions else 0
    avg_reps = int(total_reps / total_sessions) if total_sessions else 0

    most_recent = workouts[0].to_summary_dict() if workouts else None
    recent_sessions = [w.to_summary_dict() for w in workouts[:5]]

    # Breakdown by difficulty (easy/medium/hard/unknown)
    by_diff = defaultdict(lambda: {"sessions": 0, "points": 0, "reps": 0, "duration_seconds": 0})
    for w in workouts:
        d = (w.difficulty or "unknown").lower()
        by_diff[d]["sessions"] += 1
        by_diff[d]["points"] += int(w.total_points_earned or 0)
        by_diff[d]["reps"] += int(w.total_reps or 0)
        by_diff[d]["duration_seconds"] += int(w.total_duration_seconds or 0)

    # Best sessions (optional but useful)
    best_points_session = max(workouts, key=lambda w: (w.total_points_earned or 0), default=None)
    best_reps_session = max(workouts, key=lambda w: (w.total_reps or 0), default=None)

    return (
        jsonify(
            {
                "exercise": exercise,
                "stats": {
                    "total_sessions": total_sessions,
                    "total_points": total_points,
                    "total_reps": total_reps,
                    "total_duration_seconds": total_duration,
                    "average_points_per_session": avg_points,
                    "average_reps_per_session": avg_reps,
                    "average_duration_seconds_per_session": avg_duration,
                    "most_recent_session": most_recent,
                    "best_points_session": best_points_session.to_summary_dict() if best_points_session else None,
                    "best_reps_session": best_reps_session.to_summary_dict() if best_reps_session else None,
                },
                "by_difficulty": dict(by_diff),
                "recent_sessions": recent_sessions,
            }
        ),
        200,
    )
