from flask import Blueprint, request, g
from flask_restful import Api, Resource
from datetime import datetime, timedelta
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
        """Get count and list of players actively playing (updated within last 10 seconds)"""
        # Only count players who have made movements/changes in the last 10 seconds
        active_threshold = datetime.utcnow() - timedelta(seconds=10)
        active_players_query = SnakesGameData.query.filter(
            SnakesGameData.last_updated >= active_threshold
        ).order_by(SnakesGameData.last_updated.desc()).all()

        # Build list of active players with relevant info
        players_list = []
        for player in active_players_query:
            players_list.append({
                "user_id": player.user_id,
                "username": player.username,
                "selected_character": player.selected_character,
                "current_square": player.current_square,
                "total_bullets": player.total_bullets,
                "last_updated": player.last_updated.isoformat() if player.last_updated else None
            })

        return {
            "active_players": len(players_list),
            "players": players_list,
            "message": "Players actively playing (updated in last 10 seconds)"
        }, 200


class ChampionsAPI(Resource):
    """Get all players who have completed the game"""
    def get(self):
        # Get all players with game_status='completed', ordered by completion time
        champions = SnakesGameData.query.filter_by(
            game_status='completed'
        ).order_by(SnakesGameData.completed_at.asc()).all()

        champions_list = []
        for champion in champions:
            champions_list.append({
                "user_id": champion.user_id,
                "username": champion.username,
                "character": champion.selected_character,
                "total_bullets": champion.total_bullets,
                "time_played": champion.time_played,
                "completed_at": champion.completed_at.isoformat() if champion.completed_at else None
            })

        return {
            "champions": champions_list,
            "count": len(champions_list)
        }, 200


class CompleteGameAPI(Resource):
    """Mark the game as completed for the current user"""
    @token_required()
    def post(self):
        current_user = g.current_user
        game_data = SnakesGameData.query.filter_by(user_id=current_user.id).first()

        if not game_data:
            return {"message": "Game data not found"}, 404

        # Mark game as completed
        game_data.game_status = 'completed'
        game_data.completed_at = datetime.utcnow()
        db.session.commit()

        return {
            "message": "Congratulations! Game completed!",
            "data": game_data.read()
        }, 200


class ResetProgressAPI(Resource):
    """Reset all game progress for the current user to start fresh"""
    @token_required()
    def post(self):
        current_user = g.current_user
        game_data = SnakesGameData.query.filter_by(user_id=current_user.id).first()

        if not game_data:
            return {"message": "Game data not found"}, 404

        # Preserve champion status if they completed before
        was_champion = game_data.game_status == 'completed'

        # Reset all progress
        game_data.current_square = 1
        game_data.visited_squares = [1]
        game_data.total_bullets = 0
        game_data.time_played = 0.0
        game_data.lives = 5
        game_data.boss_battle_attempts = 0
        game_data.completed_lessons = []
        game_data.unlocked_sections = ['half1']
        game_data.selected_character = 'default'  # Reset character so they can choose again
        game_data.game_status = 'active'
        # Keep completed_at if they were a champion (for hall of fame)

        db.session.commit()

        return {
            "message": "Progress reset successfully! Starting fresh.",
            "was_champion": was_champion,
            "data": game_data.read()
        }, 200


api.add_resource(SnakesGameAPI, "/")
api.add_resource(LeaderboardAPI, "/leaderboard")
api.add_resource(AddBulletsAPI, "/add-bullets")
api.add_resource(UpdateSquareAPI, "/update-square")
api.add_resource(ResetPositionAPI, "/reset-position")
api.add_resource(GetUnvisitedSquaresAPI, "/unvisited-squares")
api.add_resource(ActivePlayersAPI, "/active-players")
api.add_resource(ChampionsAPI, "/champions")
api.add_resource(CompleteGameAPI, "/complete")
api.add_resource(ResetProgressAPI, "/reset")
