# backend/app/models/user.py
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from .. import db

class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.BigInteger, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(100))
    avatar_url = db.Column(db.String(255))

    total_points = db.Column(db.Integer, default=0)
    level = db.Column(db.Integer, default=1)
    current_streak_days = db.Column(db.Integer, default=0)
    longest_streak_days = db.Column(db.Integer, default=0)
    last_active_date = db.Column(db.Date)

    # NEW FIELDS
    gender = db.Column(db.Enum("male", "female", "other", name="gender_enum"))
    birth_date = db.Column(db.Date)
    height_cm = db.Column(db.Numeric(5, 2))
    weight_kg = db.Column(db.Numeric(5, 2))
    target_weight_kg = db.Column(db.Numeric(5, 2))
    fitness_level = db.Column(
        db.Enum("beginner", "intermediate", "advanced", name="fitness_level_enum")
    )
    fitness_goal = db.Column(
        db.Enum("lose_weight", "gain_muscle", "get_fitter", name="fitness_goal_enum")
    )
    has_completed_onboarding = db.Column(db.Boolean, default=False, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "username": self.username,
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
            "total_points": self.total_points,
            "level": self.level,
            "current_streak_days": self.current_streak_days,
            "longest_streak_days": self.longest_streak_days,
            # NEW:
            "gender": self.gender,
            "birth_date": self.birth_date.isoformat() if self.birth_date else None,
            "height_cm": float(self.height_cm) if self.height_cm is not None else None,
            "weight_kg": float(self.weight_kg) if self.weight_kg is not None else None,
            "target_weight_kg": float(self.target_weight_kg) if self.target_weight_kg is not None else None,
            "fitness_level": self.fitness_level,
            "fitness_goal": self.fitness_goal,
            "has_completed_onboarding": self.has_completed_onboarding,
        }
