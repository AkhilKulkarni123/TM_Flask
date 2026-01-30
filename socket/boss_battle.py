from flask_socketio import SocketIO, emit, join_room, leave_room, rooms
from flask import request
from flask_login import current_user
import json

# Boss battle state - keyed by room_id
boss_battles = {}  # { room_id: { boss_health, max_health, players: { sid: player_data } } }

# Map socket session IDs to room IDs for cleanup on disconnect
sid_to_room = {}  # { sid: room_id }

# Lobby members for pre-battle chat - { sid: { username, character } }
lobby_members = {}

# Shared lobby room name (moved to module level for proper scope)
LOBBY_ROOM = 'boss_lobby'

MAX_PLAYERS_PER_ROOM = 10

# PVP Arena state - max 2 players
pvp_room = {
    'players': {},  # { sid: player_data }
    'player_order': [],  # [sid1, sid2] - order of joining
    'battle_active': False,
    'ready_players': set()
}
pvp_sid_mapping = {}  # { sid: True } - tracks who's in PVP
PVP_ROOM_NAME = 'pvp_arena'
MAX_PVP_PLAYERS = 2

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
            emit('boss_room_full', {'message': 'Room is full (max 10 players)'})
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

    # ==================== PLAYER SHOOT (BULLET FIRED) ====================
    # Frontend format
    @socketio.on('boss_player_shoot')
    def handle_boss_player_shoot(data):
        room_id = data.get('room_id')
        if room_id:
            emit('boss_player_bullet', {
                'bulletX': data.get('bulletX'),
                'bulletY': data.get('bulletY'),
                'dx': data.get('dx'),
                'dy': data.get('dy'),
                'character': data.get('character')
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
            username = player['username']

            # Remove dead player from the room
            del boss_battles[room_id]['players'][sid]

            # Clean up sid mapping
            if sid in sid_to_room:
                del sid_to_room[sid]

            player_count = len(boss_battles[room_id]['players'])
            alive_count = get_alive_count(room_id)

            # Notify all players that this player died and update player count
            emit('boss_player_died', {
                'message': f"{username} has fallen!",
                'player': {
                    'sid': sid,
                    'username': username,
                    'lives': 0
                },
                'aliveCount': alive_count,
                'playerCount': player_count
            }, room=room_id)

            # Also emit player left event so frontend removes them from player list
            emit('boss_player_left', {
                'player': {
                    'sid': sid,
                    'username': username
                },
                'playerCount': player_count,
                'reason': 'died'
            }, room=room_id)

            print(f"[BOSS] Player {username} ({sid}) died and removed from room {room_id}. Remaining: {player_count}")

            # Clean up empty rooms
            if player_count == 0:
                del boss_battles[room_id]
                print(f"[BOSS] Room {room_id} is empty, removed.")
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

    # ==================== LOBBY (PRE-BATTLE CHAT) ====================
    # Note: LOBBY_ROOM is defined at module level for proper scope

    @socketio.on('boss_join_lobby')
    def handle_join_lobby(data):
        """Handle player joining the lobby for pre-battle chat"""
        username = data.get('username', 'Guest')
        character = data.get('character', 'knight')
        sid = request.sid

        # Join the lobby socket room
        join_room(LOBBY_ROOM)

        # Track lobby member
        lobby_members[sid] = {
            'username': username,
            'character': character
        }

        lobby_count = len(lobby_members)

        # Send lobby state to the joining player
        emit('boss_lobby_state', {
            'playerCount': lobby_count,
            'players': [{'sid': s, **p} for s, p in lobby_members.items()]
        })

        # Notify others in lobby via room broadcast
        message_data = {
            'sid': sid,
            'username': 'System',
            'character': 'knight',
            'content': f'{username} joined the lobby'
        }
        emit('boss_chat_message', message_data, room=LOBBY_ROOM, include_self=False)

        # Send current lobby members list to the joining player
        members_list = [{'sid': s, **m} for s, m in lobby_members.items()]
        emit('boss_lobby_members', {
            'members': members_list
        })
        
        # Also broadcast updated member list and player count to all lobby members
        emit('boss_lobby_members', {
            'members': members_list
        }, room=LOBBY_ROOM, include_self=False)
        emit('boss_lobby_player_count', {'playerCount': lobby_count}, room=LOBBY_ROOM, include_self=False)

        print(f"[BOSS] Player {username} ({sid}) joined lobby for chat. Lobby size: {lobby_count}")

    @socketio.on('boss_leave_lobby')
    def handle_leave_lobby(data):
        """Handle player leaving the lobby when they start battle"""
        sid = request.sid
        username = 'Unknown'
        if sid in lobby_members:
            username = lobby_members[sid].get('username', 'Unknown')
            del lobby_members[sid]
        leave_room(LOBBY_ROOM)
        
        lobby_count = len(lobby_members)
        
        # Notify remaining lobby members
        message_data = {
            'sid': sid,
            'username': 'System',
            'character': 'knight',
            'content': f'{username} left the lobby'
        }
        emit('boss_chat_message', message_data, room=LOBBY_ROOM)
        
        # Send updated member list and player count to remaining members
        emit('boss_lobby_members', {
            'members': [{'sid': s, **m} for s, m in lobby_members.items()]
        }, room=LOBBY_ROOM)
        emit('boss_lobby_player_count', {'playerCount': lobby_count}, room=LOBBY_ROOM)
        
        print(f"[BOSS] Player {username} ({sid}) left lobby. Lobby size: {lobby_count}")

    # ==================== CHAT MESSAGE ====================
    @socketio.on('boss_chat_send')
    def handle_chat_message(data):
        """Handle chat messages - broadcast to all OTHER players in room (sender shows locally)"""
        room_id = data.get('room_id')
        content = data.get('content', '')
        sid = request.sid

        print(f"[CHAT DEBUG] Received chat from {sid}, room_id={room_id}, content={content[:50] if content else 'empty'}")
        print(f"[CHAT DEBUG] Current boss_battles keys: {list(boss_battles.keys())}")
        print(f"[CHAT DEBUG] Current lobby_members: {list(lobby_members.keys())}")
        print(f"[CHAT DEBUG] sid_to_room mapping: {sid} -> {sid_to_room.get(sid, 'NOT FOUND')}")

        if not content:
            print(f"[CHAT DEBUG] No content, ignoring")
            return

        # Sanitize content (basic length limit)
        content = content[:280]

        # Get player info - check if they're in a battle room first
        username = data.get('username', 'Anonymous')
        character = data.get('character', 'knight')
        in_battle_room = False
        actual_room_id = None

        # First, check if player is tracked in sid_to_room (most reliable)
        if sid in sid_to_room:
            actual_room_id = sid_to_room[sid]
            if actual_room_id in boss_battles and sid in boss_battles[actual_room_id]['players']:
                player = boss_battles[actual_room_id]['players'][sid]
                username = player.get('username', 'Anonymous')
                character = player.get('character', 'knight')
                in_battle_room = True
                print(f"[CHAT DEBUG] Player {username} found via sid_to_room in {actual_room_id}")

        # Fallback: check if player is in the room they specified
        if not in_battle_room and room_id and room_id in boss_battles:
            if sid in boss_battles[room_id]['players']:
                player = boss_battles[room_id]['players'][sid]
                username = player.get('username', 'Anonymous')
                character = player.get('character', 'knight')
                in_battle_room = True
                actual_room_id = room_id
                print(f"[CHAT DEBUG] Player {username} found in specified room {room_id}")

        message_data = {
            'sid': sid,
            'username': username,
            'character': character,
            'content': content,
            'room_id': actual_room_id or room_id
        }

        if in_battle_room and actual_room_id:
            # Use room-based broadcast for battle rooms (exclude sender - they show message locally)
            emit('boss_chat_message', message_data, room=actual_room_id, include_self=False)
            print(f"[CHAT-BATTLE] {username}: {content[:50]}...")
        elif room_id == 'lobby' or room_id == LOBBY_ROOM or not room_id:
            # For lobby, use the lobby room broadcast (exclude sender)
            emit('boss_chat_message', message_data, room=LOBBY_ROOM, include_self=False)
            print(f"[CHAT-LOBBY] {username}: {content[:50]}...")
        elif room_id:
            # Fallback: player sent room_id but room doesn't exist yet or they're not in it
            emit('boss_chat_message', message_data, room=room_id, include_self=False)
            print(f"[CHAT] Fallback broadcast to room {room_id} - {username}: {content[:50]}...")
        else:
            # Last resort: no room found
            print(f"[CHAT] No room found for {username}, message not sent")

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
        """Clean up player from any active battles and lobby on disconnect"""
        sid = request.sid
        print(f"[SOCKET] Client disconnected: {sid}")

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

        # Clean up lobby membership and notify other lobby members
        if sid in lobby_members:
            username = lobby_members[sid].get('username', 'Unknown')
            del lobby_members[sid]
            # Notify remaining lobby members
            message_data = {
                'sid': sid,
                'username': 'System',
                'character': 'knight',
                'content': f'{username} disconnected'
            }
            emit('boss_chat_message', message_data, room=LOBBY_ROOM)
            # Send updated member list
            emit('boss_lobby_members', {
                'members': [{'sid': s, **m} for s, m in lobby_members.items()]
            }, room=LOBBY_ROOM)

            lobby_count = len(lobby_members)

            # Notify remaining lobby members
            message_data = {
                'sid': sid,
                'username': 'System',
                'character': 'knight',
                'content': f'{username} disconnected'
            }
            for member_sid in list(lobby_members.keys()):
                socketio.emit('boss_chat_message', message_data, to=member_sid)
                socketio.emit('boss_lobby_player_count', {'playerCount': lobby_count}, to=member_sid)

            print(f"[BOSS] Player {username} ({sid}) disconnected from lobby. Lobby size: {lobby_count}")

        # Clean up PVP arena (uses module-level pvp_sid_mapping)
        if sid in pvp_sid_mapping:
            cleanup_pvp_player(sid)

    # ==================== POWERUP SYSTEM ====================
    import random
    import time
    import uuid

    POWERUP_TYPES = ['damage', 'speed', 'rapidfire', 'heal']
    POWERUP_SPAWN_INTERVAL = 5  # seconds between spawns
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

    # ==================== DEBUG UTILITIES ====================
    @socketio.on('boss_debug_state')
    def handle_debug_state(data):
        """Return current server state for debugging"""
        sid = request.sid
        current_rooms = rooms(sid)
        
        emit('boss_debug_response', {
            'your_sid': sid,
            'your_rooms': list(current_rooms),
            'boss_battles': {
                room_id: {
                    'boss_health': room_data['boss_health'],
                    'player_count': len(room_data['players']),
                    'player_sids': list(room_data['players'].keys()),
                    'player_names': [p['username'] for p in room_data['players'].values()]
                }
                for room_id, room_data in boss_battles.items()
            },
            'lobby_member_count': len(lobby_members),
            'lobby_sids': list(lobby_members.keys()),
            'sid_to_room': dict(sid_to_room)
        })
        print(f"[DEBUG] State requested by {sid}")

    # ==================== GET ONLINE PLAYERS ====================
    @socketio.on('boss_get_players')
    def handle_get_players(data):
        """Get list of online players in a room or lobby"""
        room_id = data.get('room_id')
        sid = request.sid

        if room_id == 'lobby' or room_id == LOBBY_ROOM:
            # Return lobby members
            emit('boss_lobby_members', {
                'members': [{'sid': s, **m} for s, m in lobby_members.items()]
            })
        elif room_id and room_id in boss_battles:
            # Return battle room players
            emit('boss_room_players', {
                'room_id': room_id,
                'players': get_room_players_list(room_id),
                'playerCount': len(boss_battles[room_id]['players'])
            })
        else:
            # Try to find the room the player is in via sid_to_room
            player_room = sid_to_room.get(sid)
            if player_room and player_room in boss_battles:
                emit('boss_room_players', {
                    'room_id': player_room,
                    'players': get_room_players_list(player_room),
                    'playerCount': len(boss_battles[player_room]['players'])
                })
            else:
                emit('boss_room_players', {
                    'room_id': None,
                    'players': [],
                    'playerCount': 0,
                    'error': 'Not in a room'
                })

    # ==================== PVP ARENA HANDLERS ====================
    # PVP state is defined at module level (pvp_room, pvp_sid_mapping, PVP_ROOM_NAME, MAX_PVP_PLAYERS)

    def get_pvp_player_count():
        """Get current number of players in PVP arena"""
        return len(pvp_room['players'])

    def get_pvp_opponent(sid):
        """Get the opponent's data for a given player"""
        for player_sid, player_data in pvp_room['players'].items():
            if player_sid != sid:
                return {'sid': player_sid, **player_data}
        return None

    def broadcast_pvp_status():
        """Broadcast current PVP room status to all connected clients"""
        socketio.emit('pvp_status', {
            'playerCount': get_pvp_player_count(),
            'isFull': get_pvp_player_count() >= MAX_PVP_PLAYERS,
            'battleActive': pvp_room['battle_active']
        })

    @socketio.on('pvp_get_status')
    def handle_pvp_get_status(data):
        """Return current PVP room status"""
        emit('pvp_status', {
            'playerCount': get_pvp_player_count(),
            'isFull': get_pvp_player_count() >= MAX_PVP_PLAYERS,
            'battleActive': pvp_room['battle_active']
        })

    @socketio.on('pvp_join')
    def handle_pvp_join(data):
        """Handle player joining PVP arena"""
        sid = request.sid
        username = data.get('username', 'Guest')
        character = data.get('character', 'knight')
        bullets = data.get('bullets', 0)
        lives = data.get('lives', 3)

        # Check if room is full
        if get_pvp_player_count() >= MAX_PVP_PLAYERS:
            emit('pvp_room_full', {'message': 'PVP arena is full (max 2 players)'})
            return

        # Check if player is already in the room (prevent duplicate joins)
        if sid in pvp_room['players']:
            print(f"[PVP] Player {username} ({sid}) already in arena, sending current state")
            opponent = get_pvp_opponent(sid)
            emit('pvp_room_state', {
                'playerCount': get_pvp_player_count(),
                'playerNumber': pvp_room['players'][sid]['player_number'],
                'opponent': opponent
            })
            return

        # Get opponent BEFORE adding new player (to check if someone is already waiting)
        existing_opponent = get_pvp_opponent(sid)

        # Join the socket room
        join_room(PVP_ROOM_NAME)
        pvp_sid_mapping[sid] = True

        # Determine player number (1 or 2)
        player_number = len(pvp_room['player_order']) + 1

        # Add player to arena
        pvp_room['players'][sid] = {
            'username': username,
            'character': character,
            'bullets': bullets,
            'lives': lives,
            'x': 100 if player_number == 1 else 700,
            'y': 300,
            'player_number': player_number
        }
        pvp_room['player_order'].append(sid)

        # Get opponent again after adding (for the joining player's room state)
        opponent = get_pvp_opponent(sid)

        print(f"[PVP] Player {username} ({sid}) joining. Player number: {player_number}")
        print(f"[PVP] Current players in room: {list(pvp_room['players'].keys())}")
        print(f"[PVP] Opponent found: {opponent}")

        # Send room state to joining player
        emit('pvp_room_state', {
            'playerCount': get_pvp_player_count(),
            'playerNumber': player_number,
            'opponent': opponent
        })

        # Notify the existing opponent directly using their socket ID
        # This is more reliable than room-based emit
        if existing_opponent:
            new_player_data = {
                'username': username,
                'character': character,
                'bullets': bullets,
                'lives': lives
            }
            print(f"[PVP] Notifying opponent {existing_opponent['username']} ({existing_opponent['sid']}) about new player: {new_player_data}")
            # Emit directly to the opponent's socket ID for reliability
            socketio.emit('pvp_opponent_joined', {
                'opponent': new_player_data,
                'playerCount': get_pvp_player_count()
            }, to=existing_opponent['sid'])
            # Also send a full room state snapshot to the existing opponent
            # to avoid clients getting stuck in "waiting" if they miss the join event.
            socketio.emit('pvp_room_state', {
                'playerCount': get_pvp_player_count(),
                'playerNumber': existing_opponent.get('player_number', 1),
                'opponent': new_player_data
            }, to=existing_opponent['sid'])

        # Broadcast status update to all clients
        broadcast_pvp_status()

        print(f"[PVP] Player {username} ({sid}) joined arena. Total players: {get_pvp_player_count()}")

    @socketio.on('pvp_ready')
    def handle_pvp_ready(data):
        """Handle player ready status"""
        sid = request.sid

        if sid not in pvp_room['players']:
            return

        pvp_room['ready_players'].add(sid)

        # Check if both players are ready
        if len(pvp_room['ready_players']) >= 2 and get_pvp_player_count() >= 2:
            pvp_room['battle_active'] = True
            emit('pvp_battle_start', {}, room=PVP_ROOM_NAME)
            print(f"[PVP] Battle starting!")

    @socketio.on('pvp_move')
    def handle_pvp_move(data):
        """Handle player position update"""
        sid = request.sid
        x = data.get('x')
        y = data.get('y')

        if sid not in pvp_room['players']:
            return

        pvp_room['players'][sid]['x'] = x
        pvp_room['players'][sid]['y'] = y

        # Broadcast to opponent
        emit('pvp_opponent_position', {
            'x': x,
            'y': y
        }, room=PVP_ROOM_NAME, include_self=False)

    @socketio.on('pvp_shoot')
    def handle_pvp_shoot(data):
        """Handle player shooting"""
        sid = request.sid

        if sid not in pvp_room['players']:
            return

        # Broadcast shot to opponent
        emit('pvp_opponent_shot', {
            'bulletX': data.get('bulletX'),
            'bulletY': data.get('bulletY'),
            'dx': data.get('dx'),
            'dy': data.get('dy'),
            'character': data.get('character')
        }, room=PVP_ROOM_NAME, include_self=False)

    @socketio.on('pvp_hit_opponent')
    def handle_pvp_hit_opponent(data):
        """Handle player hitting opponent"""
        sid = request.sid

        if sid not in pvp_room['players']:
            return

        # Find opponent
        opponent = get_pvp_opponent(sid)
        if not opponent:
            return

        opponent_sid = opponent['sid']
        if opponent_sid not in pvp_room['players']:
            return

        # Reduce opponent lives
        pvp_room['players'][opponent_sid]['lives'] -= 1
        new_lives = pvp_room['players'][opponent_sid]['lives']

        # Broadcast hit to both players
        emit('pvp_player_hit', {
            'target': opponent_sid,
            'lives': new_lives
        }, room=PVP_ROOM_NAME)

        print(f"[PVP] Player hit! Opponent lives: {new_lives}")

        # Check if opponent died
        if new_lives <= 0:
            # Battle over - attacker wins
            pvp_room['battle_active'] = False
            pvp_room['ready_players'].clear()
            print(f"[PVP] Battle over! Player {pvp_room['players'][sid]['username']} wins!")

    @socketio.on('pvp_stats_update')
    def handle_pvp_stats_update(data):
        """Handle player stats update"""
        sid = request.sid

        if sid not in pvp_room['players']:
            return

        if data.get('bullets') is not None:
            pvp_room['players'][sid]['bullets'] = data.get('bullets')
        if data.get('lives') is not None:
            pvp_room['players'][sid]['lives'] = data.get('lives')

        # Broadcast to opponent
        emit('pvp_opponent_stats', {
            'bullets': pvp_room['players'][sid]['bullets'],
            'lives': pvp_room['players'][sid]['lives']
        }, room=PVP_ROOM_NAME, include_self=False)

    @socketio.on('pvp_chat_send')
    def handle_pvp_chat_send(data):
        """Handle chat message in PVP arena"""
        sid = request.sid
        content = data.get('content', '')
        username = data.get('username', 'Anonymous')
        character = data.get('character', 'knight')

        if not content:
            return

        # Sanitize content
        content = content[:280]

        emit('pvp_chat_message', {
            'username': username,
            'character': character,
            'content': content
        }, room=PVP_ROOM_NAME, include_self=False)

    @socketio.on('pvp_player_away')
    def handle_pvp_player_away(data):
        """Handle player switching away"""
        username = data.get('username', 'Unknown')
        emit('pvp_player_away', {'username': username}, room=PVP_ROOM_NAME, include_self=False)

    @socketio.on('pvp_player_returned')
    def handle_pvp_player_returned(data):
        """Handle player returning"""
        username = data.get('username', 'Unknown')
        emit('pvp_player_returned', {'username': username}, room=PVP_ROOM_NAME, include_self=False)

    @socketio.on('pvp_leave')
    def handle_pvp_leave(data):
        """Handle player leaving PVP arena"""
        sid = request.sid
        cleanup_pvp_player(sid)

    def cleanup_pvp_player(sid):
        """Clean up a player from PVP arena"""
        if sid not in pvp_room['players']:
            return

        player = pvp_room['players'][sid]
        username = player['username']

        # Remove from room
        del pvp_room['players'][sid]
        if sid in pvp_room['player_order']:
            pvp_room['player_order'].remove(sid)
        pvp_room['ready_players'].discard(sid)

        # Clean up mapping
        if sid in pvp_sid_mapping:
            del pvp_sid_mapping[sid]

        leave_room(PVP_ROOM_NAME)

        player_count = get_pvp_player_count()

        # Notify opponent
        emit('pvp_opponent_left', {
            'username': username
        }, room=PVP_ROOM_NAME)

        # Reset battle state if battle was active
        if pvp_room['battle_active']:
            pvp_room['battle_active'] = False
            pvp_room['ready_players'].clear()

        # Broadcast status update
        broadcast_pvp_status()

        print(f"[PVP] Player {username} ({sid}) left arena. Remaining: {player_count}")
