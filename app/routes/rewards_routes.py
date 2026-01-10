# backend/app/routes/rewards_routes.py
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from .. import db
from ..models.user import User
from ..models.social import Achievement, UserAchievement  # you already use these in social_routes

rewards_bp = Blueprint("rewards", __name__)


def _compute_next_level_points(level: int) -> int:
    """
    Simple level progression:
      - Level 1 -> 1000 pts
      - Level 2 -> 2000 pts total
      - Level 3 -> 3000 pts total
    You can swap this for something steeper later if you like.
    """
    if level < 1:
        level = 1
    return level * 1000


@rewards_bp.route("/overview", methods=["GET"])
@jwt_required()
def rewards_overview():
    """
    Returns:
    {
      "user": { ... user.to_dict() ... },
      "summary": {
        "total_points": 1200,
        "level": 5,
        "unlocked_achievements_count": 3,
        "total_achievements_count": 10,
        "next_level_points": 6000
      },
      "unlocked": [
        {
          "id": 1,
          "code": "first_workout",
          "name": "First Quest",
          "description": "...",
          "points_reward": 50,
          "unlocked_at": "2025-11-21T10:05:00"
        },
        ...
      ],
      "locked": [
        {
          "id": 3,
          "code": "workouts_25",
          "name": "Quest Grinder",
          "description": "...",
          "points_reward": 200
        },
        ...
      ]
    }
    """
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    if not user:
        return jsonify({"message": "user not found"}), 404

    total_points = user.total_points or 0
    level = user.level or 1
    next_level_points = _compute_next_level_points(level)

    # ------------------------------
    # 1) Unlocked achievements
    # ------------------------------
    ua_rows = (
        db.session.query(UserAchievement, Achievement)
        .join(Achievement, UserAchievement.achievement_id == Achievement.id)
        .filter(UserAchievement.user_id == user.id)
        .order_by(UserAchievement.unlocked_at.desc())
        .all()
    )

    unlocked = []
    unlocked_achievement_ids = []

    for ua, ach in ua_rows:
        unlocked_achievement_ids.append(ach.id)
        unlocked.append(
            {
                "id": ach.id,
                "code": ach.code,
                "name": ach.name,
                "description": ach.description,
                "points_reward": ach.points_reward,
                "unlocked_at": ua.unlocked_at.isoformat()
                if ua.unlocked_at
                else None,
            }
        )

    unlocked_count = len(unlocked)

    # ------------------------------
    # 2) Locked achievements
    # ------------------------------
    active_ach_q = Achievement.query.filter(Achievement.is_active.is_(True))
    total_achievements_count = active_ach_q.count()

    if unlocked_achievement_ids:
        locked_q = active_ach_q.filter(
            ~Achievement.id.in_(unlocked_achievement_ids)
        )
    else:
        locked_q = active_ach_q

    locked_rows = locked_q.order_by(Achievement.id.asc()).all()

    locked = []
    for ach in locked_rows:
        locked.append(
            {
                "id": ach.id,
                "code": ach.code,
                "name": ach.name,
                "description": ach.description,
                "points_reward": ach.points_reward,
            }
        )

    # ------------------------------
    # 3) Summary block
    # ------------------------------
    summary = {
        "total_points": int(total_points),
        "level": int(level),
        "unlocked_achievements_count": int(unlocked_count),
        "total_achievements_count": int(total_achievements_count),
        "next_level_points": int(next_level_points),
    }

    return (
        jsonify(
            {
                "user": user.to_dict(),
                "summary": summary,
                "unlocked": unlocked,
                "locked": locked,
            }
        ),
        200,
    )
