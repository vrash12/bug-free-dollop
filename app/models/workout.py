# backend/app/models/workout.py
from datetime import datetime
from .. import db

class Workout(db.Model):
    __tablename__ = "workouts"

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    title = db.Column(db.String(100))
    workout_date = db.Column(db.Date, nullable=False)
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime)

    total_duration_seconds = db.Column(db.Integer)
    total_points_earned = db.Column(db.Integer, default=0)
    calories_estimate = db.Column(db.Integer)

    # NEW: preset / plan metadata
    exercise_id = db.Column(db.String(50))      # e.g. "pushup"
    difficulty = db.Column(db.String(20))       # "easy" | "medium" | "hard"
    preset_id = db.Column(db.String(50))        # e.g. "pushup-m-1"
    target_sets = db.Column(db.Integer)
    target_reps = db.Column(db.Integer)
    total_reps = db.Column(db.Integer, default=0)

    user = db.relationship("User", backref="workouts")

    def to_summary_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "workout_date": self.workout_date.isoformat() if self.workout_date else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "total_duration_seconds": self.total_duration_seconds or 0,
            "total_points_earned": self.total_points_earned or 0,
            "calories_estimate": self.calories_estimate,
            "exercise_id": self.exercise_id,
            "difficulty": self.difficulty,
            "preset_id": self.preset_id,
            "target_sets": self.target_sets,
            "target_reps": self.target_reps,
            "total_reps": self.total_reps or 0,
        }
