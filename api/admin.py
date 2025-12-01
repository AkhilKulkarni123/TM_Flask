from flask import Blueprint, request, jsonify, g, render_template
from flask_cors import CORS
from model.game_progress import GameProgress, SquareCompletion
from model.boss_room import BossRoom, BossPlayer, BossBattleStats
from model.user import User
from __init__ import db
from api.jwt_authorize import token_required
import logging
from functools import wraps

admin_api = Blueprint('admin_api', __name__, url_prefix='/api/admin')
CORS(admin_api, supports_credentials=True, origins=[
    'http://localhost:4500',
    'http://127.0.0.1:4500',
    'https://akhilkulkarni123.github.io'
])
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def admin_required():
    """Decorator to require admin privileges"""
    def decorator(f):
        @wraps(f)
        @token_required()
        def decorated_function(*args, **kwargs):
            user = g.current_user
            
            # Check if user is admin (you can customize this check)
            if not user or user.role != 'Admin':
                return jsonify({'error': 'Admin access required'}), 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


@admin_api.route('/dashboard', methods=['GET'])
@admin_required()
def admin_dashboard():
    """Get admin dashboard overview"""
    try:
        # Total statistics
        total_users = User.query.count()
        total_players = GameProgress.query.count()
        
        # Game statistics
        total_squares_completed = db.session.query(
            db.func.sum(db.func.json_array_length(GameProgress.completed_squares))
        ).scalar() or 0
        
        total_bullets = db.session.query(
            db.func.sum(GameProgress.bullets)
        ).scalar() or 0
        
        total_time = db.session.query(
            db.func.sum(GameProgress.time_played_minutes)
        ).scalar() or 0
        
        # Boss battle statistics
        total_rooms = BossRoom.query.count()
        active_rooms = BossRoom.query.filter_by(is_active=True).count()
        completed_battles = BossRoom.query.filter_by(is_completed=True).count()
        
        # Player completion rates
        completion_rates = []
        for progress in GameProgress.query.all():
            if progress.user:
                rate = (len(progress.completed_squares) / 25 * 100) if progress.completed_squares else 0
                completion_rates.append({
                    'username': progress.user.name,
                    'rate': round(rate, 1)
                })
        
        completion_rates.sort(key=lambda x: x['rate'], reverse=True)
        
        dashboard_data = {
            'total_users': total_users,
            'active_players': total_players,
            'total_squares_completed': total_squares_completed,
            'total_bullets_collected': total_bullets,
            'total_hours_played': round(total_time / 60, 1),
            'boss_battles': {
                'total': total_rooms,
                'active': active_rooms,
                'completed': completed_battles,
                'win_rate': round((completed_battles / total_rooms * 100) if total_rooms > 0 else 0, 1)
            },
            'top_players': completion_rates[:10]
        }
        
        return jsonify(dashboard_data), 200
    
    except Exception as e:
        logger.error(f"Error getting admin dashboard: {str(e)}")
        return jsonify({'error': 'Failed to get dashboard data'}), 500


@admin_api.route('/players', methods=['GET'])
@admin_required()
def get_all_players():
    """Get detailed stats for all players"""
    try:
        players_data = []
        
        users = User.query.all()
        for user in users:
            progress = GameProgress.query.filter_by(user_id=user.id).first()
            boss_stats = BossBattleStats.query.filter_by(user_id=user.id).first()
            
            if not progress:
                continue
            
            player_info = {
                'id': user.id,
                'username': user.name,
                'uid': user.uid,
                'email': user.email if hasattr(user, 'email') else None,
                'game_progress': {
                    'position': progress.current_position,
                    'squares_completed': len(progress.completed_squares) if progress.completed_squares else 0,
                    'completion_rate': round((len(progress.completed_squares) / 25 * 100) if progress.completed_squares else 0, 1),
                    'bullets': progress.bullets,
                    'lives': progress.lives,
                    'time_played_minutes': progress.time_played_minutes,
                    'time_played_formatted': f"{progress.time_played_minutes // 60}h {progress.time_played_minutes % 60}m",
                    'created_at': progress.created_at.isoformat() if progress.created_at else None,
                    'last_updated': progress.updated_at.isoformat() if progress.updated_at else None
                }
            }
            
            if boss_stats:
                player_info['boss_stats'] = boss_stats.to_dict()
            
            players_data.append(player_info)
        
        # Sort by completion rate
        players_data.sort(key=lambda x: x['game_progress']['completion_rate'], reverse=True)
        
        return jsonify({
            'total': len(players_data),
            'players': players_data
        }), 200
    
    except Exception as e:
        logger.error(f"Error getting all players: {str(e)}")
        return jsonify({'error': 'Failed to get players data'}), 500


@admin_api.route('/player/<int:user_id>', methods=['GET'])
@admin_required()
def get_player_details(user_id):
    """Get detailed information about a specific player"""
    try:
        user = User.query.get(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        progress = GameProgress.query.filter_by(user_id=user_id).first()
        boss_stats = BossBattleStats.query.filter_by(user_id=user_id).first()
        
        # Get all square completions
        completions = []
        if progress:
            completions = SquareCompletion.query.filter_by(
                game_progress_id=progress.id
            ).order_by(SquareCompletion.completed_at.desc()).all()
        
        # Get boss battle history
        boss_battles = BossPlayer.query.filter_by(user_id=user_id).all()
        
        player_details = {
            'user': {
                'id': user.id,
                'username': user.name,
                'uid': user.uid,
                'created_at': user.created_at.isoformat() if hasattr(user, 'created_at') and user.created_at else None
            },
            'game_progress': progress.to_dict() if progress else None,
            'square_completions': [c.to_dict() for c in completions],
            'boss_stats': boss_stats.to_dict() if boss_stats else None,
            'boss_battle_history': [b.to_dict() for b in boss_battles]
        }
        
        return jsonify(player_details), 200
    
    except Exception as e:
        logger.error(f"Error getting player details: {str(e)}")
        return jsonify({'error': 'Failed to get player details'}), 500


@admin_api.route('/active-rooms', methods=['GET'])
@admin_required()
def get_active_rooms():
    """Get all active boss battle rooms"""
    try:
        rooms = BossRoom.query.filter_by(is_active=True).all()
        
        rooms_data = []
        for room in rooms:
            room_info = room.to_dict()
            room_info['players'] = room.get_player_stats()
            rooms_data.append(room_info)
        
        return jsonify({
            'total': len(rooms_data),
            'rooms': rooms_data
        }), 200
    
    except Exception as e:
        logger.error(f"Error getting active rooms: {str(e)}")
        return jsonify({'error': 'Failed to get active rooms'}), 500


@admin_api.route('/statistics', methods=['GET'])
@admin_required()
def get_detailed_statistics():
    """Get detailed game statistics"""
    try:
        # Square completion statistics
        square_stats = []
        for square_num in range(1, 26):
            completions = db.session.query(SquareCompletion).filter_by(
                square_number=square_num
            ).all()
            
            if completions:
                avg_time = sum(c.time_spent_seconds for c in completions) / len(completions)
                avg_bullets = sum(c.bullets_earned for c in completions) / len(completions)
                
                square_stats.append({
                    'square': square_num,
                    'completions': len(completions),
                    'avg_time_seconds': round(avg_time, 1),
                    'avg_bullets_earned': round(avg_bullets, 1)
                })
        
        # Player activity over time
        recent_players = GameProgress.query.order_by(
            GameProgress.updated_at.desc()
        ).limit(20).all()
        
        recent_activity = [{
            'username': p.user.name if p.user else 'Unknown',
            'position': p.current_position,
            'last_active': p.updated_at.isoformat() if p.updated_at else None
        } for p in recent_players]
        
        stats = {
            'square_statistics': square_stats,
            'recent_activity': recent_activity,
            'most_completed_square': max(square_stats, key=lambda x: x['completions'])['square'] if square_stats else None,
            'hardest_square': max(square_stats, key=lambda x: x['avg_time_seconds'])['square'] if square_stats else None
        }
        
        return jsonify(stats), 200
    
    except Exception as e:
        logger.error(f"Error getting statistics: {str(e)}")
        return jsonify({'error': 'Failed to get statistics'}), 500


@admin_api.route('/player/<int:user_id>/reset', methods=['POST'])
@admin_required()
def reset_player_progress(user_id):
    """Reset a player's game progress (admin only)"""
    try:
        progress = GameProgress.query.filter_by(user_id=user_id).first()
        if not progress:
            return jsonify({'error': 'Player progress not found'}), 404
        
        progress.reset_progress()
        
        # Delete all square completions
        SquareCompletion.query.filter_by(game_progress_id=progress.id).delete()
        
        db.session.commit()
        
        return jsonify({
            'message': 'Player progress reset successfully',
            'user_id': user_id
        }), 200
    
    except Exception as e:
        logger.error(f"Error resetting player progress: {str(e)}")
        db.session.rollback()
        return jsonify({'error': 'Failed to reset player progress'}), 500


@admin_api.route('/player/<int:user_id>/modify', methods=['POST'])
@admin_required()
def modify_player_stats(user_id):
    """Modify a player's stats (admin only)"""
    try:
        data = request.get_json()
        
        progress = GameProgress.query.filter_by(user_id=user_id).first()
        if not progress:
            return jsonify({'error': 'Player progress not found'}), 404
        
        # Update fields if provided
        if 'bullets' in data:
            progress.bullets = max(0, data['bullets'])
        
        if 'lives' in data:
            progress.lives = max(0, data['lives'])
        
        if 'position' in data:
            progress.move_to_position(data['position'])
        
        db.session.commit()
        
        return jsonify({
            'message': 'Player stats modified successfully',
            'progress': progress.to_dict()
        }), 200
    
    except Exception as e:
        logger.error(f"Error modifying player stats: {str(e)}")
        db.session.rollback()
        return jsonify({'error': 'Failed to modify player stats'}), 500


@admin_api.route('/export', methods=['GET'])
@admin_required()
def export_all_data():
    """Export all game data for analysis"""
    try:
        # Get all data
        players = GameProgress.query.all()
        completions = SquareCompletion.query.all()
        boss_stats = BossBattleStats.query.all()
        
        export_data = {
            'players': [p.to_dict() for p in players],
            'square_completions': [c.to_dict() for c in completions],
            'boss_statistics': [b.to_dict() for b in boss_stats],
            'exported_at': datetime.utcnow().isoformat()
        }
        
        return jsonify(export_data), 200
    
    except Exception as e:
        logger.error(f"Error exporting data: {str(e)}")
        return jsonify({'error': 'Failed to export data'}), 500