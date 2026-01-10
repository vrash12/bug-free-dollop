# backend/app/models/social.py
from datetime import datetime
from .. import db


# -----------------------------
# Achievements
# -----------------------------
class Achievement(db.Model):
    __tablename__ = "achievements"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(255))
    points_reward = db.Column(db.Integer, nullable=False, default=0)
    condition_type = db.Column(
        db.Enum(
            "first_workout",
            "total_workouts",
            "total_reps",
            "total_points",
            "streak_days",
            "custom",
            name="achievement_condition_type",
        ),
        nullable=False,
    )
    condition_value = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)


class UserAchievement(db.Model):
    __tablename__ = "user_achievements"

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    achievement_id = db.Column(db.Integer, db.ForeignKey("achievements.id"), nullable=False)
    unlocked_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow
    )

    user = db.relationship("User", backref="user_achievements")
    achievement = db.relationship("Achievement", backref="user_achievements")


# -----------------------------
# Friendships
# -----------------------------
class Friendship(db.Model):
    __tablename__ = "friendships"

    id = db.Column(db.BigInteger, primary_key=True)
    requester_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    addressee_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(
        db.Enum("pending", "accepted", "blocked", name="friendship_status"),
        nullable=False,
        default="pending",
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    requester = db.relationship(
        "User", foreign_keys=[requester_id], backref="sent_friendships"
    )
    addressee = db.relationship(
        "User", foreign_keys=[addressee_id], backref="received_friendships"
    )


# -----------------------------
# Challenges
# -----------------------------
class Challenge(db.Model):
    __tablename__ = "challenges"

    id = db.Column(db.BigInteger, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(255))
    created_by = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    metric_type = db.Column(
        db.Enum(
            "reps",
            "points",
            "duration_seconds",
            "workouts",
            name="challenge_metric_type",
        ),
        nullable=False,
    )
    target_value = db.Column(db.Integer)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    creator = db.relationship("User", backref="created_challenges")
    participants = db.relationship(
        "ChallengeParticipant",
        back_populates="challenge",
        cascade="all, delete-orphan",
    )


class ChallengeParticipant(db.Model):
    __tablename__ = "challenge_participants"

    id = db.Column(db.BigInteger, primary_key=True)
    challenge_id = db.Column(db.BigInteger, db.ForeignKey("challenges.id"), nullable=False)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    progress_value = db.Column(db.Integer, nullable=False, default=0)
    rank_cache = db.Column(db.Integer)
    last_updated = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    challenge = db.relationship("Challenge", back_populates="participants")
    user = db.relationship("User", backref="challenge_participations")


# -----------------------------
# Exercises & sessions (pose + per-exercise stats)
# -----------------------------
class Exercise(db.Model):
    __tablename__ = "exercises"

    id = db.Column(db.SmallInteger, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50))
    description = db.Column(db.String(255))
    difficulty_level = db.Column(
        db.Enum("easy", "medium", "hard", name="exercise_difficulty"),
        nullable=False,
        default="easy",
    )
    points_per_rep = db.Column(db.Integer, nullable=False, default=1)
    points_per_minute = db.Column(db.Integer, nullable=False, default=1)

    sessions = db.relationship(
        "ExerciseSession", back_populates="exercise", cascade="all, delete-orphan"
    )


class ExerciseSession(db.Model):
    __tablename__ = "exercise_sessions"

    id = db.Column(db.BigInteger, primary_key=True)
    workout_id = db.Column(db.BigInteger, db.ForeignKey("workouts.id"), nullable=False)
    exercise_id = db.Column(db.SmallInteger, db.ForeignKey("exercises.id"), nullable=False)
    sequence_order = db.Column(db.Integer, nullable=False, default=1)
    started_at = db.Column(db.DateTime, nullable=False)
    ended_at = db.Column(db.DateTime)
    reps_count = db.Column(db.Integer, nullable=False, default=0)
    duration_seconds = db.Column(db.Integer, nullable=False, default=0)
    avg_form_score = db.Column(db.Numeric(5, 2))
    max_form_score = db.Column(db.Numeric(5, 2))

    workout = db.relationship("Workout", backref="exercise_sessions")
    exercise = db.relationship("Exercise", back_populates="sessions")
    pose_metrics = db.relationship(
        "PoseMetric", back_populates="exercise_session", cascade="all, delete-orphan"
    )


class PoseMetric(db.Model):
    __tablename__ = "pose_metrics"

    id = db.Column(db.BigInteger, primary_key=True)
    exercise_session_id = db.Column(
        db.BigInteger,
        db.ForeignKey("exercise_sessions.id"),
        nullable=False,
    )
    metric_name = db.Column(db.String(100), nullable=False)
    metric_value = db.Column(db.Numeric(10, 4))
    # assuming MySQL 5.7+/8 with JSON support
    metric_json = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    exercise_session = db.relationship("ExerciseSession", back_populates="pose_metrics")


# -----------------------------
# Global leaderboard view (read-only)
# -----------------------------
class GlobalLeaderboard(db.Model):
    """
    Read-only mapping to the `global_leaderboard` view.
    """
    __tablename__ = "global_leaderboard"

    user_id = db.Column(db.BigInteger, primary_key=True)
    username = db.Column(db.String(50))
    display_name = db.Column(db.String(100))
    avatar_url = db.Column(db.String(255))
    total_points = db.Column(db.Integer)
    level = db.Column(db.Integer)
    current_streak_days = db.Column(db.Integer)
    longest_streak_days = db.Column(db.Integer)
