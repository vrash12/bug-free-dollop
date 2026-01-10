# backend/app/routes/social_routes.py
from datetime import date
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import or_, and_

from .. import db
from ..models.user import User
from ..models.workout import Workout
from ..models.social import (
    Friendship,
    Challenge,
    ChallengeParticipant,
    Achievement,
    UserAchievement,
)

social_bp = Blueprint("social", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_friend_ids(current_user_id: int) -> list[int]:
    friendships = Friendship.query.filter(
        Friendship.status == "accepted",
        or_(
            Friendship.requester_id == current_user_id,
            Friendship.addressee_id == current_user_id,
        ),
    ).all()

    friend_ids = set()
    for f in friendships:
        if f.requester_id == current_user_id:
            friend_ids.add(f.addressee_id)
        else:
            friend_ids.add(f.requester_id)
    return list(friend_ids)


# ---------------------------------------------------------------------------
# Friends
# ---------------------------------------------------------------------------

@social_bp.route("/friends", methods=["GET"])
@jwt_required()
def get_friends():
    """
    Returns:
    {
      "friends": [
        {
          "id": 2,
          "username": "alice",
          "display_name": "Alice",
          "avatar_url": "https://...",
          "total_points": 1234,
          "current_streak_days": 5,
          "last_active_date": "2025-11-20"
        },
        ...
      ]
    }
    """
    current_user_id = get_jwt_identity()

    friend_ids = _get_friend_ids(int(current_user_id))
    if not friend_ids:
        return jsonify({"friends": []}), 200

    friends = (
        User.query.filter(User.id.in_(friend_ids))
        .order_by(User.total_points.desc())
        .all()
    )

    payload = []
    for u in friends:
        payload.append(
            {
                "id": u.id,
                "username": u.username,
                "display_name": u.display_name,
                "avatar_url": u.avatar_url,
                "total_points": u.total_points or 0,
                "current_streak_days": u.current_streak_days or 0,
                # shown as-is in the app
                "last_active_date": u.last_active_date.isoformat()
                if getattr(u, "last_active_date", None)
                else None,
            }
        )

    return jsonify({"friends": payload}), 200


@social_bp.route("/friends/request", methods=["POST"])
@jwt_required()
def send_friend_request():
    """
    Body:
    {
      "username": "friendUsername"
      // OR "user_id": 2
    }
    """
    current_user_id = int(get_jwt_identity())
    data = request.get_json() or {}

    friend_username = data.get("username")
    friend_user_id = data.get("user_id")

    if friend_username:
        target = User.query.filter_by(username=friend_username).first()
    elif friend_user_id:
        target = User.query.get(friend_user_id)
    else:
        return jsonify({"message": "username or user_id is required"}), 400

    if not target:
        return jsonify({"message": "target user not found"}), 404

    if target.id == current_user_id:
        return jsonify({"message": "cannot add yourself as a friend"}), 400

    # Check if friendship already exists in either direction
    existing = Friendship.query.filter(
        or_(
            and_(
                Friendship.requester_id == current_user_id,
                Friendship.addressee_id == target.id,
            ),
            and_(
                Friendship.requester_id == target.id,
                Friendship.addressee_id == current_user_id,
            ),
        )
    ).first()

    if existing:
        if existing.status == "accepted":
            return jsonify({"message": "already friends"}), 200
        if existing.status == "pending":
            return jsonify({"message": "friend request already pending"}), 200
        if existing.status == "blocked":
            return jsonify({"message": "friendship is blocked"}), 403

    friendship = Friendship(
        requester_id=current_user_id,
        addressee_id=target.id,
        status="pending",
    )
    db.session.add(friendship)
    db.session.commit()

    return jsonify({"message": "friend request sent"}), 201


@social_bp.route("/friends/<int:friend_id>/accept", methods=["POST"])
@jwt_required()
def accept_friend_request(friend_id: int):
    current_user_id = int(get_jwt_identity())

    friendship = Friendship.query.filter_by(
        requester_id=friend_id,
        addressee_id=current_user_id,
        status="pending",
    ).first()

    if not friendship:
        return jsonify({"message": "no pending request from this user"}), 404

    friendship.status = "accepted"
    db.session.commit()

    return jsonify({"message": "friend request accepted"}), 200


@social_bp.route("/friends/<int:friend_id>/block", methods=["POST"])
@jwt_required()
def block_user(friend_id: int):
    current_user_id = int(get_jwt_identity())

    friendship = Friendship.query.filter(
        or_(
            and_(
                Friendship.requester_id == current_user_id,
                Friendship.addressee_id == friend_id,
            ),
            and_(
                Friendship.requester_id == friend_id,
                Friendship.addressee_id == current_user_id,
            ),
        )
    ).first()

    if friendship:
        friendship.status = "blocked"
    else:
        friendship = Friendship(
            requester_id=current_user_id,
            addressee_id=friend_id,
            status="blocked",
        )
        db.session.add(friendship)

    db.session.commit()
    return jsonify({"message": "user blocked"}), 200


# ---------------------------------------------------------------------------
# Activity feed (friends' workouts & achievements)
# ---------------------------------------------------------------------------

@social_bp.route("/activity", methods=["GET"])
@jwt_required()
def get_activity():
    """
    Returns:
    {
      "activity": [
        {
          "id": 123,               # numeric
          "friend_id": 2,
          "friend_name": "Alice",
          "type": "workout" | "achievement",
          "title": "Completed a workout",
          "description": "completed a 20 min workout",
          "created_at": "2025-11-21T12:34:56"
        },
        ...
      ]
    }
    """
    current_user_id = int(get_jwt_identity())
    friend_ids = _get_friend_ids(current_user_id)

    if not friend_ids:
        return jsonify({"activity": []}), 200

    limit = int(request.args.get("limit", 20))

    # --- recent workouts from friends ---
    workout_rows = (
        db.session.query(Workout, User)
        .join(User, Workout.user_id == User.id)
        .filter(Workout.user_id.in_(friend_ids))
        .order_by(Workout.started_at.desc())
        .limit(limit)
        .all()
    )

    workout_events = []
    for workout, user in workout_rows:
        dt = workout.started_at or workout.workout_date
        title = "Completed a workout"
        desc_parts = []
        if workout.total_duration_seconds:
            minutes = workout.total_duration_seconds // 60
            if minutes > 0:
                desc_parts.append(f"{minutes} min")
        if workout.total_points_earned:
            desc_parts.append(f"{workout.total_points_earned} pts")
        description = "completed " + (" and ".join(desc_parts) if desc_parts else "a workout")

        workout_events.append(
            {
                "id": workout.id,
                "friend_id": user.id,
                "friend_name": user.display_name or user.username,
                "type": "workout",
                "title": title,
                "description": description,
                "created_at": dt,
            }
        )

    # --- recent achievements from friends ---
    ua_rows = (
        db.session.query(UserAchievement, Achievement, User)
        .join(Achievement, UserAchievement.achievement_id == Achievement.id)
        .join(User, UserAchievement.user_id == User.id)
        .filter(UserAchievement.user_id.in_(friend_ids))
        .order_by(UserAchievement.unlocked_at.desc())
        .limit(limit)
        .all()
    )

    achievement_events = []
    for ua, ach, user in ua_rows:
        dt = ua.unlocked_at
        title = f'Unlocked achievement: {ach.name}'
        description = ach.description or "unlocked a new achievement"

        # offset ID to avoid collision with workout IDs
        event_id = 1_000_000_000 + ua.id

        achievement_events.append(
            {
                "id": event_id,
                "friend_id": user.id,
                "friend_name": user.display_name or user.username,
                "type": "achievement",
                "title": title,
                "description": description,
                "created_at": dt,
            }
        )

    all_events = workout_events + achievement_events
    all_events.sort(key=lambda e: e["created_at"], reverse=True)
    all_events = all_events[:limit]

    # convert created_at datetimes to ISO strings
    for e in all_events:
        if hasattr(e["created_at"], "isoformat"):
            e["created_at"] = e["created_at"].isoformat()

    return jsonify({"activity": all_events}), 200


# ---------------------------------------------------------------------------
# Challenges
# ---------------------------------------------------------------------------

@social_bp.route("/challenges", methods=["GET"])
@jwt_required()
def get_challenges():
    """
    Returns:
    {
      "challenges": [
        {
          "id": 1,
          "name": "Weekly Squat Challenge",
          "description": "Do 300 squats this week",
          "metric_type": "reps",
          "target_value": 300,
          "progress_value": 120,
          "start_date": "2025-11-18",
          "end_date": "2025-11-25"
        },
        ...
      ]
    }
    """
    current_user_id = int(get_jwt_identity())

    today = date.today()
    # Only active challenges that are in progress (or upcoming if you want)
    rows = (
        db.session.query(Challenge, ChallengeParticipant)
        .outerjoin(
            ChallengeParticipant,
            and_(
                ChallengeParticipant.challenge_id == Challenge.id,
                ChallengeParticipant.user_id == current_user_id,
            ),
        )
        .filter(
            Challenge.is_active.is_(True),
            Challenge.start_date <= today,
            Challenge.end_date >= today,
        )
        .order_by(Challenge.start_date.asc())
        .all()
    )

    challenges_payload = []
    for challenge, participant in rows:
        challenges_payload.append(
            {
                "id": challenge.id,
                "name": challenge.name,
                "description": challenge.description,
                "metric_type": challenge.metric_type,
                "target_value": challenge.target_value,
                "progress_value": participant.progress_value if participant else 0,
                "start_date": challenge.start_date.isoformat(),
                "end_date": challenge.end_date.isoformat(),
            }
        )

    return jsonify({"challenges": challenges_payload}), 200


@social_bp.route("/challenges/<int:challenge_id>/join", methods=["POST"])
@jwt_required()
def join_challenge(challenge_id: int):
    """
    Join an active challenge.
    """
    current_user_id = int(get_jwt_identity())

    challenge = Challenge.query.get(challenge_id)
    if not challenge or not challenge.is_active:
        return jsonify({"message": "challenge not found or inactive"}), 404

    today = date.today()
    if not (challenge.start_date <= today <= challenge.end_date):
        return jsonify({"message": "challenge not currently running"}), 400

    existing = ChallengeParticipant.query.filter_by(
        challenge_id=challenge_id,
        user_id=current_user_id,
    ).first()

    if existing:
        return jsonify({"message": "already joined"}), 200

    participant = ChallengeParticipant(
        challenge_id=challenge_id,
        user_id=current_user_id,
        progress_value=0,
    )
    db.session.add(participant)
    db.session.commit()

    return jsonify({"message": "joined challenge", "challenge_id": challenge_id}), 201


@social_bp.route("/challenges/<int:challenge_id>/leave", methods=["POST"])
@jwt_required()
def leave_challenge(challenge_id: int):
    """
    Leave a challenge by deleting the participant row.
    """
    current_user_id = int(get_jwt_identity())

    participant = ChallengeParticipant.query.filter_by(
        challenge_id=challenge_id,
        user_id=current_user_id,
    ).first()

    if not participant:
        return jsonify({"message": "not a participant of this challenge"}), 404

    db.session.delete(participant)
    db.session.commit()

    return jsonify({"message": "left challenge", "challenge_id": challenge_id}), 200
