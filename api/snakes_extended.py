"""
API endpoints for the extended Snakes and Ladders game.

These endpoints expose progress information, lesson completion and
question answering functionality for the AP Computer Science
Principles-themed version of Snakes and Ladders.  They operate
alongside the existing game progress endpoints and require
authentication via JWT tokens.  If a user does not yet have a
SnakesGameData record it will be created automatically.
"""

from flask import Blueprint, request, jsonify, g
from __init__ import db
from api.jwt_authorize import token_required
from model.snakes_game import SnakesGameData

snakes_bp = Blueprint('snakes_bp', __name__, url_prefix='/api/snakes')


@snakes_bp.route('/progress', methods=['GET'])
@token_required()
def get_progress():
    """Return the current user's Snakes game progress."""
    try:
        user = g.current_user
        record = SnakesGameData.query.filter_by(user_id=user.id).first()
        if not record:
            # Create a new game record for this user
            record = SnakesGameData(user_id=user.id, username=user.name, selected_character='default')
            db.session.add(record)
            db.session.commit()
        return jsonify(record.read()), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@snakes_bp.route('/complete-lesson', methods=['POST'])
@token_required()
def complete_lesson():
    """Mark a lesson as completed and award bullets."""
    try:
        user = g.current_user
        data = request.get_json() or {}
        lesson_number = data.get('lesson_number')
        bullets_earned = data.get('bullets_earned', 0)
        if not isinstance(lesson_number, int) or lesson_number < 1 or lesson_number > 5:
            return jsonify({'error': 'Invalid lesson number'}), 400
        record = SnakesGameData.query.filter_by(user_id=user.id).first()
        if not record:
            return jsonify({'error': 'Game data not found'}), 404
        # Update lesson completion if not already
        if lesson_number not in record.completed_lessons:
            record.completed_lessons.append(lesson_number)
            record.total_bullets += bullets_earned
            # Unlock second half after completing all five lessons
            if len(set(record.completed_lessons)) >= 5 and 'half2' not in record.unlocked_sections:
                record.unlocked_sections.append('half2')
        db.session.commit()
        return jsonify({
            'message': f'Lesson {lesson_number} completed',
            'completed_lessons': record.completed_lessons,
            'total_bullets': record.total_bullets,
            'unlocked_sections': record.unlocked_sections
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@snakes_bp.route('/answer-question', methods=['POST'])
@token_required()
def answer_question():
    """Record the result of answering a question square."""
    try:
        user = g.current_user
        data = request.get_json() or {}
        square = data.get('square')
        bullets_earned = data.get('bullets_earned', 0)
        correct = bool(data.get('correct'))
        # Validate square
        if not isinstance(square, int) or square < 26 or square > 50:
            return jsonify({'error': 'Invalid square number'}), 400
        record = SnakesGameData.query.filter_by(user_id=user.id).first()
        if not record:
            return jsonify({'error': 'Game data not found'}), 404
        # Update square and visited list
        record.current_square = square
        if square not in record.visited_squares:
            record.visited_squares.append(square)
        # Award bullets for correct answers
        if correct:
            record.total_bullets += bullets_earned
        # Unlock boss battle when reaching square 50
        if square >= 50 and 'boss' not in record.unlocked_sections:
            record.unlocked_sections.append('boss')
        db.session.commit()
        return jsonify({
            'message': 'Question processed',
            'total_bullets': record.total_bullets,
            'unlocked_sections': record.unlocked_sections
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@snakes_bp.route('/', methods=['PUT'])
@token_required()
def update_snakes_game():
    """Update the user's game state (current_square, visited_squares, etc.)."""
    try:
        user = g.current_user
        payload = request.get_json() or {}
        record = SnakesGameData.query.filter_by(user_id=user.id).first()
        if not record:
            return jsonify({'error': 'Game data not found'}), 404
        record.update(payload)
        return jsonify(record.read()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@snakes_bp.route('/leaderboard', methods=['GET'])
def leaderboard():
    """Return a simple leaderboard sorted by bullets."""
    try:
        players = SnakesGameData.get_leaderboard(limit=10)
        result = [
            {
                'username': p.username,
                'total_bullets': p.total_bullets,
                'time_played': p.time_played
            }
            for p in players
        ]
        return jsonify(result), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
