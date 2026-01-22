from flask_socketio import SocketIO, emit, join_room, leave_room, rooms
from flask import request
from flask_login import current_user
import json

# Boss battle state - keyed by room_id
boss_battles = {}  # { room_id: { boss_health, max_health, players: { sid: player_data } } }

# Map socket session IDs to room IDs for cleanup on disconnect
sid_to_room = {}  # { sid: room_id }

MAX_PLAYERS_PER_ROOM = 7

def init_boss_battle_socket(socketio):

    def get_room_players_list(room_id):
        """Get list of players in a room with their data"""
        if room_id not in boss_battles:
            return []
        return [
            {**p, 'sid': sid}
            for sid, p in boss_battles[room_id]['players'].items()
        ]

    def get_alive_count(room_id):
        """Get count of alive players in a room"""
        if room_id not in boss_battles:
            return 0
        return len([p for p in boss_battles[room_id]['players'].values() if p.get('status') == 'alive'])

    # ==================== JOIN ROOM ====================
    @socketio.on('boss_join_room')
    def handle_join_room(data):
        """Handle player joining a boss battle room"""
        room_id = data.get('room_id', 'default_room')
        player_data = data.get('player', {})
        boss_health = data.get('boss_health', 1000)
        max_boss_health = data.get('max_boss_health', 1000)

        sid = request.sid
        username = player_data.get('username', 'Guest')
        user_id = player_data.get('user_id', sid)
        character = player_data.get('character', 'knight')
        bullets = player_data.get('bullets', 0)
        lives = player_data.get('lives', 3)
        x = player_data.get('x', 400)
        y = player_data.get('y', 500)

        # Initialize room if it doesn't exist
        if room_id not in boss_battles:
            boss_battles[room_id] = {
                'boss_health': boss_health,
                'max_health': max_boss_health,
                'players': {}
            }

        # Check if room is full
        if len(boss_battles[room_id]['players']) >= MAX_PLAYERS_PER_ROOM:
            emit('boss_room_full', {'message': 'Room is full (max 7 players)'})
            return

        # Join the socket room
        join_room(room_id)
        sid_to_room[sid] = room_id

        # Add player to battle
        boss_battles[room_id]['players'][sid] = {
            'username': username,
            'user_id': user_id,
            'character': character,
            'bullets': bullets,
            'lives': lives,
            'x': x,
            'y': y,
            'status': 'alive'
        }

        player_count = len(boss_battles[room_id]['players'])
        players_list = get_room_players_list(room_id)

        # Send room state to the joining player (including powerups)
        room_powerups = boss_battles[room_id].get('powerups', [])
        emit('boss_room_state', {
            'bossHealth': boss_battles[room_id]['boss_health'],
            'maxBossHealth': boss_battles[room_id]['max_health'],
            'playerCount': player_count,
            'players': players_list,
            'powerups': room_powerups
        })

        # Notify all OTHER players in the room that someone joined
        emit('boss_player_joined', {
            'player': {
                'sid': sid,
                'username': username,
                'user_id': user_id,
                'character': character,
                'bullets': bullets,
                'lives': lives,
                'x': x,
                'y': y,
                'status': 'alive'
            },
            'playerCount': player_count
        }, room=room_id, include_self=False)

        print(f"[BOSS] Player {username} ({sid}) joined room {room_id}. Total players: {player_count}")

    # ==================== PLAYER MOVEMENT ====================
    @socketio.on('boss_player_move')
    def handle_player_move(data):
        """Handle player position updates - broadcast to all other players"""
        room_id = data.get('room_id')
        x = data.get('x')
        y = data.get('y')
        sid = request.sid

        if not room_id or room_id not in boss_battles:
            return

        if sid not in boss_battles[room_id]['players']:
            return

        # Update stored position
        boss_battles[room_id]['players'][sid]['x'] = x
        boss_battles[room_id]['players'][sid]['y'] = y

        # Broadcast position to all OTHER players in the room
        emit('boss_player_position', {
            'sid': sid,
            'x': x,
            'y': y
        }, room=room_id, include_self=False)

    # ==================== PLAYER STATS UPDATE ====================
    @socketio.on('boss_player_stats')
    def handle_player_stats(data):
        """Handle player stats updates (bullets, lives)"""
        room_id = data.get('room_id')
        bullets = data.get('bullets')
        lives = data.get('lives')
        sid = request.sid

        if not room_id or room_id not in boss_battles:
            return

        if sid not in boss_battles[room_id]['players']:
            return

        player = boss_battles[room_id]['players'][sid]

        if bullets is not None:
            player['bullets'] = bullets
        if lives is not None:
            player['lives'] = lives

        # Broadcast stats update to all OTHER players
        emit('boss_player_stats_update', {
            'player': {
                'sid': sid,
                'username': player['username'],
                'bullets': player['bullets'],
                'lives': player['lives']
            }
        }, room=room_id, include_self=False)

    # ==================== BOSS DAMAGE ====================
    @socketio.on('boss_damage')
    def handle_boss_damage(data):
        """Handle boss taking damage from a player"""
        room_id = data.get('room_id')
        damage = data.get('damage', 10)
        sid = request.sid

        if not room_id or room_id not in boss_battles:
            return

        if sid not in boss_battles[room_id]['players']:
            return

        player = boss_battles[room_id]['players'][sid]

        # Check if player is alive
        if player.get('status') != 'alive':
            return

        # Reduce boss health
        boss_battles[room_id]['boss_health'] -= damage
        if boss_battles[room_id]['boss_health'] < 0:
            boss_battles[room_id]['boss_health'] = 0

        # Broadcast boss health update to ALL players in room
        emit('boss_health_update', {
            'bossHealth': boss_battles[room_id]['boss_health'],
            'maxBossHealth': boss_battles[room_id]['max_health'],
            'attacker': player['username'],
            'damage': damage
        }, room=room_id)

        # Check if boss is defeated
        if boss_battles[room_id]['boss_health'] <= 0:
            emit('boss_defeated', {
                'message': 'The boss has been defeated!',
                'players': get_room_players_list(room_id)
            }, room=room_id)

            # Reset boss for next battle
            boss_battles[room_id]['boss_health'] = boss_battles[room_id]['max_health']
            print(f"[BOSS] Boss defeated in room {room_id}! Resetting boss health.")

    # ==================== PLAYER HIT ====================
    @socketio.on('boss_player_hit')
    def handle_player_hit(data):
        """Handle player taking damage from boss"""
        room_id = data.get('room_id')
        lives = data.get('lives')
        sid = request.sid

        if not room_id or room_id not in boss_battles:
            return

        if sid not in boss_battles[room_id]['players']:
            return

        player = boss_battles[room_id]['players'][sid]

        if lives is not None:
            player['lives'] = lives

        # Check if player died
        if player['lives'] <= 0:
            player['lives'] = 0
            player['status'] = 'dead'

            # Notify all players that this player died
            emit('boss_player_died', {
                'message': f"{player['username']} has fallen!",
                'player': {
                    'sid': sid,
                    'username': player['username'],
                    'lives': 0
                },
                'aliveCount': get_alive_count(room_id)
            }, room=room_id)
        else:
            # Notify all players of damage
            emit('boss_player_damaged', {
                'player': {
                    'sid': sid,
                    'username': player['username'],
                    'lives': player['lives']
                }
            }, room=room_id)

    # ==================== PLAYER AWAY (TAB SWITCH) ====================
    @socketio.on('boss_player_away')
    def handle_player_away(data):
        """Handle player switching away from the tab"""
        room_id = data.get('room_id')
        username = data.get('username', 'Unknown')
        sid = request.sid

        if not room_id or room_id not in boss_battles:
            return

        if sid in boss_battles[room_id]['players']:
            boss_battles[room_id]['players'][sid]['isAway'] = True

        # Notify all OTHER players that this player is away
        emit('boss_player_away', {
            'sid': sid,
            'username': username,
            'message': f'{username} switched away from the game'
        }, room=room_id, include_self=False)

        print(f"[BOSS] Player {username} ({sid}) went away from room {room_id}")

    # ==================== PLAYER RETURNED ====================
    @socketio.on('boss_player_returned')
    def handle_player_returned(data):
        """Handle player returning to the tab"""
        room_id = data.get('room_id')
        username = data.get('username', 'Unknown')
        sid = request.sid

        if not room_id or room_id not in boss_battles:
            return

        if sid in boss_battles[room_id]['players']:
            boss_battles[room_id]['players'][sid]['isAway'] = False

        # Notify all OTHER players that this player is back
        emit('boss_player_returned', {
            'sid': sid,
            'username': username,
            'message': f'{username} is back!'
        }, room=room_id, include_self=False)

        print(f"[BOSS] Player {username} ({sid}) returned to room {room_id}")

    # ==================== CHAT MESSAGE ====================
    @socketio.on('boss_chat_send')
    def handle_chat_message(data):
        """Handle chat messages - broadcast to all players in room"""
        room_id = data.get('room_id')
        content = data.get('content', '')
        username = data.get('username', 'Anonymous')
        character = data.get('character', 'knight')
        sid = request.sid

        if not room_id or not content:
            return

        # Sanitize content (basic length limit)
        content = content[:280]

        # Broadcast chat message to ALL players in room (including sender for confirmation)
        emit('boss_chat_message', {
            'sid': sid,
            'username': username,
            'character': character,
            'content': content
        }, room=room_id)

        print(f"[CHAT] {username}: {content[:50]}...")

    # ==================== LEAVE ROOM ====================
    @socketio.on('boss_leave_room')
    def handle_leave_room(data):
        """Handle player leaving a boss battle room"""
        room_id = data.get('room_id')
        sid = request.sid

        if not room_id or room_id not in boss_battles:
            return

        if sid not in boss_battles[room_id]['players']:
            return

        player = boss_battles[room_id]['players'][sid]
        username = player['username']

        # Remove player from battle
        del boss_battles[room_id]['players'][sid]

        # Clean up sid mapping
        if sid in sid_to_room:
            del sid_to_room[sid]

        # Leave the socket room
        leave_room(room_id)

        player_count = len(boss_battles[room_id]['players'])

        # Notify remaining players
        emit('boss_player_left', {
            'player': {
                'sid': sid,
                'username': username
            },
            'playerCount': player_count
        }, room=room_id)

        print(f"[BOSS] Player {username} ({sid}) left room {room_id}. Remaining: {player_count}")

        # Clean up empty rooms
        if player_count == 0:
            del boss_battles[room_id]
            print(f"[BOSS] Room {room_id} is empty, removed.")

    # ==================== DISCONNECT ====================
    @socketio.on('disconnect')
    def handle_disconnect():
        """Clean up player from any active battles on disconnect"""
        sid = request.sid

        # Find which room this player was in
        room_id = sid_to_room.get(sid)

        if room_id and room_id in boss_battles:
            if sid in boss_battles[room_id]['players']:
                player = boss_battles[room_id]['players'][sid]
                username = player['username']

                # Remove player from battle
                del boss_battles[room_id]['players'][sid]

                player_count = len(boss_battles[room_id]['players'])

                # Notify remaining players
                emit('boss_player_left', {
                    'player': {
                        'sid': sid,
                        'username': username
                    },
                    'playerCount': player_count
                }, room=room_id)

                print(f"[BOSS] Player {username} ({sid}) disconnected from room {room_id}. Remaining: {player_count}")

                # Clean up empty rooms
                if player_count == 0:
                    del boss_battles[room_id]
                    print(f"[BOSS] Room {room_id} is empty, removed.")

        # Clean up sid mapping
        if sid in sid_to_room:
            del sid_to_room[sid]

    # ==================== POWERUP SYSTEM ====================
    import random
    import time
    import uuid

    POWERUP_TYPES = ['damage', 'speed', 'rapidfire', 'heal']
    POWERUP_SPAWN_INTERVAL = 15  # seconds between spawns
    last_powerup_spawn = {}  # { room_id: timestamp }

    def spawn_powerup_for_room(room_id):
        """Spawn a powerup at a random location for a room"""
        if room_id not in boss_battles:
            return None

        powerup_type = random.choice(POWERUP_TYPES)
        # Spawn in the middle-bottom play area (avoiding boss zone)
        x = random.randint(100, 700)
        y = random.randint(300, 550)
        powerup_id = str(uuid.uuid4())[:8]

        powerup = {
            'id': powerup_id,
            'type': powerup_type,
            'x': x,
            'y': y,
            'spawned_at': time.time()
        }

        # Store powerup in room state
        if 'powerups' not in boss_battles[room_id]:
            boss_battles[room_id]['powerups'] = []
        boss_battles[room_id]['powerups'].append(powerup)

        return powerup

    @socketio.on('boss_request_powerup_spawn')
    def handle_request_powerup_spawn(data):
        """Handle request to potentially spawn a powerup (rate limited)"""
        room_id = data.get('room_id')

        if not room_id or room_id not in boss_battles:
            return

        current_time = time.time()
        last_spawn = last_powerup_spawn.get(room_id, 0)

        # Only spawn if enough time has passed
        if current_time - last_spawn >= POWERUP_SPAWN_INTERVAL:
            # Random chance to spawn (30% per check)
            if random.random() < 0.3:
                powerup = spawn_powerup_for_room(room_id)
                if powerup:
                    last_powerup_spawn[room_id] = current_time
                    emit('boss_powerup_spawned', powerup, room=room_id)
                    print(f"[BOSS] Powerup {powerup['type']} spawned in room {room_id}")

    @socketio.on('boss_powerup_collected')
    def handle_powerup_collected(data):
        """Handle a player collecting a powerup"""
        room_id = data.get('room_id')
        powerup_id = data.get('powerup_id')
        username = data.get('username', 'Unknown')

        if not room_id or room_id not in boss_battles:
            return

        # Find and remove the powerup
        powerup_type = None
        if 'powerups' in boss_battles[room_id]:
            for i, p in enumerate(boss_battles[room_id]['powerups']):
                if p['id'] == powerup_id:
                    powerup_type = p['type']
                    boss_battles[room_id]['powerups'].pop(i)
                    break

        # Notify all players that powerup was collected
        emit('boss_powerup_collected', {
            'powerup_id': powerup_id,
            'type': powerup_type,
            'username': username,
            'collector_sid': request.sid
        }, room=room_id)

        print(f"[BOSS] Player {username} collected powerup {powerup_id} ({powerup_type}) in room {room_id}")

    # Periodic powerup spawning is triggered by clients
    # to avoid needing a background thread

    # ==================== LEGACY EVENT HANDLERS ====================
    # Keep old event names working for backwards compatibility

    @socketio.on('join_boss_battle')
    def handle_legacy_join(data):
        """Legacy handler - convert to new format"""
        username = data.get('username', 'Guest')
        user_id = data.get('user_id', 'guest')
        bullets = data.get('bullets', 0)
        character = data.get('character', 'knight')

        # Convert to new format
        new_data = {
            'room_id': 'boss_battle_room',
            'player': {
                'username': username,
                'user_id': user_id,
                'bullets': bullets,
                'character': character,
                'lives': 3,
                'x': 400,
                'y': 500
            },
            'boss_health': 1000,
            'max_boss_health': 1000
        }
        handle_join_room(new_data)

    @socketio.on('attack_boss')
    def handle_legacy_attack(data):
        """Legacy handler for attack_boss"""
        data['room_id'] = data.get('room_id', 'boss_battle_room')
        handle_boss_damage(data)

    @socketio.on('player_hit')
    def handle_legacy_player_hit(data):
        """Legacy handler for player_hit"""
        data['room_id'] = data.get('room_id', 'boss_battle_room')
        handle_player_hit(data)

    @socketio.on('leave_boss_battle')
    def handle_legacy_leave(data):
        """Legacy handler for leave_boss_battle"""
        data['room_id'] = data.get('room_id', 'boss_battle_room')
        handle_leave_room(data)