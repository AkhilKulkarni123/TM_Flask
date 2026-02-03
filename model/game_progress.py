from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from __init__ import db

class GameProgress(db.Model):
    """
    Model for tracking player's game progress in Snakes and Ladders
    """
    __tablename__ = 'game_progress'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    current_position = Column(Integer, default=1)
    completed_squares = Column(JSON, default=list)  # List of completed square numbers
    bullets = Column(Integer, default=0)  # Currency collected
    lives = Column(Integer, default=5)
    time_played_minutes = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="game_progress")
    square_completions = relationship("SquareCompletion", back_populates="game_progress", cascade="all, delete-orphan")
    
    def __init__(self, user_id):
        self.user_id = user_id
        self.current_position = 1
        self.completed_squares = []
        self.bullets = 0
        self.lives = 5
        self.time_played_minutes = 0
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'current_position': self.current_position,
            'completed_squares': self.completed_squares or [],
            'bullets': self.bullets,
            'lives': self.lives,
            'time_played_minutes': self.time_played_minutes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    def complete_square(self, square_number, bullets_earned, time_spent):
        """Mark a square as completed and update stats"""
        if not self.completed_squares:
            self.completed_squares = []
        
        if square_number not in self.completed_squares:
            self.completed_squares.append(square_number)
            self.bullets += bullets_earned
            self.time_played_minutes += time_spent // 60
            self.updated_at = datetime.utcnow()
            return True
        return False
    
    def move_to_position(self, position):
        """Update player's current position"""
        self.current_position = max(1, min(25, position))
        self.updated_at = datetime.utcnow()
    
    def add_bullets(self, amount):
        """Add bullets (currency)"""
        self.bullets += amount
        self.updated_at = datetime.utcnow()
    
    def spend_bullets(self, amount):
        """Spend bullets if player has enough"""
        if self.bullets >= amount:
            self.bullets -= amount
            self.updated_at = datetime.utcnow()
            return True
        return False
    
    def lose_life(self):
        """Decrease lives by 1"""
        if self.lives > 0:
            self.lives -= 1
            self.updated_at = datetime.utcnow()
            return True
        return False
    
    def gain_life(self):
        """Increase lives by 1"""
        self.lives += 1
        self.updated_at = datetime.utcnow()
    
    def reset_progress(self):
        """Reset player's game progress"""
        self.current_position = 1
        self.completed_squares = []
        self.bullets = 0
        self.lives = 5
        self.updated_at = datetime.utcnow()


class SquareCompletion(db.Model):
    """
    Model for detailed tracking of each square completion
    """
    __tablename__ = 'square_completions'
    
    id = Column(Integer, primary_key=True)
    game_progress_id = Column(Integer, ForeignKey('game_progress.id'), nullable=False)
    square_number = Column(Integer, nullable=False)
    bullets_earned = Column(Integer, default=0)
    time_spent_seconds = Column(Integer, default=0)
    completed_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    game_progress = relationship("GameProgress", back_populates="square_completions")
    
    def __init__(self, game_progress_id, square_number, bullets_earned=0, time_spent_seconds=0):
        self.game_progress_id = game_progress_id
        self.square_number = square_number
        self.bullets_earned = bullets_earned
        self.time_spent_seconds = time_spent_seconds
    
    def to_dict(self):
        return {
            'id': self.id,
            'game_progress_id': self.game_progress_id,
            'square_number': self.square_number,
            'bullets_earned': self.bullets_earned,
            'time_spent_seconds': self.time_spent_seconds,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None
        }
