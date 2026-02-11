from flask import Blueprint, request, jsonify, g, current_app
from flask_cors import CORS
from model.boss_room import BossRoom, BossPlayer, BossBattleStats
from model.game_progress import GameProgress
from model.user import User
from __init__ import db
from api.jwt_authorize import token_required
import jwt
import json
import logging

boss_api = Blueprint('boss_api', __name__, url_prefix='/api/boss')
CORS(boss_api, supports_credentials=True, origins=[
    'http://localhost:4500',
    'http://127.0.0.1:4500',
    'https://akhilkulkarni123.github.io'
])
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# WebSocket connections storage
active_connections = {}


def _get_user_from_token():
    token = request.cookies.get(current_app.config["JWT_TOKEN_NAME"])
    if not token:
        return None
    try:
        data = jwt.decode(token, current_app.config["SECRET_KEY"], algorithms=["HS256"])
        return User.query.filter_by(_uid=data.get("_uid")).first()
    except Exception:
        return None


def _get_or_create_guest_user(guest_id, guest_name):
    if not guest_id:
        return None
    user = User.query.filter_by(_uid=guest_id).first()
    if user:
        return user
    user = User(name=guest_name or guest_id, uid=guest_id)
    db.session.add(user)
    db.session.flush()
    return user


@boss_api.route('/join', methods=['POST'])
def join_boss_battle():
    """Join or create a boss battle room"""
    try:
        data = request.get_json() or {}
        dev_mode = data.get('dev_mode', False)
        guest_id = data.get('guest_id')
        guest_name = data.get('guest_name')
        preferred_room_id = data.get('room_id')

        user = _get_user_from_token()
        is_guest = False
        if not user:
            if guest_id:
                user = _get_or_create_guest_user(guest_id, guest_name)
                is_guest = True
            else:
                return jsonify({'error': 'Authentication required'}), 401

        # Get player progress (may be None for guests or new users)
        progress = GameProgress.query.filter_by(user_id=user.id).first()

        if not dev_mode and not is_guest:
            # Check if player has reached square 25
            if not progress or progress.current_position < 25:
                return jsonify({'error': 'You must reach square 25 first', 'current_position': progress.current_position if progress else 0}), 403

            # Check if player has enough bullets
            if progress.bullets < 10:
                return jsonify({'error': 'You need at least 10 bullets to fight the boss', 'bullets': progress.bullets if progress else 0}), 403
        
        # Find an active room with space (max 10 players) or create a new one
        MAX_PLAYERS_PER_ROOM = 10
        room = None
        if preferred_room_id:
            preferred = BossRoom.query.filter_by(
                room_id=str(preferred_room_id),
                is_active=True,
                is_completed=False,
            ).first()
            if preferred and len(preferred.players) < MAX_PLAYERS_PER_ROOM:
                room = preferred

        active_rooms = BossRoom.query.filter_by(is_active=True, is_completed=False).all()
        if room is None:
            for candidate in active_rooms:
                if len(candidate.players) < MAX_PLAYERS_PER_ROOM:
                    room = candidate
                    break

        if not room:
            # All rooms full or none exist - create new room
            room = BossRoom(max_boss_health=2000)
            db.session.add(room)
            db.session.flush()
        
        # Check if player already in this room
        existing_player = BossPlayer.query.filter_by(
            room_id=room.id,
            user_id=user.id
        ).first()
        
        if existing_player:
            return jsonify({
                'message': 'Already in this room',
                'room_id': room.room_id,
                'room': room.to_dict(),
                'player': existing_player.to_dict()
            }), 200
        
        # Add player to room (use progress lives or default to 5)
        player_lives = progress.lives if progress else 5
        player = BossPlayer(room_id=room.id, user_id=user.id, lives=player_lives)
        db.session.add(player)
        
        # Get or create boss stats
        boss_stats = BossBattleStats.query.filter_by(user_id=user.id).first()
        if not boss_stats:
            boss_stats = BossBattleStats(user_id=user.id)
            db.session.add(boss_stats)
        
        db.session.commit()
        
        return jsonify({
            'message': 'Joined boss battle',
            'room_id': room.room_id,
            'room': room.to_dict(),
            'player': player.to_dict(),
            'players': room.get_player_stats()
        }), 200
    
    except Exception as e:
        logger.error(f"Error joining boss battle: {str(e)}")
        db.session.rollback()
        return jsonify({'error': 'Failed to join boss battle'}), 500


@boss_api.route('/room/<room_id>', methods=['GET'])
@token_required()
def get_room_status(room_id):
    """Get current status of a boss room"""
    try:
        room = BossRoom.query.filter_by(room_id=room_id).first()
        if not room:
            return jsonify({'error': 'Room not found'}), 404
        
        return jsonify({
            'room': room.to_dict(),
            'players': room.get_player_stats()
        }), 200
    
    except Exception as e:
        logger.error(f"Error getting room status: {str(e)}")
        return jsonify({'error': 'Failed to get room status'}), 500
    

@boss_api.route('/reset-rooms', methods=['POST'])
def reset_rooms():
    """Clear all boss rooms"""
    try:
        BossRoom.query.delete()
        db.session.commit()
        return jsonify({'message': 'All rooms cleared'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@boss_api.route('/damage', methods=['POST'])
@token_required()
def deal_damage():
    """Player deals damage to boss"""
    try:
        user = g.current_user
        data = request.get_json()
        
        room_id = data.get('room_id')
        damage = data.get('damage', 10)
        
        if not room_id:
            return jsonify({'error': 'Room ID required'}), 400
        
        room = BossRoom.query.filter_by(room_id=room_id).first()
        if not room or not room.is_active:
            return jsonify({'error': 'Room not active'}), 404
        
        player = BossPlayer.query.filter_by(
            room_id=room.id,
            user_id=user.id
        ).first()
        
        if not player or not player.is_alive:
            return jsonify({'error': 'Player not in room or dead'}), 403
        
        # Check if player has bullets
        progress = GameProgress.query.filter_by(user_id=user.id).first()
        if not progress or progress.bullets <= 0:
            return jsonify({'error': 'No bullets left'}), 403
        
        # Deal damage
        player.shoot_bullet(damage)
        progress.spend_bullets(1)
        new_health = room.damage_boss(damage)
        
        db.session.commit()
        
        return jsonify({
            'message': 'Damage dealt',
            'boss_health': new_health,
            'bullets_remaining': progress.bullets,
            'is_defeated': room.is_completed
        }), 200
    
    except Exception as e:
        logger.error(f"Error dealing damage: {str(e)}")
        db.session.rollback()
        return jsonify({'error': 'Failed to deal damage'}), 500


@boss_api.route('/hit', methods=['POST'])
@token_required()
def player_hit():
    """Player gets hit by boss projectile"""
    try:
        user = g.current_user
        data = request.get_json()
        
        room_id = data.get('room_id')
        
        if not room_id:
            return jsonify({'error': 'Room ID required'}), 400
        
        room = BossRoom.query.filter_by(room_id=room_id).first()
        if not room or not room.is_active:
            return jsonify({'error': 'Room not active'}), 404
        
        player = BossPlayer.query.filter_by(
            room_id=room.id,
            user_id=user.id
        ).first()
        
        if not player or not player.is_alive:
            return jsonify({'error': 'Player not in room or already dead'}), 403
        
        # Take damage
        player.take_damage()
        
        # Update game progress
        progress = GameProgress.query.filter_by(user_id=user.id).first()
        if progress:
            progress.lose_life()
        
        db.session.commit()
        
        return jsonify({
            'message': 'Took damage',
            'lives': player.lives,
            'is_alive': player.is_alive
        }), 200
    
    except Exception as e:
        logger.error(f"Error processing hit: {str(e)}")
        db.session.rollback()
        return jsonify({'error': 'Failed to process hit'}), 500


@boss_api.route('/leave', methods=['POST'])
def leave_battle():
    """Leave the boss battle"""
    try:
        data = request.get_json() or {}
        guest_id = data.get('guest_id')

        user = _get_user_from_token()
        if not user and guest_id:
            user = User.query.filter_by(_uid=guest_id).first()
        if not user:
            return jsonify({'error': 'Authentication required'}), 401
        
        room_id = data.get('room_id')
        
        if not room_id:
            return jsonify({'error': 'Room ID required'}), 400
        
        room = BossRoom.query.filter_by(room_id=room_id).first()
        if not room:
            return jsonify({'error': 'Room not found'}), 404
        
        player = BossPlayer.query.filter_by(
            room_id=room.id,
            user_id=user.id
        ).first()
        
        if player:
            boss_stats = BossBattleStats.query.filter_by(user_id=user.id).first()
            if boss_stats:
                boss_stats.update_after_battle({
                    'victory': player.victory,
                    'damage_dealt': player.damage_dealt,
                    'bullets_used': player.bullets_used,
                    'is_alive': player.is_alive
                })
            
            db.session.delete(player)
            
            remaining_players = BossPlayer.query.filter_by(room_id=room.id).count()
            if remaining_players == 1:
                room.is_active = False
            
            db.session.commit()
        
        return jsonify({'message': 'Left battle'}), 200
    
    except Exception as e:
        logger.error(f"Error leaving battle: {str(e)}")
        db.session.rollback()
        return jsonify({'error': 'Failed to leave battle'})
