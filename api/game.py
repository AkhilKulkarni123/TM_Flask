from flask import Blueprint, request, jsonify, g
from flask_cors import CORS
from model.game_progress import GameProgress, SquareCompletion
from model.user import User
from __init__ import db
from api.jwt_authorize import token_required
import logging

game_api = Blueprint('game_api', __name__, url_prefix='/api/game')
CORS(game_api, supports_credentials=True, origins=[
    'http://localhost:4500',
    'http://127.0.0.1:4500',
    'https://akhilkulkarni123.github.io'  # Your GitHub Pages domain
])
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@game_api.route('/progress', methods=['GET'])
@token_required()
def get_progress():
    """Get current user's game progress"""
    try:
        user = g.current_user
        
        # Get or create game progress
        progress = GameProgress.query.filter_by(user_id=user.id).first()
        if not progress:
            progress = GameProgress(user_id=user.id)
            db.session.add(progress)
            db.session.commit()
        
        return jsonify(progress.to_dict()), 200
    
    except Exception as e:
        logger.error(f"Error getting progress: {str(e)}")
        return jsonify({'error': 'Failed to get progress'}), 500


@game_api.route('/progress', methods=['POST'])
@token_required()
def update_progress():
    """Update user's game progress"""
    try:
        user = g.current_user
        data = request.get_json()
        
        progress = GameProgress.query.filter_by(user_id=user.id).first()
        if not progress:
            progress = GameProgress(user_id=user.id)
            db.session.add(progress)
        
        # Update fields if provided
        if 'current_position' in data:
            progress.move_to_position(data['current_position'])
        
        if 'bullets' in data:
            progress.bullets = max(0, data['bullets'])
        
        if 'lives' in data:
            progress.lives = max(0, data['lives'])
        
        if 'time_played_minutes' in data:
            progress.time_played_minutes = data['time_played_minutes']
        
        if 'completed_squares' in data:
            progress.completed_squares = data['completed_squares']
        
        db.session.commit()
        
        return jsonify({
            'message': 'Progress updated',
            'progress': progress.to_dict()
        }), 200
    
    except Exception as e:
        logger.error(f"Error updating progress: {str(e)}")
        db.session.rollback()
        return jsonify({'error': 'Failed to update progress'}), 500


@game_api.route('/complete-square', methods=['POST'])
@token_required()
def complete_square():
    """Mark a square as completed"""
    try:
        user = g.current_user
        data = request.get_json()
        
        square_number = data.get('square')
        bullets_earned = data.get('bullets', 0)
        time_spent = data.get('timeSpent', 0)
        
        if not square_number or square_number < 1 or square_number > 25:
            return jsonify({'error': 'Invalid square number'}), 400
        
        progress = GameProgress.query.filter_by(user_id=user.id).first()
        if not progress:
            progress = GameProgress(user_id=user.id)
            db.session.add(progress)
            db.session.flush()
        
        # Complete the square
        was_new = progress.complete_square(square_number, bullets_earned, time_spent)
        
        # Record the completion
        if was_new:
            completion = SquareCompletion(
                game_progress_id=progress.id,
                square_number=square_number,
                bullets_earned=bullets_earned,
                time_spent_seconds=time_spent
            )
            db.session.add(completion)
        
        db.session.commit()
        
        return jsonify({
            'message': 'Square completed' if was_new else 'Square already completed',
            'progress': progress.to_dict(),
            'was_new': was_new
        }), 200
    
    except Exception as e:
        logger.error(f"Error completing square: {str(e)}")
        db.session.rollback()
        return jsonify({'error': 'Failed to complete square'}), 500


@game_api.route('/move', methods=['POST'])
@token_required()
def move_player():
    """Move player to new position"""
    try:
        user = g.current_user
        data = request.get_json()
        
        new_position = data.get('position')
        if not new_position or new_position < 1 or new_position > 25:
            return jsonify({'error': 'Invalid position'}), 400
        
        progress = GameProgress.query.filter_by(user_id=user.id).first()
        if not progress:
            progress = GameProgress(user_id=user.id)
            db.session.add(progress)
        
        progress.move_to_position(new_position)
        db.session.commit()
        
        return jsonify({
            'message': 'Position updated',
            'progress': progress.to_dict()
        }), 200
    
    except Exception as e:
        logger.error(f"Error moving player: {str(e)}")
        db.session.rollback()
        return jsonify({'error': 'Failed to move player'}), 500


@game_api.route('/bullets', methods=['POST'])
@token_required()
def modify_bullets():
    """Add or spend bullets"""
    try:
        user = g.current_user
        data = request.get_json()
        
        amount = data.get('amount', 0)
        action = data.get('action', 'add')  # 'add' or 'spend'
        
        progress = GameProgress.query.filter_by(user_id=user.id).first()
        if not progress:
            return jsonify({'error': 'Game progress not found'}), 404
        
        if action == 'add':
            progress.add_bullets(amount)
            message = f'Added {amount} bullets'
        elif action == 'spend':
            success = progress.spend_bullets(amount)
            if not success:
                return jsonify({'error': 'Not enough bullets'}), 400
            message = f'Spent {amount} bullets'
        else:
            return jsonify({'error': 'Invalid action'}), 400
        
        db.session.commit()
        
        return jsonify({
            'message': message,
            'bullets': progress.bullets
        }), 200
    
    except Exception as e:
        logger.error(f"Error modifying bullets: {str(e)}")
        db.session.rollback()
        return jsonify({'error': 'Failed to modify bullets'}), 500


@game_api.route('/lives', methods=['POST'])
@token_required()
def modify_lives():
    """Add or lose lives"""
    try:
        user = g.current_user
        data = request.get_json()
        
        action = data.get('action', 'lose')  # 'gain' or 'lose'
        
        progress = GameProgress.query.filter_by(user_id=user.id).first()
        if not progress:
            return jsonify({'error': 'Game progress not found'}), 404
        
        if action == 'gain':
            progress.gain_life()
            message = 'Gained 1 life'
        elif action == 'lose':
            success = progress.lose_life()
            if not success:
                return jsonify({'error': 'Already at 0 lives'}), 400
            message = 'Lost 1 life'
        else:
            return jsonify({'error': 'Invalid action'}), 400
        
        db.session.commit()
        
        return jsonify({
            'message': message,
            'lives': progress.lives
        }), 200
    
    except Exception as e:
        logger.error(f"Error modifying lives: {str(e)}")
        db.session.rollback()
        return jsonify({'error': 'Failed to modify lives'}), 500


@game_api.route('/stats', methods=['GET'])
@token_required()
def get_stats():
    """Get detailed player statistics"""
    try:
        user = g.current_user
        
        progress = GameProgress.query.filter_by(user_id=user.id).first()
        if not progress:
            return jsonify({'error': 'Game progress not found'}), 404
        
        completions = SquareCompletion.query.filter_by(
            game_progress_id=progress.id
        ).all()
        
        total_bullets_earned = sum(c.bullets_earned for c in completions)
        total_time_spent = sum(c.time_spent_seconds for c in completions)
        
        stats = {
            'progress': progress.to_dict(),
            'total_squares_completed': len(progress.completed_squares) if progress.completed_squares else 0,
            'completion_percentage': (len(progress.completed_squares) / 25 * 100) if progress.completed_squares else 0,
            'total_bullets_earned': total_bullets_earned,
            'current_bullets': progress.bullets,
            'bullets_spent': total_bullets_earned - progress.bullets,
            'total_time_seconds': total_time_spent,
            'total_time_formatted': f"{total_time_spent // 3600}h {(total_time_spent % 3600) // 60}m",
            'completions': [c.to_dict() for c in completions]
        }
        
        return jsonify(stats), 200
    
    except Exception as e:
        logger.error(f"Error getting stats: {str(e)}")
        return jsonify({'error': 'Failed to get stats'}), 500


@game_api.route('/reset', methods=['POST'])
@token_required()
def reset_progress():
    """Reset player's game progress"""
    try:
        user = g.current_user
        
        progress = GameProgress.query.filter_by(user_id=user.id).first()
        if not progress:
            return jsonify({'error': 'Game progress not found'}), 404
        
        progress.reset_progress()
        
        # Delete all square completions
        SquareCompletion.query.filter_by(game_progress_id=progress.id).delete()
        
        db.session.commit()
        
        return jsonify({
            'message': 'Progress reset successfully',
            'progress': progress.to_dict()
        }), 200
    
    except Exception as e:
        logger.error(f"Error resetting progress: {str(e)}")
        db.session.rollback()
        return jsonify({'error': 'Failed to reset progress'}), 500


@game_api.route('/leaderboard', methods=['GET'])
def get_leaderboard():
    """Get top players leaderboard"""
    try:
        # Get all players with their progress
        players = db.session.query(User, GameProgress).join(
            GameProgress, User.id == GameProgress.user_id
        ).all()
        
        leaderboard = []
        for user, progress in players:
            completion_rate = (len(progress.completed_squares) / 25 * 100) if progress.completed_squares else 0
            leaderboard.append({
                'username': user.name,
                'position': progress.current_position,
                'squares_completed': len(progress.completed_squares) if progress.completed_squares else 0,
                'completion_rate': round(completion_rate, 1),
                'bullets': progress.bullets,
                'time_played': progress.time_played_minutes
            })
        
        # Sort by squares completed, then bullets
        leaderboard.sort(key=lambda x: (x['squares_completed'], x['bullets']), reverse=True)
        
        # Add rank
        for i, entry in enumerate(leaderboard):
            entry['rank'] = i + 1
        
        return jsonify(leaderboard), 200
    
    except Exception as e:
        logger.error(f"Error getting leaderboard: {str(e)}")
        return jsonify({'error': 'Failed to get leaderboard'}), 500