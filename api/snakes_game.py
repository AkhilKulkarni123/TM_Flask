from flask import Blueprint, request, g
from flask_restful import Api, Resource
from model.snakes_game import SnakesGameData
from __init__ import db
from api.jwt_authorize import token_required

snakes_game_api = Blueprint('snakes_game_api', __name__, url_prefix='/api/snakes')
api = Api(snakes_game_api)


class SnakesGameAPI(Resource):
    @token_required()
    def get(self):
        current_user = g.current_user
        game_data = SnakesGameData.query.filter_by(user_id=current_user.id).first()

        if game_data:
            # flask_restful will JSON serialize this dict
            return game_data.read(), 200

        return {"message": "No game data found"}, 404

    @token_required()
    def post(self):
        current_user = g.current_user
        data = request.get_json() or {}

        existing = SnakesGameData.query.filter_by(user_id=current_user.id).first()
        if existing:
            return {"message": "Game data already exists"}, 400

        character = data.get("selected_character", "default")

        game_data = SnakesGameData(
            user_id=current_user.id,
            username=current_user.name,
            selected_character=character,
        )

        created = game_data.create()
        if created:
            return created.read(), 201

        return {"message": "Failed to create game data"}, 500

    @token_required()
    def put(self):
        current_user = g.current_user
        data = request.get_json() or {}

        game_data = SnakesGameData.query.filter_by(user_id=current_user.id).first()
        if not game_data:
            return {"message": "Game data not found"}, 404

        updated = game_data.update(data)
        return updated.read(), 200

    @token_required()
    def delete(self):
        current_user = g.current_user
        game_data = SnakesGameData.query.filter_by(user_id=current_user.id).first()

        if game_data:
            game_data.delete()
            return {"message": "Game data deleted"}, 200

        return {"message": "Game data not found"}, 404


class LeaderboardAPI(Resource):
    def get(self):
        limit = request.args.get("limit", 10, type=int)
        players = SnakesGameData.get_leaderboard(limit)

        leaderboard = [player.read() for player in players]

        return {
            "leaderboard": leaderboard,
            "count": len(leaderboard),
        }, 200


class AddBulletsAPI(Resource):
    @token_required()
    def post(self):
        current_user = g.current_user
        data = request.get_json() or {}
        bullets_earned = data.get("bullets", 0)

        game_data = SnakesGameData.query.filter_by(user_id=current_user.id).first()
        if not game_data:
            return {"message": "Game data not found"}, 404

        game_data.total_bullets += bullets_earned
        db.session.commit()

        return {
            "total_bullets": game_data.total_bullets,
            "bullets_earned": bullets_earned,
        }, 200


class UpdateSquareAPI(Resource):
    @token_required()
    def post(self):
        current_user = g.current_user
        data = request.get_json() or {}
        square_number = data.get("square", 1)

        game_data = SnakesGameData.query.filter_by(user_id=current_user.id).first()
        if not game_data:
            return {"message": "Game data not found"}, 404

        game_data.current_square = square_number

        visited = game_data.visited_squares or []
        if square_number not in visited:
            visited.append(square_number)
            game_data.visited_squares = visited

        db.session.commit()

        return {
            "current_square": game_data.current_square,
            "visited_squares": game_data.visited_squares,
        }, 200


class ResetPositionAPI(Resource):
    @token_required()
    def post(self):
        current_user = g.current_user
        reset_player = SnakesGameData.reset_player_position(current_user.id)

        if reset_player:
            return {
                "message": "Position reset",
                "data": reset_player.read(),
            }, 200

        return {"message": "Failed to reset position"}, 500


class GetUnvisitedSquaresAPI(Resource):
    @token_required()
    def get(self):
        current_user = g.current_user
        game_data = SnakesGameData.query.filter_by(user_id=current_user.id).first()

        if not game_data:
            return {"message": "Game data not found"}, 404

        all_squares = list(range(1, 101))
        visited = game_data.visited_squares or []
        unvisited = [sq for sq in all_squares if sq not in visited]

        return {
            "unvisited_squares": unvisited,
            "visited_count": len(visited),
            "unvisited_count": len(unvisited),
        }, 200


class ActivePlayersAPI(Resource):
    def get(self):
        """Get count of all players who have started the game"""
        total_players = SnakesGameData.query.count()
        return {
            "active_players": total_players,
            "message": "Total players who have joined the game"
        }, 200


api.add_resource(SnakesGameAPI, "/")
api.add_resource(LeaderboardAPI, "/leaderboard")
api.add_resource(AddBulletsAPI, "/add-bullets")
api.add_resource(UpdateSquareAPI, "/update-square")
api.add_resource(ResetPositionAPI, "/reset-position")
api.add_resource(GetUnvisitedSquaresAPI, "/unvisited-squares")
api.add_resource(ActivePlayersAPI, "/active-players")
