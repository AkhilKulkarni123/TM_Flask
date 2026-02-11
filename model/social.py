"""Social system models for friends, parties, conversations, and messages."""

from datetime import datetime, timedelta

from sqlalchemy import CheckConstraint, UniqueConstraint

from __init__ import db


def utcnow() -> datetime:
    return datetime.utcnow()


def default_invite_expiry() -> datetime:
    return datetime.utcnow() + timedelta(hours=48)


def normalize_user_pair(user_a: int, user_b: int):
    low = int(min(user_a, user_b))
    high = int(max(user_a, user_b))
    return low, high


class Friendship(db.Model):
    __tablename__ = "friendships"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    other_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    pair_low_user_id = db.Column(db.Integer, nullable=False, index=True)
    pair_high_user_id = db.Column(db.Integer, nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="pending")
    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("pair_low_user_id", "pair_high_user_id", name="uq_friendships_pair"),
        CheckConstraint("user_id != other_user_id", name="ck_friendships_not_self"),
    )

    def sync_pair(self) -> None:
        low, high = normalize_user_pair(self.user_id, self.other_user_id)
        self.pair_low_user_id = low
        self.pair_high_user_id = high


class Party(db.Model):
    __tablename__ = "parties"

    id = db.Column(db.Integer, primary_key=True)
    leader_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    invite_code = db.Column(db.String(32), nullable=True, unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    members = db.relationship("PartyMember", back_populates="party", cascade="all, delete-orphan")
    invites = db.relationship("PartyInvite", back_populates="party", cascade="all, delete-orphan")


class PartyMember(db.Model):
    __tablename__ = "party_members"

    party_id = db.Column(db.Integer, db.ForeignKey("parties.id"), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    role = db.Column(db.String(20), nullable=False, default="member")
    joined_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    party = db.relationship("Party", back_populates="members")


class PartyInvite(db.Model):
    __tablename__ = "party_invites"

    id = db.Column(db.Integer, primary_key=True)
    party_id = db.Column(db.Integer, db.ForeignKey("parties.id"), nullable=False, index=True)
    inviter_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    invitee_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime, nullable=False, default=default_invite_expiry)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    party = db.relationship("Party", back_populates="invites")


class Conversation(db.Model):
    __tablename__ = "conversations"

    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(20), nullable=False, default="dm", index=True)
    party_id = db.Column(db.Integer, db.ForeignKey("parties.id"), nullable=True, unique=True, index=True)
    dm_low_user_id = db.Column(db.Integer, nullable=True, index=True)
    dm_high_user_id = db.Column(db.Integer, nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("dm_low_user_id", "dm_high_user_id", name="uq_conversations_dm_pair"),
    )

    members = db.relationship("ConversationMember", back_populates="conversation", cascade="all, delete-orphan")
    messages = db.relationship("Message", back_populates="conversation", cascade="all, delete-orphan")

    def sync_dm_pair(self, user_a: int, user_b: int) -> None:
        low, high = normalize_user_pair(user_a, user_b)
        self.dm_low_user_id = low
        self.dm_high_user_id = high


class ConversationMember(db.Model):
    __tablename__ = "conversation_members"

    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    joined_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_read_message_id = db.Column(db.Integer, db.ForeignKey("messages.id"), nullable=True)

    conversation = db.relationship("Conversation", back_populates="members")


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"), nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    type = db.Column(db.String(20), nullable=False, default="text")
    body_text = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.String(512), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    edited_at = db.Column(db.DateTime, nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)

    conversation = db.relationship("Conversation", back_populates="messages")


class PresenceRecord(db.Model):
    __tablename__ = "presence_records"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    status = db.Column(db.String(20), nullable=False, default="offline")
    activity = db.Column(db.String(120), nullable=True)
    last_seen = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
