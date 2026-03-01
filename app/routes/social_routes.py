# backend/app/routes/social_routes.py
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy import and_, or_

from .. import db
from ..models.user import User
from ..models.workout import Workout
from ..models.social import (
    Achievement,
    Challenge,
    ChallengeItem,
    ChallengeParticipant,
    Friendship,
    GlobalLeaderboard,
    UserAchievement,
)

social_bp = Blueprint("social", __name__)

VALID_METRICS = {"reps", "points", "duration_seconds", "workouts"}
VALID_FRIEND_STATUS = {"pending", "accepted", "blocked"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower().replace("_", "-")


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except Exception:
        return default


def _parse_iso_date(val: Optional[str], default: date) -> date:
    if not val:
        return default
    try:
        return date.fromisoformat(val)
    except Exception:
        raise ValueError("invalid date format (expected YYYY-MM-DD)")


def _get_friend_ids(current_user_id: int) -> List[int]:
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
            friend_ids.add(int(f.addressee_id))
        else:
            friend_ids.add(int(f.requester_id))
    return list(friend_ids)


def _find_friendship(a: int, b: int) -> Optional[Friendship]:
    return Friendship.query.filter(
        or_(
            and_(Friendship.requester_id == a, Friendship.addressee_id == b),
            and_(Friendship.requester_id == b, Friendship.addressee_id == a),
        )
    ).first()


def _challenge_items_or_legacy(ch: Challenge) -> List[Dict[str, Any]]:
    """
    Returns list of normalized items used by progress computation + API payload.

    If ch.items exists -> use them.
    Else -> build a single "legacy item" from ch.exercise_id/difficulty/preset_id/target_value.
    If even exercise_id is empty -> returns one catch-all item (no filters) if target exists.
    """
    items: List[Dict[str, Any]] = []

    if getattr(ch, "items", None):
        for it in ch.items:
            items.append(
                {
                    "id": int(it.id) if it.id else None,
                    "exercise_id": (it.exercise_id or "").strip() or None,
                    "difficulty": (it.difficulty or "").strip() or None,
                    "preset_id": (it.preset_id or "").strip() or None,
                    "target_value": int(it.target_value or 0),
                }
            )
        return items

    # legacy single-item
    legacy_ex = (getattr(ch, "exercise_id", None) or "").strip()
    legacy_diff = (getattr(ch, "difficulty", None) or "").strip()
    legacy_preset = (getattr(ch, "preset_id", None) or "").strip()

    items.append(
        {
            "id": None,
            "exercise_id": legacy_ex or None,
            "difficulty": legacy_diff or None,
            "preset_id": legacy_preset or None,
            # legacy: ch.target_value is total; item target is same
            "target_value": int(getattr(ch, "target_value", 0) or 0),
        }
    )
    return items


def _workout_matches_item(w: Workout, item: Dict[str, Any]) -> bool:
    ex = item.get("exercise_id")
    diff = item.get("difficulty")
    preset = item.get("preset_id")

    if ex and _norm(getattr(w, "exercise_id", None)) != _norm(ex):
        return False
    if diff and _norm(getattr(w, "difficulty", None)) != _norm(diff):
        return False
    if preset and (getattr(w, "preset_id", None) or "").strip().lower() != preset.strip().lower():
        return False
    return True


def _compute_progress_value(ch: Challenge, user_id: int) -> int:
    """
    Compute user's progress based on completed workouts within date range.
    Supports multi-item challenges.

    Metric sources:
      - workouts: count matching workouts
      - points: sum Workout.total_points_earned
      - duration_seconds: sum Workout.total_duration_seconds
      - reps: sum Workout.total_reps
    """
    rows = (
        Workout.query.filter(
            Workout.user_id == user_id,
            Workout.workout_date >= ch.start_date,
            Workout.workout_date <= ch.end_date,
            Workout.ended_at.isnot(None),
        )
        .order_by(Workout.started_at.desc())
        .all()
    )

    metric = (ch.metric_type or "").lower().strip()
    if metric not in VALID_METRICS:
        metric = "reps"

    items = _challenge_items_or_legacy(ch)

    # If challenge has no meaningful filters (exercise_id empty),
    # treat it as a global "all workouts" challenge. (common for duration challenges)
    # In that case, we don't want to "loop items" because that could double-count.
    only_item = items[0] if items else {}
    no_filter_global = (
        len(items) == 1
        and not only_item.get("exercise_id")
        and not only_item.get("difficulty")
        and not only_item.get("preset_id")
    )

    def sum_metric(matching: List[Workout]) -> int:
        if metric == "workouts":
            return len(matching)
        if metric == "points":
            return sum(int(getattr(w, "total_points_earned", 0) or 0) for w in matching)
        if metric == "duration_seconds":
            return sum(int(getattr(w, "total_duration_seconds", 0) or 0) for w in matching)
        # reps default
        return sum(int(getattr(w, "total_reps", 0) or 0) for w in matching)

    if no_filter_global:
        return int(sum_metric(rows))

    total = 0
    for item in items:
        matching = [w for w in rows if _workout_matches_item(w, item)]
        total += int(sum_metric(matching))

    return int(total)


def _challenge_payload(
    ch: Challenge,
    current_user_id: int,
    participant: Optional[ChallengeParticipant],
    include_items: bool = True,
) -> Dict[str, Any]:
    items = _challenge_items_or_legacy(ch) if include_items else []

    payload: Dict[str, Any] = {
        "id": int(ch.id),
        "name": ch.name,
        "description": ch.description,
        "created_by": int(ch.created_by),
        "is_creator": int(ch.created_by) == int(current_user_id),
        "metric_type": ch.metric_type,
        "target_value": int(ch.target_value or 0),
        "progress_value": int(getattr(participant, "progress_value", 0) or 0) if participant else 0,
        "joined": participant is not None,
        "start_date": ch.start_date.isoformat(),
        "end_date": ch.end_date.isoformat(),
        "is_active": bool(ch.is_active),
    }

    # legacy convenience fields (if single-item)
    if len(items) == 1 and items[0].get("exercise_id"):
        payload["exercise_id"] = items[0].get("exercise_id")
        payload["difficulty"] = items[0].get("difficulty")
        payload["preset_id"] = items[0].get("preset_id")
    else:
        payload["exercise_id"] = None
        payload["difficulty"] = None
        payload["preset_id"] = None

    if include_items:
        payload["items"] = items

    # derived status
    today = date.today()
    if ch.end_date < today:
        payload["status"] = "ended"
    elif ch.start_date > today:
        payload["status"] = "upcoming"
    else:
        payload["status"] = "active"

    return payload


# ---------------------------------------------------------------------------
# Leaderboard + Achievements (READ)
# ---------------------------------------------------------------------------

@social_bp.route("/leaderboard", methods=["GET"])
@jwt_required()
def global_leaderboard():
    limit = _safe_int(request.args.get("limit", 100), 100)
    limit = max(1, min(limit, 500))

    rows = (
        GlobalLeaderboard.query.order_by(GlobalLeaderboard.total_points.desc())
        .limit(limit)
        .all()
    )

    return jsonify(
        {
            "leaderboard": [
                {
                    "user_id": int(r.user_id),
                    "username": r.username,
                    "display_name": r.display_name,
                    "avatar_url": r.avatar_url,
                    "total_points": int(r.total_points or 0),
                    "level": int(r.level or 1),
                    "current_streak_days": int(r.current_streak_days or 0),
                    "longest_streak_days": int(r.longest_streak_days or 0),
                }
                for r in rows
            ]
        }
    ), 200


@social_bp.route("/achievements", methods=["GET"])
@jwt_required()
def achievements_list():
    current_user_id = int(get_jwt_identity())

    achievements = Achievement.query.filter(Achievement.is_active.is_(True)).order_by(Achievement.id.asc()).all()
    unlocked_rows = (
        UserAchievement.query.filter(UserAchievement.user_id == current_user_id)
        .all()
    )
    unlocked_by_id = {int(ua.achievement_id): ua for ua in unlocked_rows}

    payload = []
    for a in achievements:
        ua = unlocked_by_id.get(int(a.id))
        payload.append(
            {
                "id": int(a.id),
                "code": a.code,
                "name": a.name,
                "description": a.description,
                "points_reward": int(a.points_reward or 0),
                "condition_type": a.condition_type,
                "condition_value": int(a.condition_value or 0),
                "unlocked": ua is not None,
                "unlocked_at": ua.unlocked_at.isoformat() if ua else None,
            }
        )

    return jsonify({"achievements": payload}), 200


# ---------------------------------------------------------------------------
# Friends CRUD
# ---------------------------------------------------------------------------

@social_bp.route("/friends", methods=["GET"])
@jwt_required()
def get_friends():
    current_user_id = int(get_jwt_identity())
    friend_ids = _get_friend_ids(current_user_id)
    if not friend_ids:
        return jsonify({"friends": []}), 200

    friends = User.query.filter(User.id.in_(friend_ids)).order_by(User.total_points.desc()).all()
    payload = []
    for u in friends:
        payload.append(
            {
                "id": int(u.id),
                "username": u.username,
                "display_name": u.display_name,
                "avatar_url": u.avatar_url,
                "total_points": int(u.total_points or 0),
                "current_streak_days": int(u.current_streak_days or 0),
                "last_active_date": u.last_active_date.isoformat() if getattr(u, "last_active_date", None) else None,
            }
        )
    return jsonify({"friends": payload}), 200


@social_bp.route("/friends/requests/incoming", methods=["GET"])
@jwt_required()
def incoming_requests():
    current_user_id = int(get_jwt_identity())

    rows = (
        db.session.query(Friendship, User)
        .join(User, Friendship.requester_id == User.id)
        .filter(Friendship.addressee_id == current_user_id, Friendship.status == "pending")
        .order_by(Friendship.created_at.desc())
        .all()
    )

    return jsonify(
        {
            "incoming": [
                {
                    "friendship_id": int(f.id),
                    "from_user": {
                        "id": int(u.id),
                        "username": u.username,
                        "display_name": u.display_name,
                        "avatar_url": u.avatar_url,
                        "total_points": int(u.total_points or 0),
                    },
                    "created_at": f.created_at.isoformat() if f.created_at else None,
                }
                for f, u in rows
            ]
        }
    ), 200


@social_bp.route("/friends/requests/outgoing", methods=["GET"])
@jwt_required()
def outgoing_requests():
    current_user_id = int(get_jwt_identity())

    rows = (
        db.session.query(Friendship, User)
        .join(User, Friendship.addressee_id == User.id)
        .filter(Friendship.requester_id == current_user_id, Friendship.status == "pending")
        .order_by(Friendship.created_at.desc())
        .all()
    )

    return jsonify(
        {
            "outgoing": [
                {
                    "friendship_id": int(f.id),
                    "to_user": {
                        "id": int(u.id),
                        "username": u.username,
                        "display_name": u.display_name,
                        "avatar_url": u.avatar_url,
                        "total_points": int(u.total_points or 0),
                    },
                    "created_at": f.created_at.isoformat() if f.created_at else None,
                }
                for f, u in rows
            ]
        }
    ), 200


@social_bp.route("/friends/request", methods=["POST"])
@jwt_required()
def send_friend_request():
    current_user_id = int(get_jwt_identity())
    data = request.get_json() or {}

    friend_username = (data.get("username") or "").strip()
    friend_user_id = data.get("user_id")

    if friend_username:
        target = User.query.filter_by(username=friend_username).first()
    elif friend_user_id:
        target = User.query.get(int(friend_user_id))
    else:
        return jsonify({"message": "username or user_id is required"}), 400

    if not target:
        return jsonify({"message": "target user not found"}), 404
    if int(target.id) == current_user_id:
        return jsonify({"message": "cannot add yourself as a friend"}), 400

    existing = _find_friendship(current_user_id, int(target.id))
    if existing:
        if existing.status == "accepted":
            return jsonify({"message": "already friends"}), 200
        if existing.status == "pending":
            return jsonify({"message": "friend request already pending"}), 200
        if existing.status == "blocked":
            return jsonify({"message": "friendship is blocked"}), 403

    friendship = Friendship(requester_id=current_user_id, addressee_id=int(target.id), status="pending")
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


@social_bp.route("/friends/<int:friend_id>/decline", methods=["POST"])
@jwt_required()
def decline_friend_request(friend_id: int):
    current_user_id = int(get_jwt_identity())

    friendship = Friendship.query.filter_by(
        requester_id=friend_id,
        addressee_id=current_user_id,
        status="pending",
    ).first()
    if not friendship:
        return jsonify({"message": "no pending request from this user"}), 404

    db.session.delete(friendship)
    db.session.commit()
    return jsonify({"message": "friend request declined"}), 200


@social_bp.route("/friends/<int:user_id>/cancel", methods=["POST"])
@jwt_required()
def cancel_outgoing_request(user_id: int):
    current_user_id = int(get_jwt_identity())

    friendship = Friendship.query.filter_by(
        requester_id=current_user_id,
        addressee_id=user_id,
        status="pending",
    ).first()
    if not friendship:
        return jsonify({"message": "no outgoing request to this user"}), 404

    db.session.delete(friendship)
    db.session.commit()
    return jsonify({"message": "friend request cancelled"}), 200


@social_bp.route("/friends/<int:friend_id>", methods=["DELETE"])
@jwt_required()
def unfriend(friend_id: int):
    """
    Remove an accepted friendship in either direction.
    """
    current_user_id = int(get_jwt_identity())
    friendship = Friendship.query.filter(
        Friendship.status == "accepted",
        or_(
            and_(Friendship.requester_id == current_user_id, Friendship.addressee_id == friend_id),
            and_(Friendship.requester_id == friend_id, Friendship.addressee_id == current_user_id),
        ),
    ).first()

    if not friendship:
        return jsonify({"message": "not friends"}), 404

    db.session.delete(friendship)
    db.session.commit()
    return jsonify({"message": "friend removed"}), 200


@social_bp.route("/friends/<int:friend_id>/block", methods=["POST"])
@jwt_required()
def block_user(friend_id: int):
    current_user_id = int(get_jwt_identity())

    friendship = _find_friendship(current_user_id, friend_id)
    if friendship:
        friendship.status = "blocked"
    else:
        friendship = Friendship(requester_id=current_user_id, addressee_id=friend_id, status="blocked")
        db.session.add(friendship)

    db.session.commit()
    return jsonify({"message": "user blocked"}), 200


@social_bp.route("/friends/<int:friend_id>/unblock", methods=["POST"])
@jwt_required()
def unblock_user(friend_id: int):
    """
    Unblock only if YOU were the blocker (requester_id=current_user).
    """
    current_user_id = int(get_jwt_identity())

    friendship = Friendship.query.filter_by(
        requester_id=current_user_id,
        addressee_id=friend_id,
        status="blocked",
    ).first()
    if not friendship:
        return jsonify({"message": "no blocked relationship found"}), 404

    db.session.delete(friendship)
    db.session.commit()
    return jsonify({"message": "user unblocked"}), 200


# ---------------------------------------------------------------------------
# Activity feed (friends' workouts & achievements)
# ---------------------------------------------------------------------------

@social_bp.route("/activity", methods=["GET"])
@jwt_required()
def get_activity():
    current_user_id = int(get_jwt_identity())
    friend_ids = _get_friend_ids(current_user_id)
    if not friend_ids:
        return jsonify({"activity": []}), 200

    limit = _safe_int(request.args.get("limit", 20), 20)
    limit = max(1, min(limit, 100))

    workout_rows = (
        db.session.query(Workout, User)
        .join(User, Workout.user_id == User.id)
        .filter(Workout.user_id.in_(friend_ids))
        .order_by(Workout.started_at.desc())
        .limit(limit)
        .all()
    )

    workout_events = []
    for w, u in workout_rows:
        dt = w.started_at or w.workout_date
        desc_parts = []
        if w.total_duration_seconds:
            minutes = int(w.total_duration_seconds) // 60
            if minutes > 0:
                desc_parts.append(f"{minutes} min")
        if w.total_points_earned:
            desc_parts.append(f"{int(w.total_points_earned)} pts")
        description = "completed " + (" and ".join(desc_parts) if desc_parts else "a workout")

        workout_events.append(
            {
                "id": int(w.id),
                "friend_id": int(u.id),
                "friend_name": u.display_name or u.username,
                "type": "workout",
                "title": "Completed a workout",
                "description": description,
                "created_at": dt,
            }
        )

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
    for ua, ach, u in ua_rows:
        dt = ua.unlocked_at
        event_id = 1_000_000_000 + int(ua.id)
        achievement_events.append(
            {
                "id": int(event_id),
                "friend_id": int(u.id),
                "friend_name": u.display_name or u.username,
                "type": "achievement",
                "title": f"Unlocked achievement: {ach.name}",
                "description": ach.description or "unlocked a new achievement",
                "created_at": dt,
            }
        )

    all_events = workout_events + achievement_events
    all_events.sort(key=lambda e: e["created_at"], reverse=True)
    all_events = all_events[:limit]

    for e in all_events:
        if hasattr(e["created_at"], "isoformat"):
            e["created_at"] = e["created_at"].isoformat()

    return jsonify({"activity": all_events}), 200


# ---------------------------------------------------------------------------
# Challenges CRUD
# ---------------------------------------------------------------------------

@social_bp.route("/challenges", methods=["GET"])
@jwt_required()
def get_challenges():
    """
    Query params:
      scope=active|all|upcoming|ended  (default active)
      include_items=1|0 (default 1)
    """
    current_user_id = int(get_jwt_identity())
    today = date.today()

    scope = (request.args.get("scope") or "active").strip().lower()
    include_items = (request.args.get("include_items") or "1").strip() != "0"

    q = Challenge.query.filter(Challenge.is_active.is_(True))

    if scope == "active":
        q = q.filter(Challenge.start_date <= today, Challenge.end_date >= today)
    elif scope == "upcoming":
        q = q.filter(Challenge.start_date > today)
    elif scope == "ended":
        q = q.filter(Challenge.end_date < today)
    else:
        # all active (including upcoming + active)
        q = q.filter(Challenge.end_date >= (today.replace(year=today.year - 5)))  # harmless

    challenges = q.order_by(Challenge.start_date.asc()).all()

    participants = (
        ChallengeParticipant.query.filter(
            ChallengeParticipant.user_id == current_user_id,
            ChallengeParticipant.challenge_id.in_([c.id for c in challenges] or [0]),
        ).all()
    )
    participant_by_ch = {int(p.challenge_id): p for p in participants}

    # refresh current user's progress best-effort (keeps list accurate)
    changed = False
    for ch in challenges:
        p = participant_by_ch.get(int(ch.id))
        if not p:
            continue
        computed = _compute_progress_value(ch, current_user_id)
        if int(p.progress_value or 0) != int(computed):
            p.progress_value = int(computed)
            changed = True

    if changed:
        db.session.commit()

    payload = []
    for ch in challenges:
        p = participant_by_ch.get(int(ch.id))
        payload.append(_challenge_payload(ch, current_user_id, p, include_items=include_items))

    return jsonify({"challenges": payload}), 200


@social_bp.route("/challenges/<int:challenge_id>", methods=["GET"])
@jwt_required()
def get_challenge_detail(challenge_id: int):
    current_user_id = int(get_jwt_identity())

    ch = Challenge.query.get(challenge_id)
    if not ch or not ch.is_active:
        return jsonify({"message": "challenge not found"}), 404

    participant = ChallengeParticipant.query.filter_by(
        challenge_id=challenge_id, user_id=current_user_id
    ).first()

    if participant:
        computed = _compute_progress_value(ch, current_user_id)
        if int(participant.progress_value or 0) != int(computed):
            participant.progress_value = int(computed)
            db.session.commit()

    return jsonify({"challenge": _challenge_payload(ch, current_user_id, participant, include_items=True)}), 200


@social_bp.route("/challenges", methods=["POST"])
@jwt_required()
def create_challenge():
    """
    Body (multi-items):
    {
      "name": "Preset Mix",
      "description": "...",
      "metric_type": "reps" | "points" | "duration_seconds" | "workouts",
      "start_date": "YYYY-MM-DD",
      "end_date": "YYYY-MM-DD",
      "items": [
        {"exercise_id":"pushup","difficulty":"medium","preset_id":"pushup-m-1","target_value":126},
        {"exercise_id":"squat","difficulty":"easy","preset_id":"squat-e-1","target_value":80}
      ]
    }

    Legacy single-item (still accepted):
    {
      ...,
      "exercise_id":"pushup","difficulty":"medium","preset_id":"pushup-m-1",
      "target_value":126
    }
    """
    current_user_id = int(get_jwt_identity())
    data = request.get_json() or {}

    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"message": "name is required"}), 400

    metric_type = (data.get("metric_type") or "").strip().lower()
    if metric_type not in VALID_METRICS:
        return jsonify({"message": "invalid metric_type"}), 400

    # dates
    try:
        start_date = _parse_iso_date(data.get("start_date"), default=date.today())
        end_date = _parse_iso_date(data.get("end_date"), default=start_date)
    except ValueError as e:
        return jsonify({"message": str(e)}), 400

    if end_date < start_date:
        return jsonify({"message": "end_date must be >= start_date"}), 400

    description = (data.get("description") or "").strip() or None

    items_in = data.get("items")

    # Build items
    items: List[Dict[str, Any]] = []
    if isinstance(items_in, list) and len(items_in) > 0:
        for it in items_in:
            if not isinstance(it, dict):
                continue
            ex = (it.get("exercise_id") or "").strip()
            if not ex:
                return jsonify({"message": "each item requires exercise_id"}), 400
            tv = _safe_int(it.get("target_value"), 0)
            if tv <= 0:
                return jsonify({"message": "each item requires target_value > 0"}), 400

            items.append(
                {
                    "exercise_id": ex,
                    "difficulty": (it.get("difficulty") or "").strip() or None,
                    "preset_id": (it.get("preset_id") or "").strip() or None,
                    "target_value": int(tv),
                }
            )
    else:
        # legacy single-item
        tv = _safe_int(data.get("target_value"), 0)
        if tv <= 0:
            return jsonify({"message": "target_value is required (> 0)"}), 400
        items.append(
            {
                "exercise_id": (data.get("exercise_id") or "").strip() or None,
                "difficulty": (data.get("difficulty") or "").strip() or None,
                "preset_id": (data.get("preset_id") or "").strip() or None,
                "target_value": int(tv),
            }
        )

    total_target = sum(int(i["target_value"]) for i in items)
    if total_target <= 0:
        return jsonify({"message": "total target must be > 0"}), 400

    ch = Challenge(
        name=name,
        description=description,
        created_by=current_user_id,
        start_date=start_date,
        end_date=end_date,
        metric_type=metric_type,
        target_value=int(total_target),
        is_active=True,
        created_at=datetime.utcnow(),
    )

    # keep legacy columns if exactly 1 filtered item
    if len(items) == 1 and items[0].get("exercise_id"):
        ch.exercise_id = items[0].get("exercise_id")
        ch.difficulty = items[0].get("difficulty")
        ch.preset_id = items[0].get("preset_id")
    else:
        ch.exercise_id = None
        ch.difficulty = None
        ch.preset_id = None

    db.session.add(ch)
    db.session.flush()  # ch.id

    # insert challenge_items if multi OR if you want to always use items table
    # We'll always store items in challenge_items (so "mix & match" works consistently).
    for it in items:
        # if item has no exercise_id (global), skip storing as item
        if not it.get("exercise_id"):
            continue
        db.session.add(
            ChallengeItem(
                challenge_id=int(ch.id),
                exercise_id=(it.get("exercise_id") or "").strip(),
                difficulty=(it.get("difficulty") or None),
                preset_id=(it.get("preset_id") or None),
                target_value=int(it.get("target_value") or 0),
            )
        )

    # auto-join creator with computed progress
    progress = _compute_progress_value(ch, current_user_id)
    participant = ChallengeParticipant(
        challenge_id=int(ch.id),
        user_id=current_user_id,
        progress_value=int(progress),
    )
    db.session.add(participant)

    db.session.commit()

    return jsonify({"challenge": _challenge_payload(ch, current_user_id, participant, include_items=True)}), 201


@social_bp.route("/challenges/<int:challenge_id>", methods=["PUT"])
@jwt_required()
def update_challenge(challenge_id: int):
    current_user_id = int(get_jwt_identity())
    data = request.get_json() or {}

    ch = Challenge.query.get(challenge_id)
    if not ch or not ch.is_active:
        return jsonify({"message": "challenge not found"}), 404

    if int(ch.created_by) != current_user_id:
        return jsonify({"message": "only the creator can update this challenge"}), 403

    name = (data.get("name") or ch.name).strip()
    if not name:
        return jsonify({"message": "name is required"}), 400

    metric_type = (data.get("metric_type") or ch.metric_type or "").strip().lower()
    if metric_type not in VALID_METRICS:
        return jsonify({"message": "invalid metric_type"}), 400

    # dates (optional)
    try:
        start_date = _parse_iso_date(data.get("start_date"), default=ch.start_date)
        end_date = _parse_iso_date(data.get("end_date"), default=ch.end_date)
    except ValueError as e:
        return jsonify({"message": str(e)}), 400

    if end_date < start_date:
        return jsonify({"message": "end_date must be >= start_date"}), 400

    description = (data.get("description") or ch.description or "").strip() or None

    # items (optional replace)
    items_in = data.get("items")
    new_items: Optional[List[Dict[str, Any]]] = None
    if items_in is not None:
        if not isinstance(items_in, list) or len(items_in) == 0:
            return jsonify({"message": "items must be a non-empty array"}), 400
        new_items = []
        for it in items_in:
            if not isinstance(it, dict):
                continue
            ex = (it.get("exercise_id") or "").strip()
            if not ex:
                return jsonify({"message": "each item requires exercise_id"}), 400
            tv = _safe_int(it.get("target_value"), 0)
            if tv <= 0:
                return jsonify({"message": "each item requires target_value > 0"}), 400
            new_items.append(
                {
                    "exercise_id": ex,
                    "difficulty": (it.get("difficulty") or "").strip() or None,
                    "preset_id": (it.get("preset_id") or "").strip() or None,
                    "target_value": int(tv),
                }
            )

    # apply updates
    ch.name = name
    ch.metric_type = metric_type
    ch.start_date = start_date
    ch.end_date = end_date
    ch.description = description

    if new_items is not None:
        # replace items
        ChallengeItem.query.filter_by(challenge_id=int(ch.id)).delete()
        for it in new_items:
            db.session.add(
                ChallengeItem(
                    challenge_id=int(ch.id),
                    exercise_id=it["exercise_id"],
                    difficulty=it.get("difficulty"),
                    preset_id=it.get("preset_id"),
                    target_value=int(it["target_value"]),
                )
            )

        total_target = sum(int(i["target_value"]) for i in new_items)
        ch.target_value = int(total_target)

        if len(new_items) == 1 and new_items[0].get("exercise_id"):
            ch.exercise_id = new_items[0].get("exercise_id")
            ch.difficulty = new_items[0].get("difficulty")
            ch.preset_id = new_items[0].get("preset_id")
        else:
            ch.exercise_id = None
            ch.difficulty = None
            ch.preset_id = None

    db.session.flush()

    # recompute progress for all participants (so rankings stay consistent)
    participants = ChallengeParticipant.query.filter_by(challenge_id=int(ch.id)).all()
    for p in participants:
        p.progress_value = int(_compute_progress_value(ch, int(p.user_id)))

    db.session.commit()

    participant = ChallengeParticipant.query.filter_by(
        challenge_id=int(ch.id), user_id=current_user_id
    ).first()

    return jsonify({"challenge": _challenge_payload(ch, current_user_id, participant, include_items=True)}), 200


@social_bp.route("/challenges/<int:challenge_id>", methods=["DELETE"])
@jwt_required()
def delete_challenge(challenge_id: int):
    current_user_id = int(get_jwt_identity())

    ch = Challenge.query.get(challenge_id)
    if not ch or not ch.is_active:
        return jsonify({"message": "challenge not found"}), 404

    if int(ch.created_by) != current_user_id:
        return jsonify({"message": "only the creator can delete this challenge"}), 403

    ch.is_active = False
    db.session.commit()
    return jsonify({"message": "challenge deleted"}), 200


@social_bp.route("/challenges/<int:challenge_id>/join", methods=["POST"])
@jwt_required()
def join_challenge(challenge_id: int):
    current_user_id = int(get_jwt_identity())

    ch = Challenge.query.get(challenge_id)
    if not ch or not ch.is_active:
        return jsonify({"message": "challenge not found or inactive"}), 404

    today = date.today()
    if not (ch.start_date <= today <= ch.end_date):
        return jsonify({"message": "challenge not currently running"}), 400

    existing = ChallengeParticipant.query.filter_by(
        challenge_id=challenge_id, user_id=current_user_id
    ).first()
    if existing:
        return jsonify({"message": "already joined"}), 200

    progress = _compute_progress_value(ch, current_user_id)
    participant = ChallengeParticipant(
        challenge_id=challenge_id,
        user_id=current_user_id,
        progress_value=int(progress),
    )
    db.session.add(participant)
    db.session.commit()

    return jsonify({"message": "joined challenge", "challenge_id": challenge_id}), 201


@social_bp.route("/challenges/<int:challenge_id>/leave", methods=["POST"])
@jwt_required()
def leave_challenge(challenge_id: int):
    current_user_id = int(get_jwt_identity())

    participant = ChallengeParticipant.query.filter_by(
        challenge_id=challenge_id, user_id=current_user_id
    ).first()
    if not participant:
        return jsonify({"message": "not a participant of this challenge"}), 404

    db.session.delete(participant)
    db.session.commit()
    return jsonify({"message": "left challenge", "challenge_id": challenge_id}), 200


@social_bp.route("/challenges/<int:challenge_id>/refresh", methods=["POST"])
@jwt_required()
def refresh_challenge_progress(challenge_id: int):
    current_user_id = int(get_jwt_identity())

    ch = Challenge.query.get(challenge_id)
    if not ch or not ch.is_active:
        return jsonify({"message": "challenge not found"}), 404

    participant = ChallengeParticipant.query.filter_by(
        challenge_id=challenge_id, user_id=current_user_id
    ).first()
    if not participant:
        return jsonify({"message": "not a participant of this challenge"}), 404

    computed = _compute_progress_value(ch, current_user_id)
    participant.progress_value = int(computed)
    db.session.commit()

    target = int(ch.target_value or 0)
    pct = float(computed / target) if target > 0 else 0.0

    return jsonify({"progress_value": int(computed), "target_value": target, "pct": pct}), 200


@social_bp.route("/challenges/<int:challenge_id>/participants", methods=["GET"])
@jwt_required()
def challenge_participants(challenge_id: int):
    current_user_id = int(get_jwt_identity())

    ch = Challenge.query.get(challenge_id)
    if not ch or not ch.is_active:
        return jsonify({"message": "challenge not found"}), 404

    rows = (
        db.session.query(ChallengeParticipant, User)
        .join(User, ChallengeParticipant.user_id == User.id)
        .filter(ChallengeParticipant.challenge_id == challenge_id)
        .order_by(ChallengeParticipant.progress_value.desc(), User.total_points.desc())
        .all()
    )

    payload = []
    rank = 1
    for p, u in rows:
        payload.append(
            {
                "rank": rank,
                "user": {
                    "id": int(u.id),
                    "username": u.username,
                    "display_name": u.display_name,
                    "avatar_url": u.avatar_url,
                    "total_points": int(u.total_points or 0),
                },
                "progress_value": int(p.progress_value or 0),
                "last_updated": p.last_updated.isoformat() if p.last_updated else None,
                "is_you": int(u.id) == int(current_user_id),
            }
        )
        rank += 1

    return jsonify({"challenge_id": int(ch.id), "participants": payload}), 200