# backend/app/routes/exercise_routes.py
from datetime import datetime

from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from ..models.workout import Workout

exercises_bp = Blueprint("exercises", __name__)

# Minimal metadata for each exercise.
# Now includes points_per_rep so both backend and frontend can use it.
EXERCISES = {
    "pushup": {
        "id": "pushup",
        "name": "Pushup",
        "display_name": "Pushups",           # how we name sessions
        "level": "Intermediate",
        "focus": "Chest • Triceps • Core",
        "equipment": "Bodyweight",
        "points_per_rep": 8,
    },
    "situp": {
        "id": "situp",
        "name": "Situp",
        "display_name": "Situps",
        "level": "Beginner",
        "focus": "Core strength",
        "equipment": "Bodyweight",
        "points_per_rep": 5,
    },
    "squat": {
        "id": "squat",
        "name": "Squat",
        "display_name": "Squats",
        "level": "Intermediate",
        "focus": "Legs • Glutes • Core",
        "equipment": "Bodyweight",
        "points_per_rep": 6,
    },
    "switch-lunges": {
        "id": "switch-lunges",
        "name": "Switch Lunges",
        "display_name": "Switch Lunges",
        "level": "Intermediate",
        "focus": "Legs • Power • Conditioning",
        "equipment": "Bodyweight",
        "points_per_rep": 8,
    },
    "dips": {
        "id": "dips",
        "name": "Dips",
        "display_name": "Dips",
        "level": "Intermediate",
        "focus": "Triceps • Chest",
        "equipment": "Chair / Bench",
        "points_per_rep": 9,
    },
    "shoulder-taps": {
        "id": "shoulder-taps",
        "name": "Shoulder Taps",
        "display_name": "Shoulder Taps",
        "level": "Beginner",
        "focus": "Core • Anti-rotation",
        "equipment": "Bodyweight",
        "points_per_rep": 3,
    },
    "russian-twist": {
        "id": "russian-twist",
        "name": "Russian Twist",
        "display_name": "Russian Twists",
        "level": "Beginner",
        "focus": "Obliques • Core",
        "equipment": "Bodyweight",
        "points_per_rep": 3,
    },
    "pike-pushup": {
        "id": "pike-pushup",
        "name": "Pike Pushup",
        "display_name": "Pike Pushups",
        "level": "Advanced",
        "focus": "Shoulders • Triceps",
        "equipment": "Bodyweight",
        "points_per_rep": 12,
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
        "points_per_rep": 2,
    },
}


@exercises_bp.route("/", methods=["GET"])
def list_exercises():
    """
    Public: list all exercises with basic metadata.

    GET /api/exercises
    """
    return jsonify({"exercises": list(EXERCISES.values())}), 200


@exercises_bp.route("/<exercise_id>", methods=["GET"])
def get_exercise(exercise_id):
    """
    Public: get metadata for a single exercise.

    GET /api/exercises/<exercise_id>
    """
    exercise_id = (exercise_id or "").lower()
    exercise = EXERCISES.get(exercise_id)
    if not exercise:
        return jsonify({"message": "Exercise not found"}), 404

    return jsonify({"exercise": exercise}), 200


@exercises_bp.route("/<exercise_id>/dashboard", methods=["GET"])
@jwt_required()
def exercise_dashboard(exercise_id):
    """
    Per-exercise dashboard for the current user.

    GET /api/exercises/<exercise_id>/dashboard

    We infer which workouts belong to this exercise by matching
    their title prefix (e.g. "Pushups session").
    In the frontend WorkoutSessionScreen we set title to
    `${displayName} session`.
    """
    current_user_id = int(get_jwt_identity())
    exercise_id = (exercise_id or "").lower()

    exercise = EXERCISES.get(exercise_id)
    if not exercise:
        return jsonify({"message": "Exercise not found"}), 404

    display_name = exercise.get("display_name") or exercise["name"]

    # Filter workouts for this user & exercise using title prefix
    q = (
        Workout.query.filter(
            Workout.user_id == current_user_id,
            Workout.title.ilike(f"{display_name}%"),
        )
        .order_by(Workout.workout_date.desc(), Workout.started_at.desc())
    )

    workouts = q.all()
    total_sessions = len(workouts)
    total_points = sum(w.total_points_earned or 0 for w in workouts)
    total_duration = sum(w.total_duration_seconds or 0 for w in workouts)

    average_points = int(total_points / total_sessions) if total_sessions else 0
    average_duration = (
        int(total_duration / total_sessions) if total_sessions else 0
    )

    most_recent = workouts[0].to_summary_dict() if workouts else None
    recent_sessions = [w.to_summary_dict() for w in workouts[:5]]

    return (
        jsonify(
            {
                "exercise": exercise,
                "stats": {
                    "total_sessions": total_sessions,
                    "total_points": total_points,
                    "total_duration_seconds": total_duration,
                    "average_points_per_session": average_points,
                    "average_duration_seconds_per_session": average_duration,
                    "most_recent_session": most_recent,
                },
                "recent_sessions": recent_sessions,
            }
        ),
        200,
    )
