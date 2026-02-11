"""Core data helpers for the social system."""

from __future__ import annotations

import threading
import uuid
from collections import defaultdict, deque
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import or_

from __init__ import db
from model.snakes_game import SnakesGameData
from model.social import (
    Conversation,
    ConversationMember,
    Friendship,
    Message,
    Party,
    PartyInvite,
    PartyMember,
    PresenceRecord,
    normalize_user_pair,
)
from model.user import User


ALLOWED_CHAT_IMAGE_PREFIX = "/uploads/social_chat/"
MESSAGE_MAX_LENGTH = 1200
MESSAGE_RATE_WINDOW_SECONDS = 8
MESSAGE_RATE_LIMIT = 10
MAX_CHAT_PAGE_SIZE = 60

PRESENCE_STATUSES = {"online", "away", "in-game", "offline"}
MANUAL_PRESENCE_STATUSES = {"online", "away"}

_presence_lock = threading.RLock()
_presence_runtime: Dict[int, Dict] = {}

_rate_lock = threading.RLock()
_send_rate: Dict[int, deque] = defaultdict(deque)


def utcnow_iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _get_or_create_runtime_presence(user_id: int) -> Dict:
    state = _presence_runtime.get(user_id)
    if state is None:
        state = {
            "sids": set(),
            "manual_status": "online",
            "activity": None,
            "last_seen": None,
            "status": "offline",
        }
        _presence_runtime[user_id] = state
    return state


def _resolve_runtime_status(state: Dict) -> str:
    if state.get("manual_status") == "away" and state.get("sids"):
        return "away"
    if state.get("activity") and state.get("sids"):
        return "in-game"
    if state.get("sids"):
        return "online"
    return "offline"


def _persist_presence(user_id: int, status: str, activity: Optional[Dict], last_seen: Optional[datetime]) -> None:
    record = PresenceRecord.query.filter_by(user_id=user_id).first()
    if record is None:
        record = PresenceRecord(user_id=user_id)
        db.session.add(record)

    record.status = status if status in PRESENCE_STATUSES else "offline"
    record.activity = None
    if activity:
        mode = str(activity.get("mode") or "").strip()
        target = str(activity.get("target") or "").strip()
        label = str(activity.get("label") or "").strip()
        record.activity = "|".join([mode, target, label]).strip("|")[:120] or None
    record.last_seen = last_seen
    db.session.commit()


def presence_online(user_id: int, sid: str) -> Dict:
    with _presence_lock:
        state = _get_or_create_runtime_presence(user_id)
        state["sids"].add(sid)
        if state.get("manual_status") not in MANUAL_PRESENCE_STATUSES:
            state["manual_status"] = "online"
        state["status"] = _resolve_runtime_status(state)
        if state["status"] != "offline":
            state["last_seen"] = None
        snapshot = {
            "user_id": user_id,
            "status": state["status"],
            "activity": state.get("activity"),
            "last_seen": utcnow_iso(state.get("last_seen")),
        }

    _persist_presence(user_id, snapshot["status"], snapshot["activity"], None)
    return snapshot


def presence_disconnect(user_id: int, sid: str) -> Dict:
    now = datetime.utcnow()
    with _presence_lock:
        state = _get_or_create_runtime_presence(user_id)
        if sid in state["sids"]:
            state["sids"].remove(sid)
        state["status"] = _resolve_runtime_status(state)
        if state["status"] == "offline":
            state["last_seen"] = now
        snapshot = {
            "user_id": user_id,
            "status": state["status"],
            "activity": state.get("activity"),
            "last_seen": utcnow_iso(state.get("last_seen")),
        }

    _persist_presence(
        user_id,
        snapshot["status"],
        snapshot["activity"],
        now if snapshot["status"] == "offline" else None,
    )
    return snapshot


def presence_set_manual(user_id: int, manual_status: str) -> Dict:
    manual = str(manual_status or "").strip().lower()
    if manual not in MANUAL_PRESENCE_STATUSES:
        manual = "online"
    with _presence_lock:
        state = _get_or_create_runtime_presence(user_id)
        state["manual_status"] = manual
        state["status"] = _resolve_runtime_status(state)
        snapshot = {
            "user_id": user_id,
            "status": state["status"],
            "activity": state.get("activity"),
            "last_seen": utcnow_iso(state.get("last_seen")),
        }
    _persist_presence(user_id, snapshot["status"], snapshot["activity"], state.get("last_seen"))
    return snapshot


def presence_set_activity(user_id: int, activity: Optional[Dict]) -> Dict:
    with _presence_lock:
        state = _get_or_create_runtime_presence(user_id)
        normalized = None
        if activity:
            normalized = {
                "mode": str(activity.get("mode") or "").strip()[:32],
                "target": str(activity.get("target") or "").strip()[:80],
                "label": str(activity.get("label") or "").strip()[:80],
            }
            if not (normalized["mode"] or normalized["target"] or normalized["label"]):
                normalized = None
        state["activity"] = normalized
        state["status"] = _resolve_runtime_status(state)
        snapshot = {
            "user_id": user_id,
            "status": state["status"],
            "activity": state.get("activity"),
            "last_seen": utcnow_iso(state.get("last_seen")),
        }
    _persist_presence(user_id, snapshot["status"], snapshot["activity"], state.get("last_seen"))
    return snapshot


def get_presence_snapshot(user_id: int) -> Dict:
    with _presence_lock:
        if user_id in _presence_runtime:
            state = _presence_runtime[user_id]
            return {
                "user_id": user_id,
                "status": state.get("status", "offline"),
                "activity": state.get("activity"),
                "last_seen": utcnow_iso(state.get("last_seen")),
            }

    record = PresenceRecord.query.filter_by(user_id=user_id).first()
    if not record:
        return {"user_id": user_id, "status": "offline", "activity": None, "last_seen": None}
    return {
        "user_id": user_id,
        "status": record.status or "offline",
        "activity": None,
        "last_seen": utcnow_iso(record.last_seen),
    }


def user_summary(user_id: int) -> Dict:
    user = User.query.get(user_id)
    if not user:
        return {
            "id": int(user_id),
            "uid": "unknown",
            "username": "Unknown",
            "avatar_url": None,
            "character": None,
        }

    avatar_url = None
    if user.pfp:
        avatar_url = f"/uploads/{user.uid}/{user.pfp}"

    character = None
    profile = SnakesGameData.query.filter_by(user_id=user.id).first()
    if profile and profile.selected_character and profile.selected_character != "default":
        character = profile.selected_character

    return {
        "id": int(user.id),
        "uid": user.uid,
        "username": user.name or user.uid,
        "avatar_url": avatar_url,
        "character": character,
    }


def get_friendship(user_a: int, user_b: int) -> Optional[Friendship]:
    low, high = normalize_user_pair(user_a, user_b)
    return Friendship.query.filter_by(pair_low_user_id=low, pair_high_user_id=high).first()


def are_friends(user_a: int, user_b: int) -> bool:
    row = get_friendship(user_a, user_b)
    return bool(row and row.status == "accepted")


def is_blocked_pair(user_a: int, user_b: int) -> bool:
    row = get_friendship(user_a, user_b)
    return bool(row and row.status == "blocked")


def search_users(query: str, current_user_id: int, limit: int = 20) -> List[Dict]:
    if not query:
        return []
    term = f"%{str(query).strip()}%"
    rows = (
        User.query.filter(User.id != current_user_id)
        .filter(or_(User._uid.ilike(term), User._name.ilike(term)))
        .order_by(User._name.asc())
        .limit(max(1, min(limit, 40)))
        .all()
    )
    return [user_summary(row.id) for row in rows]


def _friend_row_to_payload(current_user_id: int, row: Friendship) -> Dict:
    other_user_id = row.other_user_id if row.user_id == current_user_id else row.user_id
    summary = user_summary(other_user_id)
    presence = get_presence_snapshot(other_user_id)
    summary.update(
        {
            "friendship_id": row.id,
            "status": row.status,
            "presence": presence.get("status"),
            "last_seen": presence.get("last_seen"),
            "activity": presence.get("activity"),
        }
    )
    return summary


def get_friends_state(user_id: int) -> Dict:
    rows = Friendship.query.filter(
        or_(Friendship.user_id == user_id, Friendship.other_user_id == user_id)
    ).all()

    friends = []
    pending_in = []
    pending_out = []
    blocked = []

    for row in rows:
        payload = _friend_row_to_payload(user_id, row)
        if row.status == "accepted":
            friends.append(payload)
        elif row.status == "pending":
            if row.requested_by == user_id:
                pending_out.append(payload)
            else:
                pending_in.append(payload)
        elif row.status == "blocked":
            if row.requested_by == user_id:
                blocked.append(payload)

    def sort_key(item: Dict):
        order = {"online": 0, "in-game": 1, "away": 2, "offline": 3}
        return (order.get(item.get("presence"), 4), item.get("username", "").lower())

    friends.sort(key=sort_key)
    pending_in.sort(key=lambda item: item.get("username", "").lower())
    pending_out.sort(key=lambda item: item.get("username", "").lower())
    blocked.sort(key=lambda item: item.get("username", "").lower())

    return {
        "friends": friends,
        "pending_in": pending_in,
        "pending_out": pending_out,
        "blocked": blocked,
    }


def send_friend_request(requester_id: int, target_user_id: int) -> Tuple[bool, str, Optional[Friendship]]:
    if requester_id == target_user_id:
        return False, "Cannot friend yourself", None

    target = User.query.get(target_user_id)
    if not target:
        return False, "User not found", None

    existing = get_friendship(requester_id, target_user_id)
    if existing:
        if existing.status == "accepted":
            return False, "Already friends", existing
        if existing.status == "blocked":
            return False, "Cannot send request to blocked user", existing
        if existing.status == "pending":
            if existing.requested_by == requester_id:
                return False, "Request already sent", existing
            existing.status = "accepted"
            existing.requested_by = requester_id
            existing.updated_at = datetime.utcnow()
            db.session.commit()
            return True, "Friend request accepted", existing

    row = Friendship(
        user_id=requester_id,
        other_user_id=target_user_id,
        requested_by=requester_id,
        status="pending",
    )
    row.sync_pair()
    db.session.add(row)
    db.session.commit()
    return True, "Friend request sent", row


def accept_friend_request(current_user_id: int, request_id: Optional[int], user_id: Optional[int]) -> Tuple[bool, str]:
    row = None
    if request_id:
        row = Friendship.query.filter_by(id=request_id, status="pending").first()
    elif user_id:
        row = get_friendship(current_user_id, user_id)
        if row and row.status != "pending":
            row = None

    if not row:
        return False, "Pending request not found"

    if row.requested_by == current_user_id:
        return False, "Cannot accept your own outgoing request"

    if current_user_id not in (row.user_id, row.other_user_id):
        return False, "Not authorized for this request"

    row.status = "accepted"
    row.updated_at = datetime.utcnow()
    db.session.commit()
    return True, "Friend request accepted"


def decline_friend_request(current_user_id: int, request_id: Optional[int], user_id: Optional[int]) -> Tuple[bool, str]:
    row = None
    if request_id:
        row = Friendship.query.filter_by(id=request_id, status="pending").first()
    elif user_id:
        row = get_friendship(current_user_id, user_id)
        if row and row.status != "pending":
            row = None

    if not row:
        return False, "Pending request not found"

    if current_user_id not in (row.user_id, row.other_user_id):
        return False, "Not authorized for this request"
    if row.requested_by == current_user_id:
        return False, "Cannot decline your own outgoing request"

    db.session.delete(row)
    db.session.commit()
    return True, "Friend request declined"


def remove_friend(user_id: int, friend_user_id: int) -> Tuple[bool, str]:
    row = get_friendship(user_id, friend_user_id)
    if not row or row.status != "accepted":
        return False, "Friendship not found"
    db.session.delete(row)
    db.session.commit()
    return True, "Friend removed"


def block_user(actor_id: int, target_user_id: int) -> Tuple[bool, str]:
    if actor_id == target_user_id:
        return False, "Cannot block yourself"
    target = User.query.get(target_user_id)
    if not target:
        return False, "User not found"

    row = get_friendship(actor_id, target_user_id)
    if row is None:
        row = Friendship(
            user_id=actor_id,
            other_user_id=target_user_id,
            requested_by=actor_id,
            status="blocked",
        )
        row.sync_pair()
        db.session.add(row)
    else:
        row.status = "blocked"
        row.requested_by = actor_id
        row.updated_at = datetime.utcnow()

    db.session.commit()
    return True, "User blocked"


def get_friends_user_ids(user_id: int) -> List[int]:
    rows = Friendship.query.filter(
        Friendship.status == "accepted",
        or_(Friendship.user_id == user_id, Friendship.other_user_id == user_id),
    ).all()
    out = []
    for row in rows:
        out.append(row.other_user_id if row.user_id == user_id else row.user_id)
    return out


def get_party_for_user(user_id: int) -> Optional[Party]:
    membership = PartyMember.query.filter_by(user_id=user_id).first()
    if membership:
        return Party.query.get(membership.party_id)
    return None


def get_or_create_party_conversation(party_id: int) -> Conversation:
    conversation = Conversation.query.filter_by(type="party", party_id=party_id).first()
    if conversation:
        return conversation
    conversation = Conversation(type="party", party_id=party_id)
    db.session.add(conversation)
    db.session.flush()
    return conversation


def sync_party_conversation_members(party_id: int) -> None:
    conversation = get_or_create_party_conversation(party_id)
    party_members = PartyMember.query.filter_by(party_id=party_id).all()
    member_ids = {row.user_id for row in party_members}

    existing_rows = ConversationMember.query.filter_by(conversation_id=conversation.id).all()
    existing_ids = {row.user_id for row in existing_rows}

    for user_id in member_ids - existing_ids:
        db.session.add(
            ConversationMember(
                conversation_id=conversation.id,
                user_id=user_id,
            )
        )
    for row in existing_rows:
        if row.user_id not in member_ids:
            db.session.delete(row)

    db.session.commit()


def create_party(leader_id: int) -> Party:
    existing = get_party_for_user(leader_id)
    if existing:
        return existing

    party = Party(leader_id=leader_id, invite_code=uuid.uuid4().hex[:8])
    db.session.add(party)
    db.session.flush()
    db.session.add(PartyMember(party_id=party.id, user_id=leader_id, role="leader"))
    get_or_create_party_conversation(party.id)
    db.session.commit()
    sync_party_conversation_members(party.id)
    return party


def _ensure_party_member(user_id: int, party_id: int) -> Optional[PartyMember]:
    return PartyMember.query.filter_by(user_id=user_id, party_id=party_id).first()


def invite_to_party(party_id: int, inviter_id: int, invitee_id: int) -> Tuple[bool, str, Optional[PartyInvite]]:
    party = Party.query.get(party_id)
    if not party:
        return False, "Party not found", None
    inviter_member = _ensure_party_member(inviter_id, party_id)
    if not inviter_member:
        return False, "Not in party", None
    if _ensure_party_member(invitee_id, party_id):
        return False, "User already in party", None
    if not are_friends(inviter_id, invitee_id):
        return False, "Can only invite friends", None

    existing = (
        PartyInvite.query.filter_by(
            party_id=party_id,
            invitee_id=invitee_id,
            status="pending",
        )
        .order_by(PartyInvite.created_at.desc())
        .first()
    )
    if existing and existing.expires_at and existing.expires_at > datetime.utcnow():
        return False, "Invite already pending", existing

    row = PartyInvite(
        party_id=party_id,
        inviter_id=inviter_id,
        invitee_id=invitee_id,
        status="pending",
    )
    db.session.add(row)
    db.session.commit()
    return True, "Party invite sent", row


def _expire_invite_if_needed(invite: PartyInvite) -> None:
    if invite.status != "pending":
        return
    if invite.expires_at and invite.expires_at < datetime.utcnow():
        invite.status = "expired"
        invite.updated_at = datetime.utcnow()
        db.session.commit()


def respond_party_invite(invite_id: int, user_id: int, accept: bool) -> Tuple[bool, str, Optional[int]]:
    invite = PartyInvite.query.filter_by(id=invite_id).first()
    if not invite:
        return False, "Invite not found", None
    if invite.invitee_id != user_id:
        return False, "Not authorized", None

    _expire_invite_if_needed(invite)
    if invite.status != "pending":
        return False, "Invite is no longer pending", None

    party = Party.query.get(invite.party_id)
    if not party:
        invite.status = "expired"
        invite.updated_at = datetime.utcnow()
        db.session.commit()
        return False, "Party no longer exists", None

    if not accept:
        invite.status = "declined"
        invite.updated_at = datetime.utcnow()
        db.session.commit()
        return True, "Party invite declined", party.id

    existing_party = get_party_for_user(user_id)
    if existing_party and existing_party.id != party.id:
        return False, "Leave your current party first", None

    membership = _ensure_party_member(user_id, party.id)
    if not membership:
        db.session.add(PartyMember(party_id=party.id, user_id=user_id, role="member"))

    invite.status = "accepted"
    invite.updated_at = datetime.utcnow()
    db.session.commit()
    sync_party_conversation_members(party.id)
    return True, "Joined party", party.id


def _destroy_party_if_empty(party_id: int) -> bool:
    remaining = PartyMember.query.filter_by(party_id=party_id).count()
    if remaining > 0:
        return False

    conversation = Conversation.query.filter_by(type="party", party_id=party_id).first()
    if conversation:
        db.session.delete(conversation)
    party = Party.query.get(party_id)
    if party:
        db.session.delete(party)
    db.session.commit()
    return True


def leave_party(user_id: int, party_id: int) -> Tuple[bool, str]:
    party = Party.query.get(party_id)
    if not party:
        return False, "Party not found"
    member = _ensure_party_member(user_id, party_id)
    if not member:
        return False, "Not in party"

    db.session.delete(member)
    db.session.flush()

    remaining_members = PartyMember.query.filter_by(party_id=party_id).order_by(PartyMember.joined_at.asc()).all()
    if remaining_members:
        if party.leader_id == user_id:
            new_leader = remaining_members[0]
            party.leader_id = new_leader.user_id
            for row in remaining_members:
                row.role = "leader" if row.user_id == new_leader.user_id else "member"
    db.session.commit()
    sync_party_conversation_members(party_id)
    _destroy_party_if_empty(party_id)
    return True, "Left party"


def kick_party_member(actor_id: int, party_id: int, target_user_id: int) -> Tuple[bool, str]:
    party = Party.query.get(party_id)
    if not party:
        return False, "Party not found"
    if party.leader_id != actor_id:
        return False, "Only party leader can kick members"
    if target_user_id == actor_id:
        return False, "Leader cannot kick self"

    member = _ensure_party_member(target_user_id, party_id)
    if not member:
        return False, "Member not found"

    db.session.delete(member)
    db.session.commit()
    sync_party_conversation_members(party_id)
    _destroy_party_if_empty(party_id)
    return True, "Member kicked"


def transfer_party_leader(actor_id: int, party_id: int, new_leader_id: int) -> Tuple[bool, str]:
    party = Party.query.get(party_id)
    if not party:
        return False, "Party not found"
    if party.leader_id != actor_id:
        return False, "Only party leader can transfer leadership"

    new_leader_member = _ensure_party_member(new_leader_id, party_id)
    if not new_leader_member:
        return False, "Target user is not in party"

    party.leader_id = new_leader_id
    members = PartyMember.query.filter_by(party_id=party_id).all()
    for row in members:
        row.role = "leader" if row.user_id == new_leader_id else "member"
    db.session.commit()
    return True, "Party leader transferred"


def serialize_party(party: Optional[Party], for_user_id: Optional[int] = None) -> Optional[Dict]:
    if not party:
        return None

    members = PartyMember.query.filter_by(party_id=party.id).order_by(PartyMember.joined_at.asc()).all()
    payload_members = []
    for row in members:
        summary = user_summary(row.user_id)
        presence = get_presence_snapshot(row.user_id)
        payload_members.append(
            {
                **summary,
                "role": row.role,
                "presence": presence.get("status", "offline"),
                "last_seen": presence.get("last_seen"),
                "activity": presence.get("activity"),
                "joined_at": utcnow_iso(row.joined_at),
            }
        )

    pending_invites = []
    invites = PartyInvite.query.filter_by(party_id=party.id, status="pending").all()
    now = datetime.utcnow()
    for invite in invites:
        if invite.expires_at and invite.expires_at < now:
            continue
        pending_invites.append(
            {
                "id": invite.id,
                "party_id": invite.party_id,
                "inviter_id": invite.inviter_id,
                "invitee_id": invite.invitee_id,
                "status": invite.status,
                "created_at": utcnow_iso(invite.created_at),
                "expires_at": utcnow_iso(invite.expires_at),
                "inviter": user_summary(invite.inviter_id),
                "invitee": user_summary(invite.invitee_id),
            }
        )

    conversation = Conversation.query.filter_by(type="party", party_id=party.id).first()
    return {
        "id": party.id,
        "leader_id": party.leader_id,
        "invite_code": party.invite_code,
        "created_at": utcnow_iso(party.created_at),
        "conversation_id": conversation.id if conversation else None,
        "members": payload_members,
        "pending_invites": pending_invites,
        "is_leader": bool(for_user_id and party.leader_id == for_user_id),
    }


def get_pending_party_invites_for_user(user_id: int) -> List[Dict]:
    rows = (
        PartyInvite.query.filter_by(invitee_id=user_id, status="pending")
        .order_by(PartyInvite.created_at.desc())
        .all()
    )
    out = []
    now = datetime.utcnow()
    for invite in rows:
        if invite.expires_at and invite.expires_at < now:
            continue
        out.append(
            {
                "id": invite.id,
                "party_id": invite.party_id,
                "inviter_id": invite.inviter_id,
                "invitee_id": invite.invitee_id,
                "status": invite.status,
                "created_at": utcnow_iso(invite.created_at),
                "expires_at": utcnow_iso(invite.expires_at),
                "inviter": user_summary(invite.inviter_id),
                "party_summary": serialize_party(Party.query.get(invite.party_id), user_id),
            }
        )
    return out


def get_or_create_dm_conversation(user_a: int, user_b: int) -> Conversation:
    low, high = normalize_user_pair(user_a, user_b)
    conversation = Conversation.query.filter_by(
        type="dm",
        dm_low_user_id=low,
        dm_high_user_id=high,
    ).first()
    if conversation:
        return conversation

    conversation = Conversation(type="dm")
    conversation.sync_dm_pair(user_a, user_b)
    db.session.add(conversation)
    db.session.flush()
    db.session.add(ConversationMember(conversation_id=conversation.id, user_id=low))
    db.session.add(ConversationMember(conversation_id=conversation.id, user_id=high))
    db.session.commit()
    return conversation


def is_conversation_member(user_id: int, conversation_id: int) -> bool:
    row = ConversationMember.query.filter_by(conversation_id=conversation_id, user_id=user_id).first()
    return bool(row)


def conversation_member_ids(conversation_id: int) -> List[int]:
    rows = ConversationMember.query.filter_by(conversation_id=conversation_id).all()
    return [row.user_id for row in rows]


def compute_unread_count(user_id: int, conversation_id: int) -> int:
    member = ConversationMember.query.filter_by(conversation_id=conversation_id, user_id=user_id).first()
    if not member:
        return 0
    query = Message.query.filter(
        Message.conversation_id == conversation_id,
        Message.deleted_at.is_(None),
        Message.sender_id != user_id,
    )
    if member.last_read_message_id:
        query = query.filter(Message.id > member.last_read_message_id)
    return int(query.count())


def mark_conversation_read(user_id: int, conversation_id: int, last_read_message_id: Optional[int]) -> int:
    member = ConversationMember.query.filter_by(conversation_id=conversation_id, user_id=user_id).first()
    if not member:
        return 0
    if last_read_message_id:
        member.last_read_message_id = int(last_read_message_id)
    else:
        last = (
            Message.query.filter(
                Message.conversation_id == conversation_id,
                Message.deleted_at.is_(None),
            )
            .order_by(Message.id.desc())
            .first()
        )
        member.last_read_message_id = last.id if last else None
    db.session.commit()
    return compute_unread_count(user_id, conversation_id)


def serialize_message(row: Message) -> Dict:
    sender = user_summary(row.sender_id)
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "sender_id": row.sender_id,
        "sender": sender,
        "type": row.type,
        "body_text": row.body_text,
        "image_url": row.image_url,
        "created_at": utcnow_iso(row.created_at),
        "edited_at": utcnow_iso(row.edited_at),
        "deleted_at": utcnow_iso(row.deleted_at),
    }


def get_messages(conversation_id: int, limit: int = 40, before_message_id: Optional[int] = None) -> List[Dict]:
    page_size = max(1, min(limit, MAX_CHAT_PAGE_SIZE))
    query = Message.query.filter(
        Message.conversation_id == conversation_id,
        Message.deleted_at.is_(None),
    )
    if before_message_id:
        query = query.filter(Message.id < int(before_message_id))

    rows = query.order_by(Message.id.desc()).limit(page_size).all()
    rows.reverse()
    return [serialize_message(row) for row in rows]


def _conversation_title_for_user(user_id: int, conversation: Conversation) -> Dict:
    if conversation.type == "dm":
        members = conversation_member_ids(conversation.id)
        other_id = None
        for member_id in members:
            if member_id != user_id:
                other_id = member_id
                break
        if other_id is None:
            return {"name": "Direct Message", "avatar_url": None, "target_user_id": None}
        other = user_summary(other_id)
        return {
            "name": other.get("username"),
            "avatar_url": other.get("avatar_url"),
            "target_user_id": other.get("id"),
        }
    if conversation.type == "party":
        party = Party.query.get(conversation.party_id) if conversation.party_id else None
        if party:
            leader = user_summary(party.leader_id)
            return {
                "name": f"Party ({leader.get('username')})",
                "avatar_url": leader.get("avatar_url"),
                "target_user_id": None,
            }
        return {"name": "Party Chat", "avatar_url": None, "target_user_id": None}
    return {"name": "Group Chat", "avatar_url": None, "target_user_id": None}


def serialize_conversation(user_id: int, conversation: Conversation) -> Dict:
    title = _conversation_title_for_user(user_id, conversation)
    last_message = (
        Message.query.filter(
            Message.conversation_id == conversation.id,
            Message.deleted_at.is_(None),
        )
        .order_by(Message.id.desc())
        .first()
    )

    unread = compute_unread_count(user_id, conversation.id)
    payload = {
        "id": conversation.id,
        "type": conversation.type,
        "party_id": conversation.party_id,
        "created_at": utcnow_iso(conversation.created_at),
        "title": title,
        "unread_count": unread,
        "last_message": serialize_message(last_message) if last_message else None,
        "member_ids": conversation_member_ids(conversation.id),
    }
    return payload


def get_chat_list(user_id: int) -> List[Dict]:
    memberships = ConversationMember.query.filter_by(user_id=user_id).all()
    out = []
    for membership in memberships:
        conversation = Conversation.query.get(membership.conversation_id)
        if not conversation:
            continue
        if conversation.type == "dm":
            members = conversation_member_ids(conversation.id)
            if len(members) >= 2 and not are_friends(members[0], members[1]):
                continue
        if conversation.type == "party":
            party = get_party_for_user(user_id)
            if not party or party.id != conversation.party_id:
                continue
        out.append(serialize_conversation(user_id, conversation))
    out.sort(
        key=lambda item: (
            item["last_message"]["id"] if item.get("last_message") else 0,
            item["id"],
        ),
        reverse=True,
    )
    return out


def send_message(
    user_id: int,
    conversation_id: int,
    msg_type: str,
    body_text: Optional[str],
    image_url: Optional[str],
) -> Tuple[bool, str, Optional[Message]]:
    if not is_conversation_member(user_id, conversation_id):
        return False, "Not a conversation member", None

    conversation = Conversation.query.get(conversation_id)
    if not conversation:
        return False, "Conversation not found", None

    message_type = str(msg_type or "text").strip().lower()
    if message_type not in {"text", "emoji", "image"}:
        return False, "Unsupported message type", None

    text = None
    if body_text is not None:
        text = str(body_text).strip()

    if message_type in {"text", "emoji"}:
        if not text:
            return False, "Message content is required", None
        if len(text) > MESSAGE_MAX_LENGTH:
            return False, f"Message exceeds {MESSAGE_MAX_LENGTH} characters", None
    if message_type == "image":
        if not image_url:
            return False, "Image URL is required", None
        if not str(image_url).startswith(ALLOWED_CHAT_IMAGE_PREFIX):
            return False, "Invalid image URL", None
        if text and len(text) > MESSAGE_MAX_LENGTH:
            return False, f"Caption exceeds {MESSAGE_MAX_LENGTH} characters", None

    row = Message(
        conversation_id=conversation_id,
        sender_id=user_id,
        type=message_type,
        body_text=text,
        image_url=image_url if message_type == "image" else None,
    )
    db.session.add(row)
    db.session.commit()
    return True, "Message sent", row


def chat_send_rate_limited(user_id: int) -> bool:
    now = datetime.utcnow().timestamp()
    with _rate_lock:
        entries = _send_rate[user_id]
        while entries and now - entries[0] > MESSAGE_RATE_WINDOW_SECONDS:
            entries.popleft()
        if len(entries) >= MESSAGE_RATE_LIMIT:
            return True
        entries.append(now)
        return False
