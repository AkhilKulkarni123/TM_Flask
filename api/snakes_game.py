from flask import Blueprint, request, jsonify
from flask_restful import Api, Resource
from model.snakes_game import SnakesGameData
from model.user import User
from flask_login import current_user, login_required
from __init__ import db

snakes_game_api = Blueprint('snakes_game_api', __name__, url_prefix='/api/snakes')
api = Api(snakes_game_api)


class SnakesGameAPI(Resource):
    @login_required
    def get(self):
        game_data = SnakesGameData.query.filter_by(user_id=current_user.id).first()
        if game_data:
            return jsonify(game_data.read())
        return jsonify({'message': 'No game data found'}), 404
    
    @login_required
    def post(self):
        data = request.get_json()
        existing = SnakesGameData.query.filter_by(user_id=current_user.id).first()
        if existing:
            return jsonify({'message': 'Game data already exists'}), 400
        
        character = data.get('selected_character', 'default')
        game_data = SnakesGameData(
            user_id=current_user.id,
            username=current_user.name,
            selected_character=character
        )
        
        created = game_data.create()
        if created:
            return jsonify(created.read()), 201
        return jsonify({'message': 'Failed to create game data'}), 500
    
    @login_required
    def put(self):
        data = request.get_json()
        game_data = SnakesGameData.query.filter_by(user_id=current_user.id).first()
        
        if not game_data:
            return jsonify({'message': 'Game data not found'}), 404
        
        updated = game_data.update(data)
        return jsonify(updated.read())
    
    @login_required
    def delete(self):
        game_data = SnakesGameData.query.filter_by(user_id=current_user.id).first()
        if game_data:
            game_data.delete()
            return jsonify({'message': 'Game data deleted'}), 200
        return jsonify({'message': 'Game data not found'}), 404


class LeaderboardAPI(Resource):
    def get(self):
        limit = request.args.get('limit', 10, type=int)
        players = SnakesGameData.get_leaderboard(limit)
        
        leaderboard = []
        for player in players:
            leaderboard.append(player.read())
        
        return jsonify({
            'leaderboard': leaderboard,
            'count': len(leaderboard)
        })


class AddBulletsAPI(Resource):
    @login_required
    def post(self):
        data = request.get_json()
        bullets_earned = data.get('bullets', 0)
        
        game_data = SnakesGameData.query.filter_by(user_id=current_user.id).first()
        if not game_data:
            return jsonify({'message': 'Game data not found'}), 404
        
        game_data.total_bullets += bullets_earned
        db.session.commit()
        
        return jsonify({
            'total_bullets': game_data.total_bullets,
            'bullets_earned': bullets_earned
        })


class UpdateSquareAPI(Resource):
    @login_required
    def post(self):
        data = request.get_json()
        square_number = data.get('square', 1)
        
        game_data = SnakesGameData.query.filter_by(user_id=current_user.id).first()
        if not game_data:
            return jsonify({'message': 'Game data not found'}), 404
        
        game_data.current_square = square_number
        
        visited = game_data.visited_squares or []
        if square_number not in visited:
            visited.append(square_number)
            game_data.visited_squares = visited
        
        db.session.commit()
        
        return jsonify({
            'current_square': game_data.current_square,
            'visited_squares': game_data.visited_squares
        })


class ResetPositionAPI(Resource):
    @login_required
    def post(self):
        reset_player = SnakesGameData.reset_player_position(current_user.id)
        if reset_player:
            return jsonify({
                'message': 'Position reset',
                'data': reset_player.read()
            })
        return jsonify({'message': 'Failed to reset position'}), 500


class GetUnvisitedSquaresAPI(Resource):
    @login_required
    def get(self):
        game_data = SnakesGameData.query.filter_by(user_id=current_user.id).first()
        if not game_data:
            return jsonify({'message': 'Game data not found'}), 404
        
        all_squares = list(range(1, 101))
        visited = game_data.visited_squares or []
        unvisited = [sq for sq in all_squares if sq not in visited]
        
        return jsonify({
            'unvisited_squares': unvisited,
            'visited_count': len(visited),
            'unvisited_count': len(unvisited)
        })


api.add_resource(SnakesGameAPI, '/')
api.add_resource(LeaderboardAPI, '/leaderboard')
api.add_resource(AddBulletsAPI, '/add-bullets')
api.add_resource(UpdateSquareAPI, '/update-square')
api.add_resource(ResetPositionAPI, '/reset-position')
api.add_resource(GetUnvisitedSquaresAPI, '/unvisited-squares')