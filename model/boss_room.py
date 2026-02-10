from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Float, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from __init__ import db

class BossRoom(db.Model):
    """
    Model for managing boss battle rooms where multiple players fight together
    """
    __tablename__ = 'boss_rooms'
    
    id = Column(Integer, primary_key=True)
    room_id = Column(String(64), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    boss_health = Column(Integer, default=1000)
    max_boss_health = Column(Integer, default=1000)
    is_active = Column(Boolean, default=True)
    is_completed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    
    # Relationships
    players = relationship("BossPlayer", back_populates="room", cascade="all, delete-orphan")
    
    def __init__(self, max_boss_health=1000):
        self.room_id = str(uuid.uuid4())
        self.boss_health = max_boss_health
        self.max_boss_health = max_boss_health
        self.is_active = True
        self.is_completed = False
    
    def to_dict(self):
        return {
            'id': self.id,
            'room_id': self.room_id,
            'boss_health': self.boss_health,
            'max_boss_health': self.max_boss_health,
            'boss_health_percentage': (self.boss_health / self.max_boss_health * 100) if self.max_boss_health > 0 else 0,
            'is_active': self.is_active,
            'is_completed': self.is_completed,
            'player_count': len([p for p in self.players if p.is_alive]),
            'total_players': len(self.players),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None
        }
    
    def damage_boss(self, damage_amount):
        """Apply damage to the boss"""
        self.boss_health = max(0, self.boss_health - damage_amount)
        if self.boss_health <= 0:
            self.complete_battle()
        return self.boss_health
    
    def complete_battle(self):
        """Mark the battle as completed"""
        self.is_completed = True
        self.is_active = False
        self.completed_at = datetime.utcnow()
        
        # Reward all alive players
        for player in self.players:
            if player.is_alive:
                player.victory = True
    
    def get_alive_players(self):
        """Get list of all alive players"""
        return [p for p in self.players if p.is_alive]
    
    def get_player_stats(self):
        """Get stats for all players in the room"""
        return [p.to_dict() for p in self.players]


class BossPlayer(db.Model):
    """
    Model for tracking individual players in a boss battle
    """
    __tablename__ = 'boss_players'
    
    id = Column(Integer, primary_key=True)
    room_id = Column(Integer, ForeignKey('boss_rooms.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    lives = Column(Integer, default=5)
    bullets_used = Column(Integer, default=0)
    damage_dealt = Column(Integer, default=0)
    is_alive = Column(Boolean, default=True)
    victory = Column(Boolean, default=False)
    joined_at = Column(DateTime, default=datetime.utcnow)
    died_at = Column(DateTime, nullable=True)
    
    # Relationships
    room = relationship("BossRoom", back_populates="players")
    user = relationship("User", backref="boss_battles")
    
    def __init__(self, room_id, user_id, lives=5):
        self.room_id = room_id
        self.user_id = user_id
        self.lives = lives
        self.bullets_used = 0
        self.damage_dealt = 0
        self.is_alive = True
        self.victory = False
    
    def to_dict(self):
        return {
            'id': self.id,
            'room_id': self.room_id,
            'user_id': self.user_id,
            'username': self.user.name if self.user else 'Unknown',
            'lives': self.lives,
            'bullets_used': self.bullets_used,
            'damage_dealt': self.damage_dealt,
            'is_alive': self.is_alive,
            'victory': self.victory,
            'joined_at': self.joined_at.isoformat() if self.joined_at else None,
            'died_at': self.died_at.isoformat() if self.died_at else None
        }
    
    def take_damage(self):
        """Player takes damage from boss projectile"""
        if self.lives > 0:
            self.lives -= 1
            if self.lives <= 0:
                self.is_alive = False
                self.died_at = datetime.utcnow()
            return True
        return False
    
    def shoot_bullet(self, damage=10):
        """Player shoots a bullet at the boss"""
        self.bullets_used += 1
        self.damage_dealt += damage
        return damage
    
    def revive(self, lives=1):
        """Revive player with specified lives"""
        self.lives = lives
        self.is_alive = True
        self.died_at = None


class BossBattleStats(db.Model):
    """
    Model for storing aggregate boss battle statistics
    """
    __tablename__ = 'boss_battle_stats'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, unique=True)
    battles_joined = Column(Integer, default=0)
    battles_won = Column(Integer, default=0)
    total_damage_dealt = Column(Integer, default=0)
    total_bullets_used = Column(Integer, default=0)
    deaths = Column(Integer, default=0)
    best_damage = Column(Integer, default=0)
    
    # Relationships
    user = relationship("User", backref="boss_stats")
    
    def __init__(self, user_id):
        self.user_id = user_id
        self.battles_joined = 0
        self.battles_won = 0
        self.total_damage_dealt = 0
        self.total_bullets_used = 0
        self.deaths = 0
        self.best_damage = 0
    
    def to_dict(self):
        win_rate = (self.battles_won / self.battles_joined * 100) if self.battles_joined > 0 else 0
        avg_damage = (self.total_damage_dealt / self.battles_joined) if self.battles_joined > 0 else 0
        
        return {
            'id': self.id,
            'user_id': self.user_id,
            'battles_joined': self.battles_joined,
            'battles_won': self.battles_won,
            'win_rate': round(win_rate, 1),
            'total_damage_dealt': self.total_damage_dealt,
            'average_damage': round(avg_damage, 1),
            'total_bullets_used': self.total_bullets_used,
            'deaths': self.deaths,
            'best_damage': self.best_damage
        }
    
    def update_after_battle(self, player_data):
        """Update stats after a battle"""
        self.battles_joined += 1
        if player_data.get('victory'):
            self.battles_won += 1
        self.total_damage_dealt += player_data.get('damage_dealt', 0)
        self.total_bullets_used += player_data.get('bullets_used', 0)
        if not player_data.get('is_alive'):
            self.deaths += 1
        if player_data.get('damage_dealt', 0) > self.best_damage:
            self.best_damage = player_data.get('damage_dealt', 0)



class SlitherRushStats(db.Model):
    """Persisted per-user SLITHERRUSH profile statistics."""
    __tablename__ = 'slitherrush_stats'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, unique=True)
    username = Column(String(255), nullable=False)

    matches_played = Column(Integer, default=0)
    matches_won = Column(Integer, default=0)
    total_score = Column(Integer, default=0)
    total_kills = Column(Integer, default=0)
    total_orbs = Column(Integer, default=0)
    total_survival_seconds = Column(Integer, default=0)
    best_length = Column(Integer, default=0)
    best_score = Column(Integer, default=0)
    last_played_at = Column(Float, nullable=True)

    user = relationship("User", backref="slitherrush_stats")

    def __init__(self, user_id, username):
        self.user_id = user_id
        self.username = username

    def to_dict(self):
        win_rate = (self.matches_won / self.matches_played * 100.0) if self.matches_played else 0.0
        avg_score = (self.total_score / self.matches_played) if self.matches_played else 0.0
        avg_survival = (self.total_survival_seconds / self.matches_played) if self.matches_played else 0.0
        return {
            'user_id': self.user_id,
            'username': self.username,
            'matches_played': int(self.matches_played or 0),
            'matches_won': int(self.matches_won or 0),
            'win_rate': round(win_rate, 1),
            'total_score': int(self.total_score or 0),
            'average_score': round(avg_score, 1),
            'total_kills': int(self.total_kills or 0),
            'total_orbs': int(self.total_orbs or 0),
            'total_survival_seconds': int(self.total_survival_seconds or 0),
            'average_survival_seconds': round(avg_survival, 1),
            'best_length': int(self.best_length or 0),
            'best_score': int(self.best_score or 0),
        }

    @staticmethod
    def get_global_leaderboard(limit=50):
        rows = SlitherRushStats.query.order_by(
            SlitherRushStats.matches_won.desc(),
            SlitherRushStats.total_score.desc(),
            SlitherRushStats.best_length.desc()
        ).limit(limit).all()
        return [row.to_dict() for row in rows]
