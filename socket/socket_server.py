# imports from flask
from flask_socketio import SocketIO, send, emit
from flask import Flask

# Import boss battle socket handlers
from boss_battle import init_boss_battle_socket

app = Flask(__name__)

# Socket.IO server - runs on port 8500 for real-time multiplayer
# Allow all origins in development for easier testing
socketio = SocketIO(app, cors_allowed_origins="*")

# Initialize boss battle socket handlers
init_boss_battle_socket(socketio)

players = []  # Keep a list of players and scores

@socketio.on("player_join")
def handle_player_join(data):
    name = data.get("name")
    if name:
        players.append({"name": name, "score": 0})
        emit("player_joined", {"name": name}, broadcast=True)

@socketio.on("player_score")
def handle_player_score(data):
    name = data.get("name")
    score = data.get("score", 0)
    for p in players:
        if p["name"] == name:
            p["score"] = score
            break
    # Sort and broadcast leaderboard
    leaderboard = sorted(players, key=lambda x: x["score"], reverse=True)
    emit("leaderboard_update", leaderboard, broadcast=True)

@socketio.on("clear_leaderboard")
def handle_clear_leaderboard():
    global players
    players = []
    emit("leaderboard_update", players, broadcast=True)

@socketio.on("get_leaderboard")
def handle_get_leaderboard():
    # Sort and emit current leaderboard
    leaderboard = sorted(players, key=lambda x: x["score"], reverse=True)
    emit("leaderboard_update", leaderboard)

@socketio.on('connect')
def handle_connect():
    print(f"[SOCKET] Client connected: {__import__('flask').request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    print(f"[SOCKET] Client disconnected: {__import__('flask').request.sid}")


# this runs the flask application on the development server
if __name__ == "__main__":
    print("\n" + "="*60)
    print("🔌 Socket.IO Server starting on port 8500")
    print("   - Boss Battle multiplayer enabled")
    print("   - Leaderboard sync enabled")
    print("="*60 + "\n")
    socketio.run(app, debug=True, host="0.0.0.0", port=8500, allow_unsafe_werkzeug=True)
