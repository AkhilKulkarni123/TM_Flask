"""
Model definitions for the Snakes and Ladders game.

This module defines the ``SnakesGameData`` SQLAlchemy model.  It stores
per‑user state for the Snakes game including the player's current
square, total bullets earned, completed lessons, unlocked board
sections and other metadata.  New fields ``completed_lessons`` and
``unlocked_sections`` have been added to support the split board and
lesson mechanics.
"""

from datetime import datetime
from sqlite3 import IntegrityError
from sqlalchemy import Column, Integer, String, JSON, Float, DateTime
from sqlalchemy.ext.mutable import MutableList
from __init__ import app, db


class SnakesGameData(db.Model):
    __tablename__ = 'snakes_game_data'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, unique=True)
    username = Column(String(255), nullable=False)
    total_bullets = Column(Integer, default=0)
    time_played = Column(Float, default=0.0)
    current_square = Column(Integer, default=1)
    boss_battle_attempts = Column(Integer, default=0)
    selected_character = Column(String(100), default='default')
    # List of visited squares for this player.  MutableList/JSON allows
    # in‑place modification of the list while persisting to JSON.
    visited_squares = Column(MutableList.as_mutable(JSON), default=lambda: [1])
    lives = Column(Integer, default=5)
    game_status = Column(String(50), default='active')
    completed_at = Column(DateTime, nullable=True)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # NEW FIELDS
    # completed_lessons stores integers identifying lesson rows (1–5) that
    # the user has finished.  unlocked_sections lists which board
    # segments ('half1', 'half2', 'boss') are available for the user.
    completed_lessons = Column(MutableList.as_mutable(JSON), default=list)
    unlocked_sections = Column(MutableList.as_mutable(JSON), default=lambda: ['half1'])

    def __init__(self, user_id, username, selected_character='default'):
        self.user_id = user_id
        self.username = username
        self.selected_character = selected_character
        # Start at square 1 and initialise mutable lists
        self.visited_squares = [1]
        self.completed_lessons = []
        self.unlocked_sections = ['half1']

    def create(self):
        """Insert this record into the database."""
        try:
            db.session.add(self)
            db.session.commit()
            return self
        except IntegrityError:
            db.session.rollback()
            return None

    def read(self):
        """Serialize this object to a dictionary for JSON responses."""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'username': self.username,
            'total_bullets': self.total_bullets,
            'time_played': self.time_played,
            'current_square': self.current_square,
            'boss_battle_attempts': self.boss_battle_attempts,
            'selected_character': self.selected_character,
            'visited_squares': self.visited_squares or [],
            'lives': self.lives,
            'game_status': self.game_status,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'completed_lessons': self.completed_lessons or [],
            'unlocked_sections': self.unlocked_sections or ['half1'],
            'last_updated': self.last_updated.isoformat() if self.last_updated else None
        }

    def update(self, data):
        """Update this record with a dictionary of values."""
        if 'total_bullets' in data:
            self.total_bullets = data['total_bullets']
        if 'time_played' in data:
            self.time_played = data['time_played']
        if 'current_square' in data:
            self.current_square = data['current_square']
        if 'boss_battle_attempts' in data:
            self.boss_battle_attempts = data['boss_battle_attempts']
        if 'selected_character' in data:
            self.selected_character = data['selected_character']
        if 'visited_squares' in data:
            self.visited_squares = data['visited_squares']
        if 'completed_lessons' in data:
            self.completed_lessons = data['completed_lessons']
        if 'unlocked_sections' in data:
            self.unlocked_sections = data['unlocked_sections']
        if 'lives' in data:
            self.lives = data['lives']
        if 'game_status' in data:
            self.game_status = data['game_status']
        db.session.commit()
        return self

    def delete(self):
        db.session.delete(self)
        db.session.commit()

    @staticmethod
    def get_leaderboard(limit=10):
        """Return a list of top players ordered by bullets."""
        return SnakesGameData.query.order_by(SnakesGameData.total_bullets.desc()).limit(limit).all()

    @staticmethod
    def reset_player_position(user_id):
        """Reset a player's position, lives and status (used for boss battle resets)."""
        player = SnakesGameData.query.filter_by(user_id=user_id).first()
        if player:
            player.current_square = 1
            player.lives = 5
            player.game_status = 'active'
            db.session.commit()
        return player

    @staticmethod
    def initSnakesGame():
        """Initialise the database tables for SnakesGameData."""
        with app.app_context():
            db.create_all()
