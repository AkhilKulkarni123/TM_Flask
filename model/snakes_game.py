from sqlite3 import IntegrityError
from sqlalchemy import Column, Integer, String, JSON, Float, DateTime
from sqlalchemy.ext.mutable import MutableList
from __init__ import app, db
from datetime import datetime

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
    visited_squares = Column(JSON, default=list)
    lives = Column(Integer, default=3)
    game_status = Column(String(50), default='active')
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __init__(self, user_id, username, selected_character='default'):
        self.user_id = user_id
        self.username = username
        self.selected_character = selected_character
        self.visited_squares = [1]
        
    def create(self):
        try:
            db.session.add(self)
            db.session.commit()
            return self
        except IntegrityError:
            db.session.rollback()
            return None
            
    def read(self):
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
            'last_updated': self.last_updated.isoformat() if self.last_updated else None
        }
    
    def update(self, data):
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
        return SnakesGameData.query.order_by(
            SnakesGameData.total_bullets.desc()
        ).limit(limit).all()
    
    @staticmethod
    def reset_player_position(user_id):
        player = SnakesGameData.query.filter_by(user_id=user_id).first()
        if player:
            player.current_square = 1
            player.lives = 3
            player.game_status = 'active'
            db.session.commit()
            return player
        return None


def initSnakesGame():
    with app.app_context():
        db.create_all()