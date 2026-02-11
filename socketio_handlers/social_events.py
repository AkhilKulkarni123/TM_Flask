"""Socket.IO handlers for the social system."""

from __future__ import annotations

from typing import Dict, List, Optional, Set

import jwt
from flask import current_app, request
from flask_login import current_user
from flask_socketio import emit, join_room, leave_room

from model.social import Conversation, ConversationMember, Friendship, Party, PartyInvite, PartyMember
from model.user import User
from socketio_handlers import social_core


SOCIAL_NAMESPACE = "/social"

_sid_to_user: Dict[str, int] = {}
_user_to_sids: Dict[int, Set[str]] = {}


def _to_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_user_from_socket() -> Optional[User]:
    try:
        if current_user and getattr(current_user, "is_authenticated", False):
            return User.query.get(int(current_user.id))
    except Exception:
        pass

    token_name = current_app.config.get("JWT_TOKEN_NAME", "jwt")
    token = request.cookies.get(token_name)
    if not token:
        return None

    try:
        payload = jwt.decode(token, current_app.config["SECRET_KEY"], algorithms=["HS256"])
        uid = payload.get("_uid")
        if not uid:
            return None
        return User.query.filter_by(_uid=uid).first()
    except Exception:
        return None


def _emit_error(message: str, sid: Optional[str] = None) -> None:
    emit("social_error", {"message": message}, to=sid)


def _user_room(user_id: int) -> str:
    return f"user:{user_id}"


def _party_room(party_id: int) -> str:
    return f"party:{party_id}"


def _conv_room(conversation_id: int) -> str:
    return f"conv:{conversation_id}"


def _refresh_friends_state(socketio, user_ids: List[int]) -> None:
    seen = set()
    for user_id in user_ids:
        if user_id in seen:
            continue
        seen.add(user_id)
        payload = social_core.get_friends_state(user_id)
        socketio.emit("friends_state", payload, room=_user_room(user_id), namespace=SOCIAL_NAMESPACE)


def _emit_presence_to_friends(socketio, user_id: int, snapshot: Dict) -> None:
    friends = social_core.get_friends_user_ids(user_id)
    payload = {
        "user_id": user_id,
        "status": snapshot.get("status", "offline"),
        "last_seen": snapshot.get("last_seen"),
        "activity": snapshot.get("activity"),
    }
    for friend_id in friends:
        socketio.emit("presence_update", payload, room=_user_room(friend_id), namespace=SOCIAL_NAMESPACE)
    socketio.emit("presence_update", payload, room=_user_room(user_id), namespace=SOCIAL_NAMESPACE)


def _emit_party_state_for_user(socketio, user_id: int) -> None:
    party = social_core.get_party_for_user(user_id)
    payload = {
        "party": social_core.serialize_party(party, user_id) if party else None,
        "incoming_invites": social_core.get_pending_party_invites_for_user(user_id),
    }
    socketio.emit("party_state", payload, room=_user_room(user_id), namespace=SOCIAL_NAMESPACE)


def _emit_party_state_for_members(socketio, party_id: Optional[int], extra_user_ids: Optional[List[int]] = None) -> None:
    user_ids = set(extra_user_ids or [])
    if party_id:
        members = PartyMember.query.filter_by(party_id=party_id).all()
        for row in members:
            user_ids.add(row.user_id)
    for uid in user_ids:
        _emit_party_state_for_user(socketio, uid)


def _emit_chat_list(socketio, user_id: int) -> None:
    payload = {"conversations": social_core.get_chat_list(user_id)}
    socketio.emit("chat_list", payload, room=_user_room(user_id), namespace=SOCIAL_NAMESPACE)


def _emit_chat_list_for_users(socketio, user_ids: List[int]) -> None:
    for user_id in sorted(set(user_ids)):
        _emit_chat_list(socketio, user_id)


def _join_user_context_rooms(user_id: int, sid: str) -> None:
    join_room(_user_room(user_id), sid=sid, namespace=SOCIAL_NAMESPACE)

    party = social_core.get_party_for_user(user_id)
    if party:
        join_room(_party_room(party.id), sid=sid, namespace=SOCIAL_NAMESPACE)

    memberships = ConversationMember.query.filter_by(user_id=user_id).all()
    for membership in memberships:
        join_room(_conv_room(membership.conversation_id), sid=sid, namespace=SOCIAL_NAMESPACE)


def _leave_user_context_rooms(user_id: int, sid: str) -> None:
    leave_room(_user_room(user_id), sid=sid, namespace=SOCIAL_NAMESPACE)

    party = social_core.get_party_for_user(user_id)
    if party:
        leave_room(_party_room(party.id), sid=sid, namespace=SOCIAL_NAMESPACE)

    memberships = ConversationMember.query.filter_by(user_id=user_id).all()
    for membership in memberships:
        leave_room(_conv_room(membership.conversation_id), sid=sid, namespace=SOCIAL_NAMESPACE)


def _leave_user_from_party_context(user_id: int, party_id: int) -> None:
    sids = _user_to_sids.get(user_id, set())
    conversation = Conversation.query.filter_by(type="party", party_id=party_id).first()
    for sid in sids:
        leave_room(_party_room(party_id), sid=sid, namespace=SOCIAL_NAMESPACE)
        if conversation:
            leave_room(_conv_room(conversation.id), sid=sid, namespace=SOCIAL_NAMESPACE)


def _refresh_unread_for_conversation(socketio, conversation_id: int) -> None:
    member_ids = social_core.conversation_member_ids(conversation_id)
    for member_id in member_ids:
        unread = social_core.compute_unread_count(member_id, conversation_id)
        socketio.emit(
            "chat_unread",
            {"conversation_id": conversation_id, "unread_count": unread},
            room=_user_room(member_id),
            namespace=SOCIAL_NAMESPACE,
        )


def init_social_socket(socketio) -> None:
    @socketio.on("connect", namespace=SOCIAL_NAMESPACE)
    def handle_connect():
        user = _get_user_from_socket()
        if not user:
            return False

        sid = request.sid
        user_id = int(user.id)
        _sid_to_user[sid] = user_id
        _user_to_sids.setdefault(user_id, set()).add(sid)

        _join_user_context_rooms(user_id, sid)
        snapshot = social_core.presence_online(user_id, sid)

        emit("friends_state", social_core.get_friends_state(user_id))
        _emit_party_state_for_user(socketio, user_id)
        _emit_chat_list(socketio, user_id)
        _emit_presence_to_friends(socketio, user_id, snapshot)

    @socketio.on("disconnect", namespace=SOCIAL_NAMESPACE)
    def handle_disconnect():
        sid = request.sid
        user_id = _sid_to_user.pop(sid, None)
        if not user_id:
            return

        sids = _user_to_sids.get(user_id, set())
        if sid in sids:
            sids.remove(sid)
        if not sids and user_id in _user_to_sids:
            _user_to_sids.pop(user_id, None)

        _leave_user_context_rooms(user_id, sid)
        snapshot = social_core.presence_disconnect(user_id, sid)
        _emit_presence_to_friends(socketio, user_id, snapshot)

    @socketio.on("presence_set", namespace=SOCIAL_NAMESPACE)
    def handle_presence_set(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")
        status = (data or {}).get("status")
        snapshot = social_core.presence_set_manual(user_id, status)
        _emit_presence_to_friends(socketio, user_id, snapshot)
        _emit_party_state_for_user(socketio, user_id)

    @socketio.on("social_activity_set", namespace=SOCIAL_NAMESPACE)
    def handle_social_activity_set(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")
        payload = data or {}
        activity = {
            "mode": payload.get("mode"),
            "target": payload.get("target"),
            "label": payload.get("label"),
        }
        snapshot = social_core.presence_set_activity(user_id, activity)
        _emit_presence_to_friends(socketio, user_id, snapshot)
        party = social_core.get_party_for_user(user_id)
        if party:
            _emit_party_state_for_members(socketio, party.id)

    # --------------------------- Friends events ---------------------------

    @socketio.on("friends_search", namespace=SOCIAL_NAMESPACE)
    def handle_friends_search(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")

        query = str((data or {}).get("query") or "").strip()
        results = social_core.search_users(query, user_id, limit=20)
        for row in results:
            friendship = social_core.get_friendship(user_id, row["id"])
            row["friendship_status"] = friendship.status if friendship else None
        emit("friends_search", {"query": query, "results": results})

    @socketio.on("friends_request_send", namespace=SOCIAL_NAMESPACE)
    def handle_friends_request_send(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")
        target_user_id = _to_int((data or {}).get("target_user_id"))
        if not target_user_id:
            return _emit_error("target_user_id is required")

        ok, message, row = social_core.send_friend_request(user_id, target_user_id)
        if not ok:
            return _emit_error(message)

        _refresh_friends_state(socketio, [user_id, target_user_id])
        if row and row.status == "pending":
            socketio.emit(
                "friend_request_received",
                {
                    "request_id": row.id,
                    "from_user": social_core.user_summary(user_id),
                },
                room=_user_room(target_user_id),
                namespace=SOCIAL_NAMESPACE,
            )

    @socketio.on("friends_request_accept", namespace=SOCIAL_NAMESPACE)
    def handle_friends_request_accept(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")

        request_id = _to_int((data or {}).get("request_id"))
        other_user_id = _to_int((data or {}).get("user_id"))
        target_ids = [user_id]
        if other_user_id:
            target_ids.append(other_user_id)
        if request_id:
            row = Friendship.query.get(request_id)
            if row:
                target_ids.extend([row.user_id, row.other_user_id])

        ok, message = social_core.accept_friend_request(user_id, request_id, other_user_id)
        if not ok:
            return _emit_error(message)
        _refresh_friends_state(socketio, target_ids)

    @socketio.on("friends_request_decline", namespace=SOCIAL_NAMESPACE)
    def handle_friends_request_decline(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")

        request_id = _to_int((data or {}).get("request_id"))
        other_user_id = _to_int((data or {}).get("user_id"))
        target_ids = [user_id]
        if other_user_id:
            target_ids.append(other_user_id)

        ok, message = social_core.decline_friend_request(user_id, request_id, other_user_id)
        if not ok:
            return _emit_error(message)
        _refresh_friends_state(socketio, target_ids)

    @socketio.on("friends_remove", namespace=SOCIAL_NAMESPACE)
    def handle_friends_remove(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")
        friend_user_id = _to_int((data or {}).get("friend_user_id"))
        if not friend_user_id:
            return _emit_error("friend_user_id is required")
        ok, message = social_core.remove_friend(user_id, friend_user_id)
        if not ok:
            return _emit_error(message)
        _refresh_friends_state(socketio, [user_id, friend_user_id])

    @socketio.on("friends_block", namespace=SOCIAL_NAMESPACE)
    def handle_friends_block(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")
        target_user_id = _to_int((data or {}).get("user_id"))
        if not target_user_id:
            return _emit_error("user_id is required")
        ok, message = social_core.block_user(user_id, target_user_id)
        if not ok:
            return _emit_error(message)
        _refresh_friends_state(socketio, [user_id, target_user_id])

    # ---------------------------- Party events ----------------------------

    @socketio.on("party_create", namespace=SOCIAL_NAMESPACE)
    def handle_party_create():
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")

        party = social_core.create_party(user_id)
        join_room(_party_room(party.id), sid=request.sid, namespace=SOCIAL_NAMESPACE)
        social_core.sync_party_conversation_members(party.id)
        conversation = Conversation.query.filter_by(type="party", party_id=party.id).first()
        if conversation:
            join_room(_conv_room(conversation.id), sid=request.sid, namespace=SOCIAL_NAMESPACE)

        _emit_party_state_for_members(socketio, party.id)
        _emit_chat_list(socketio, user_id)

    @socketio.on("party_invite", namespace=SOCIAL_NAMESPACE)
    def handle_party_invite(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")
        payload = data or {}
        party_id = _to_int(payload.get("party_id"))
        invitee_user_id = _to_int(payload.get("invitee_user_id"))
        if not party_id or not invitee_user_id:
            return _emit_error("party_id and invitee_user_id are required")

        ok, message, invite = social_core.invite_to_party(party_id, user_id, invitee_user_id)
        if not ok:
            return _emit_error(message)

        party = Party.query.get(party_id)
        _emit_party_state_for_members(socketio, party_id, [invitee_user_id])
        if invite and party:
            socketio.emit(
                "party_invite_received",
                {
                    "invite": {
                        "id": invite.id,
                        "party_id": invite.party_id,
                        "inviter_id": invite.inviter_id,
                        "invitee_id": invite.invitee_id,
                        "status": invite.status,
                        "created_at": social_core.utcnow_iso(invite.created_at),
                        "expires_at": social_core.utcnow_iso(invite.expires_at),
                    },
                    "party_summary": social_core.serialize_party(party, invitee_user_id),
                },
                room=_user_room(invitee_user_id),
                namespace=SOCIAL_NAMESPACE,
            )

    @socketio.on("party_invite_accept", namespace=SOCIAL_NAMESPACE)
    def handle_party_invite_accept(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")
        invite_id = _to_int((data or {}).get("invite_id"))
        if not invite_id:
            return _emit_error("invite_id is required")
        invite = PartyInvite.query.get(invite_id)
        inviter_id = invite.inviter_id if invite else None
        ok, message, party_id = social_core.respond_party_invite(invite_id, user_id, accept=True)
        if not ok:
            return _emit_error(message)
        if party_id:
            join_room(_party_room(party_id), sid=request.sid, namespace=SOCIAL_NAMESPACE)
            social_core.sync_party_conversation_members(party_id)
            conversation = Conversation.query.filter_by(type="party", party_id=party_id).first()
            if conversation:
                join_room(_conv_room(conversation.id), sid=request.sid, namespace=SOCIAL_NAMESPACE)
                _refresh_unread_for_conversation(socketio, conversation.id)
            _emit_party_state_for_members(socketio, party_id, [user_id, inviter_id] if inviter_id else [user_id])
            member_ids = [row.user_id for row in PartyMember.query.filter_by(party_id=party_id).all()]
            _emit_chat_list_for_users(socketio, member_ids + [user_id])
        else:
            _emit_party_state_for_user(socketio, user_id)

    @socketio.on("party_invite_decline", namespace=SOCIAL_NAMESPACE)
    def handle_party_invite_decline(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")
        invite_id = _to_int((data or {}).get("invite_id"))
        if not invite_id:
            return _emit_error("invite_id is required")
        invite = PartyInvite.query.get(invite_id)
        inviter_id = invite.inviter_id if invite else None
        party_id = invite.party_id if invite else None

        ok, message, _ = social_core.respond_party_invite(invite_id, user_id, accept=False)
        if not ok:
            return _emit_error(message)
        _emit_party_state_for_user(socketio, user_id)
        if party_id:
            _emit_party_state_for_members(socketio, party_id, [inviter_id] if inviter_id else None)

    @socketio.on("party_leave", namespace=SOCIAL_NAMESPACE)
    def handle_party_leave(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")
        party_id = _to_int((data or {}).get("party_id"))
        if not party_id:
            party = social_core.get_party_for_user(user_id)
            party_id = party.id if party else None
        if not party_id:
            return _emit_error("party_id is required")

        member_ids_before = [row.user_id for row in PartyMember.query.filter_by(party_id=party_id).all()]
        ok, message = social_core.leave_party(user_id, party_id)
        if not ok:
            return _emit_error(message)

        _leave_user_from_party_context(user_id, party_id)
        _emit_party_state_for_members(socketio, party_id, member_ids_before + [user_id])
        _emit_chat_list_for_users(socketio, member_ids_before + [user_id])

    @socketio.on("party_kick", namespace=SOCIAL_NAMESPACE)
    def handle_party_kick(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")
        payload = data or {}
        party_id = _to_int(payload.get("party_id"))
        member_user_id = _to_int(payload.get("member_user_id"))
        if not party_id or not member_user_id:
            return _emit_error("party_id and member_user_id are required")

        member_ids_before = [row.user_id for row in PartyMember.query.filter_by(party_id=party_id).all()]
        ok, message = social_core.kick_party_member(user_id, party_id, member_user_id)
        if not ok:
            return _emit_error(message)
        _leave_user_from_party_context(member_user_id, party_id)

        socketio.emit(
            "party_state",
            {
                "party": None,
                "incoming_invites": social_core.get_pending_party_invites_for_user(member_user_id),
            },
            room=_user_room(member_user_id),
            namespace=SOCIAL_NAMESPACE,
        )
        _emit_party_state_for_members(socketio, party_id, member_ids_before + [member_user_id])
        _emit_chat_list_for_users(socketio, member_ids_before + [member_user_id])

    @socketio.on("party_transfer_leader", namespace=SOCIAL_NAMESPACE)
    def handle_party_transfer_leader(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")
        payload = data or {}
        party_id = _to_int(payload.get("party_id"))
        new_leader_id = _to_int(payload.get("new_leader_id"))
        if not party_id or not new_leader_id:
            return _emit_error("party_id and new_leader_id are required")
        ok, message = social_core.transfer_party_leader(user_id, party_id, new_leader_id)
        if not ok:
            return _emit_error(message)
        _emit_party_state_for_members(socketio, party_id)

    # ---------------------------- Chat events -----------------------------

    @socketio.on("chat_list", namespace=SOCIAL_NAMESPACE)
    def handle_chat_list():
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")
        emit("chat_list", {"conversations": social_core.get_chat_list(user_id)})

    @socketio.on("chat_open", namespace=SOCIAL_NAMESPACE)
    def handle_chat_open(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")
        payload = data or {}
        conversation_id = _to_int(payload.get("conversation_id"))
        limit = _to_int(payload.get("limit")) or 40
        if not conversation_id:
            return _emit_error("conversation_id is required")
        if not social_core.is_conversation_member(user_id, conversation_id):
            return _emit_error("Conversation not found")
        conversation = Conversation.query.get(conversation_id)
        if not conversation:
            return _emit_error("Conversation not found")
        if conversation.type == "dm":
            members = social_core.conversation_member_ids(conversation_id)
            if len(members) >= 2 and not social_core.are_friends(members[0], members[1]):
                return _emit_error("DM permission denied")
        if conversation.type == "party":
            current_party = social_core.get_party_for_user(user_id)
            if not current_party or current_party.id != conversation.party_id:
                return _emit_error("Party chat permission denied")

        join_room(_conv_room(conversation_id), sid=request.sid, namespace=SOCIAL_NAMESPACE)
        emit(
            "chat_open",
            {
                "conversation": social_core.serialize_conversation(user_id, conversation),
                "messages": social_core.get_messages(conversation_id, limit=limit),
            },
        )

    @socketio.on("chat_open_dm", namespace=SOCIAL_NAMESPACE)
    def handle_chat_open_dm(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")
        friend_user_id = _to_int((data or {}).get("friend_user_id"))
        if not friend_user_id:
            return _emit_error("friend_user_id is required")
        if not social_core.are_friends(user_id, friend_user_id):
            return _emit_error("DMs are only available between friends")

        conversation = social_core.get_or_create_dm_conversation(user_id, friend_user_id)
        join_room(_conv_room(conversation.id), sid=request.sid, namespace=SOCIAL_NAMESPACE)
        emit(
            "chat_open",
            {
                "conversation": social_core.serialize_conversation(user_id, conversation),
                "messages": social_core.get_messages(conversation.id, limit=40),
            },
        )
        _emit_chat_list_for_users(socketio, [user_id, friend_user_id])

    @socketio.on("chat_send", namespace=SOCIAL_NAMESPACE)
    def handle_chat_send(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")
        payload = data or {}
        conversation_id = _to_int(payload.get("conversation_id"))
        if not conversation_id:
            return _emit_error("conversation_id is required")
        if social_core.chat_send_rate_limited(user_id):
            return _emit_error("You're sending messages too quickly")

        conversation = Conversation.query.get(conversation_id)
        if not conversation:
            return _emit_error("Conversation not found")
        if not social_core.is_conversation_member(user_id, conversation_id):
            return _emit_error("Conversation not found")
        if conversation.type == "dm":
            members = social_core.conversation_member_ids(conversation_id)
            if len(members) >= 2 and not social_core.are_friends(members[0], members[1]):
                return _emit_error("DM permission denied")
        if conversation.type == "party":
            current_party = social_core.get_party_for_user(user_id)
            if not current_party or conversation.party_id != current_party.id:
                return _emit_error("Party chat permission denied")

        msg_type = payload.get("type") or "text"
        body_text = payload.get("body_text")
        if not body_text:
            body_text = payload.get("emoji")
        image_url = payload.get("image_url")

        ok, message, row = social_core.send_message(
            user_id=user_id,
            conversation_id=conversation_id,
            msg_type=msg_type,
            body_text=body_text,
            image_url=image_url,
        )
        if not ok:
            return _emit_error(message)

        payload_msg = social_core.serialize_message(row)
        socketio.emit(
            "chat_message",
            {"message": payload_msg},
            room=_conv_room(conversation_id),
            namespace=SOCIAL_NAMESPACE,
        )

        _refresh_unread_for_conversation(socketio, conversation_id)
        _emit_chat_list_for_users(socketio, social_core.conversation_member_ids(conversation_id))

    @socketio.on("chat_typing", namespace=SOCIAL_NAMESPACE)
    def handle_chat_typing(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")
        payload = data or {}
        conversation_id = _to_int(payload.get("conversation_id"))
        is_typing = bool(payload.get("is_typing"))
        if not conversation_id or not social_core.is_conversation_member(user_id, conversation_id):
            return
        emit(
            "chat_typing",
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "is_typing": is_typing,
            },
            room=_conv_room(conversation_id),
            include_self=False,
        )

    @socketio.on("chat_read", namespace=SOCIAL_NAMESPACE)
    def handle_chat_read(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")
        payload = data or {}
        conversation_id = _to_int(payload.get("conversation_id"))
        last_read_message_id = _to_int(payload.get("last_read_message_id"))
        if not conversation_id:
            return _emit_error("conversation_id is required")
        if not social_core.is_conversation_member(user_id, conversation_id):
            return _emit_error("Conversation not found")
        unread = social_core.mark_conversation_read(user_id, conversation_id, last_read_message_id)
        emit("chat_unread", {"conversation_id": conversation_id, "unread_count": unread})
        _emit_chat_list(socketio, user_id)

    @socketio.on("chat_history_before", namespace=SOCIAL_NAMESPACE)
    def handle_chat_history_before(data):
        user_id = _sid_to_user.get(request.sid)
        if not user_id:
            return _emit_error("Unauthorized")
        payload = data or {}
        conversation_id = _to_int(payload.get("conversation_id"))
        before_message_id = _to_int(payload.get("before_message_id"))
        limit = _to_int(payload.get("limit")) or 30
        if not conversation_id:
            return _emit_error("conversation_id is required")
        if not social_core.is_conversation_member(user_id, conversation_id):
            return _emit_error("Conversation not found")
        conversation = Conversation.query.get(conversation_id)
        if conversation and conversation.type == "dm":
            members = social_core.conversation_member_ids(conversation_id)
            if len(members) >= 2 and not social_core.are_friends(members[0], members[1]):
                return _emit_error("DM permission denied")
        if conversation and conversation.type == "party":
            current_party = social_core.get_party_for_user(user_id)
            if not current_party or current_party.id != conversation.party_id:
                return _emit_error("Party chat permission denied")
        emit(
            "chat_history_before",
            {
                "conversation_id": conversation_id,
                "messages": social_core.get_messages(
                    conversation_id,
                    limit=limit,
                    before_message_id=before_message_id,
                ),
            },
        )
