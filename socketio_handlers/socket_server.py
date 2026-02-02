# imports from flask
from flask_socketio import SocketIO, send, emit
from flask import Flask, jsonify
from flask_cors import CORS

# Import boss battle socket handlers
from boss_battle import init_boss_battle_socket

app = Flask(__name__)

# Add CORS support for the Flask app
CORS(app, origins="*", supports_credentials=True)

# Socket.IO server - runs on port 8500 for real-time multiplayer
# Allow all origins for cross-domain socket connections
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='eventlet',
    logger=True,
    engineio_logger=True,
    ping_timeout=60,
    ping_interval=25
)

# Health check endpoint
@app.route('/health')
def health_check():
    return jsonify({
        'status': 'ok',
        'service': 'socket-server',
        'message': 'Socket.IO server is running'
    }), 200

@app.route('/')
def index():
    return jsonify({
        'status': 'ok',
        'service': 'socket-server',
        'endpoints': {
            'health': '/health',
            'socket': 'ws://host:port/socket.io/'
        }
    }), 200

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

# NOTE: disconnect handler is in boss_battle.py to properly clean up battle rooms
# Do NOT add a duplicate disconnect handler here


# this runs the flask application on the development server
if __name__ == "__main__":
    print("\n" + "="*60)
    print("🔌 Socket.IO Server starting on port 8500")
    print("   - Boss Battle multiplayer enabled")
    print("   - Leaderboard sync enabled")
    print("="*60 + "\n")
    socketio.run(app, debug=True, host="0.0.0.0", port=8500, allow_unsafe_werkzeug=True)
