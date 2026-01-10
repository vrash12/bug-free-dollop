# backend/app/models/user_daily_stats.py
from .. import db

class UserDailyStats(db.Model):
    __tablename__ = "user_daily_stats"

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    stat_date = db.Column(db.Date, nullable=False)
    total_points = db.Column(db.Integer, default=0, nullable=False)
    total_workouts = db.Column(db.Integer, default=0, nullable=False)
    total_reps = db.Column(db.Integer, default=0, nullable=False)
    total_duration_seconds = db.Column(db.Integer, default=0, nullable=False)

    user = db.relationship("User", backref="daily_stats")
