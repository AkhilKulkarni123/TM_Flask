from flask_socketio import SocketIO, emit, join_room, leave_room, rooms
from flask import request
from flask_login import current_user
import json
import math
import random
import threading

# Boss battle state - keyed by room_id
boss_battles = {}  # { room_id: { boss_health, max_health, players: { sid: player_data } } }

# Map socket session IDs to room IDs for cleanup on disconnect
sid_to_room = {}  # { sid: room_id }

# Lobby members for pre-battle chat - { sid: { username, character } }
lobby_members = {}

# Shared lobby room name (moved to module level for proper scope)
LOBBY_ROOM = 'boss_lobby'

MAX_PLAYERS_PER_ROOM = 10

# PVP Arena state - multiple rooms, max 2 players per room
pvp_rooms = {}  # { room_id: { players, player_order, battle_active, ready_players } }
pvp_sid_mapping = {}  # { sid: room_id } - tracks which PVP room a player is in
PVP_ROOM_PREFIX = 'pvp_arena'
MAX_PVP_PLAYERS = 2
pvp_room_counter = 1

# King of the Zone state - multiple rooms
koz_rooms = {}  # { room_id: { players, team_scores, zone, timers, ... } }
koz_sid_mapping = {}  # { sid: room_id }
KOZ_ROOM_PREFIX = 'koz_arena'
KOZ_MAX_PLAYERS = 12
koz_room_counter = 1

# Multiplayer collision tuning (server authoritative)
# PVP sprite renders ~60px wide, so radius ~28-30 keeps collisions fair.
PVP_PLAYER_RADIUS = 28
# KOZ uses simple 12px circles client-side; 16px radius gives a forgiving buffer.
KOZ_PLAYER_RADIUS = 16
# Boss battle uses 70px boss + 35px player size on client canvas.
BOSS_PLAYER_RADIUS = 35
BOSS_BOSS_RADIUS = 70
BOSS_DEFAULT_WIDTH = 1100
BOSS_DEFAULT_HEIGHT = 600
BOSS_TOP_MARGIN = 200
BOSS_SPAWN_ATTEMPTS = 80
BOSS_SPAWN_PADDING = 24
BOSS_SPAWN_GRID_STEP = 40

# Protects spawn allocation for concurrent joins
boss_spawn_lock = threading.Lock()

def init_boss_battle_socket(socketio):
    def normalize_boss_bounds(bounds):
        width = bounds.get('width', BOSS_DEFAULT_WIDTH) if isinstance(bounds, dict) else BOSS_DEFAULT_WIDTH
        height = bounds.get('height', BOSS_DEFAULT_HEIGHT) if isinstance(bounds, dict) else BOSS_DEFAULT_HEIGHT
        top = bounds.get('top', BOSS_TOP_MARGIN) if isinstance(bounds, dict) else BOSS_TOP_MARGIN
        try:
            width = int(width)
            height = int(height)
            top = int(top)
        except (TypeError, ValueError):
            width = BOSS_DEFAULT_WIDTH
            height = BOSS_DEFAULT_HEIGHT
            top = BOSS_TOP_MARGIN
        width = max(480, width)
        height = max(360, height)
        top = max(0, min(top, height - 120))
        return {'width': width, 'height': height, 'top': top}

    def ensure_boss_bounds(room_id, incoming_bounds=None):
        if room_id not in boss_battles:
            return normalize_boss_bounds(incoming_bounds or {})
        if 'bounds' not in boss_battles[room_id]:
            boss_battles[room_id]['bounds'] = normalize_boss_bounds(incoming_bounds or {})
        return boss_battles[room_id]['bounds']

    def is_spawn_clear(room_id, x, y, radius, bounds):
        # Avoid overlapping existing players
        for other in boss_battles.get(room_id, {}).get('players', {}).values():
            ox = other.get('x', x)
            oy = other.get('y', y)
            if math.hypot(x - ox, y - oy) < (radius * 2 + 6):
                return False
        # Avoid the boss zone near the top of the map
        boss_x = bounds['width'] / 2
        boss_y = 110
        if math.hypot(x - boss_x, y - boss_y) < (BOSS_BOSS_RADIUS + radius + 12):
            return False
        return True

    def allocate_boss_spawn(room_id, radius, bounds):
        min_x = radius + BOSS_SPAWN_PADDING
        max_x = bounds['width'] - radius - BOSS_SPAWN_PADDING
        min_y = max(bounds['top'] + radius, 260)
        max_y = bounds['height'] - radius - BOSS_SPAWN_PADDING
        if max_x <= min_x or max_y <= min_y:
            return None
        for _ in range(BOSS_SPAWN_ATTEMPTS):
            x = random.uniform(min_x, max_x)
            y = random.uniform(min_y, max_y)
            if is_spawn_clear(room_id, x, y, radius, bounds):
                return x, y
        step = max(radius * 2, BOSS_SPAWN_GRID_STEP)
        y = min_y
        while y <= max_y:
            x = min_x
            while x <= max_x:
                if is_spawn_clear(room_id, x, y, radius, bounds):
                    return x, y
                x += step
            y += step
        return None

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
        incoming_bounds = data.get('bounds')
        boss_health = data.get('boss_health', 1000)
        max_boss_health = data.get('max_boss_health', 1000)

        sid = request.sid
        username = player_data.get('username', 'Guest')
        user_id = player_data.get('user_id', sid)
        character = player_data.get('character', 'knight')
        bullets = player_data.get('bullets', 0)
        lives = player_data.get('lives', 5)
        x = player_data.get('x', 400)
        y = player_data.get('y', 500)

        # Initialize room if it doesn't exist
        if room_id not in boss_battles:
            boss_battles[room_id] = {
                'boss_health': boss_health,
                'max_health': max_boss_health,
                'players': {}
            }
        room_bounds = ensure_boss_bounds(room_id, incoming_bounds)

        # Check if room is full
        if len(boss_battles[room_id]['players']) >= MAX_PLAYERS_PER_ROOM:
            emit('boss_room_full', {'message': 'Room is full (max 10 players)'})
            return

        # Join the socket room
        join_room(room_id)
        sid_to_room[sid] = room_id

        # Add player to battle (server assigns safe spawn)
        with boss_spawn_lock:
            spawn = allocate_boss_spawn(room_id, BOSS_PLAYER_RADIUS, room_bounds)
            if spawn:
                x, y = spawn
            else:
                # Fallback to clamped requested position if needed
                x = max(BOSS_PLAYER_RADIUS, min(x, room_bounds['width'] - BOSS_PLAYER_RADIUS))
                y = max(room_bounds['top'] + BOSS_PLAYER_RADIUS, min(y, room_bounds['height'] - BOSS_PLAYER_RADIUS))

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
            'powerups': room_powerups,
            'self': {
                'x': x,
                'y': y,
                'bullets': bullets,
                'lives': lives
            },
            'bounds': room_bounds
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
        boss_x = data.get('boss_x')
        boss_y = data.get('boss_y')
        sid = request.sid

        if not room_id or room_id not in boss_battles:
            return

        if sid not in boss_battles[room_id]['players']:
            return

        if x is None or y is None:
            return

        # Server-authoritative bounds + collision resolution
        room_bounds = boss_battles[room_id].get('bounds') or normalize_boss_bounds({})
        desired_x = max(BOSS_PLAYER_RADIUS, min(x, room_bounds['width'] - BOSS_PLAYER_RADIUS))
        desired_y = max(room_bounds['top'] + BOSS_PLAYER_RADIUS, min(y, room_bounds['height'] - BOSS_PLAYER_RADIUS))

        # Resolve boss collision if boss coords provided
        if boss_x is not None and boss_y is not None:
            try:
                boss_x = float(boss_x)
                boss_y = float(boss_y)
                desired_x, desired_y, _ = resolve_player_collision(
                    desired_x, desired_y, boss_x, boss_y, BOSS_BOSS_RADIUS + BOSS_PLAYER_RADIUS
                )
            except (TypeError, ValueError):
                pass

        # Resolve player ↔ player collisions
        min_dist = BOSS_PLAYER_RADIUS * 2
        for other_sid, other in boss_battles[room_id]['players'].items():
            if other_sid == sid:
                continue
            desired_x, desired_y, _ = resolve_player_collision(
                desired_x, desired_y, other.get('x', desired_x), other.get('y', desired_y), min_dist
            )

        # Final clamp to bounds after collision resolution
        desired_x = max(BOSS_PLAYER_RADIUS, min(desired_x, room_bounds['width'] - BOSS_PLAYER_RADIUS))
        desired_y = max(room_bounds['top'] + BOSS_PLAYER_RADIUS, min(desired_y, room_bounds['height'] - BOSS_PLAYER_RADIUS))

        # Update stored position
        boss_battles[room_id]['players'][sid]['x'] = desired_x
        boss_battles[room_id]['players'][sid]['y'] = desired_y

        # Broadcast position to all OTHER players in the room
        emit('boss_player_position', {
            'sid': sid,
            'x': desired_x,
            'y': desired_y
        }, room=room_id, include_self=False)

        # Authoritative position for the mover
        emit('boss_self_position', {
            'x': desired_x,
            'y': desired_y
        }, to=sid)

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
        sid = request.sid

        if room_id and room_id in boss_battles:
            # Track bullets fired for this player
            if sid in boss_battles[room_id]['players']:
                player = boss_battles[room_id]['players'][sid]
                if 'bullets_fired' not in player:
                    player['bullets_fired'] = 0
                player['bullets_fired'] += 1

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

        # Track damage dealt and bullets hit by this player
        if 'damage_dealt' not in player:
            player['damage_dealt'] = 0
        player['damage_dealt'] += damage

        if 'bullets_hit' not in player:
            player['bullets_hit'] = 0
        player['bullets_hit'] += 1

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
            # Collect all players' stats for the victory screen
            all_player_stats = []
            for player_sid, player_data in boss_battles[room_id]['players'].items():
                all_player_stats.append({
                    'sid': player_sid,
                    'username': player_data.get('username', 'Unknown'),
                    'character': player_data.get('character', 'knight'),
                    'damage_dealt': player_data.get('damage_dealt', 0),
                    'lives': player_data.get('lives', 0),
                    'bullets_used': player_data.get('bullets_used', 0),
                    'powerups_collected': player_data.get('powerups_collected', [])
                })

            emit('boss_defeated', {
                'message': 'The boss has been defeated!',
                'players': get_room_players_list(room_id),
                'all_player_stats': all_player_stats
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

        # Track lives lost
        if 'lives_lost' not in player:
            player['lives_lost'] = 0
        player['lives_lost'] += 1

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

        # Clean up King of the Zone arena
        if sid in koz_sid_mapping:
            cleanup_koz_player(sid)

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
        sid = request.sid

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

        # Track powerup collection for this player
        if sid in boss_battles[room_id]['players']:
            player = boss_battles[room_id]['players'][sid]
            if 'powerups_collected' not in player:
                player['powerups_collected'] = []
            if powerup_type:
                player['powerups_collected'].append(powerup_type)

        # Notify all players that powerup was collected
        emit('boss_powerup_collected', {
            'powerup_id': powerup_id,
            'type': powerup_type,
            'username': username,
            'collector_sid': request.sid
        }, room=room_id)

        print(f"[BOSS] Player {username} collected powerup {powerup_id} ({powerup_type}) in room {room_id}")

    @socketio.on('boss_report_stats')
    def handle_report_stats(data):
        """Handle player reporting their battle stats"""
        room_id = data.get('room_id')
        sid = request.sid

        if not room_id or room_id not in boss_battles:
            return

        if sid not in boss_battles[room_id]['players']:
            return

        player = boss_battles[room_id]['players'][sid]

        # Update player stats from client report
        if 'bullets_fired' in data:
            player['bullets_fired'] = data['bullets_fired']
        if 'bullets_hit' in data:
            player['bullets_hit'] = data['bullets_hit']
        if 'lives_lost' in data:
            player['lives_lost'] = data['lives_lost']
        if 'damage_dealt' in data:
            player['damage_dealt'] = data['damage_dealt']
        if 'powerups_collected' in data:
            player['powerups_collected'] = data['powerups_collected']

        print(f"[BOSS] Player {player.get('username')} reported stats: bullets_fired={data.get('bullets_fired')}, damage={data.get('damage_dealt')}")

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
                'lives': 5,
                'x': 400,
                'y': 500
            },
            'boss_health': 1000,
            'max_boss_health': 1000
        }
        if data.get('bounds'):
            new_data['bounds'] = data.get('bounds')
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
    # PVP state is defined at module level (pvp_rooms, pvp_sid_mapping, PVP_ROOM_PREFIX, MAX_PVP_PLAYERS)

    def get_pvp_room_name(room_id):
        return f"{PVP_ROOM_PREFIX}_{room_id}"

    def create_pvp_room():
        global pvp_room_counter
        room_id = str(pvp_room_counter)
        pvp_room_counter += 1
        pvp_rooms[room_id] = {
            'players': {},
            'player_order': [],
            'battle_active': False,
            'ready_players': set()
        }
        return room_id

    def get_or_create_open_room():
        for room_id, room in pvp_rooms.items():
            if len(room['players']) < MAX_PVP_PLAYERS:
                return room_id, room
        room_id = create_pvp_room()
        return room_id, pvp_rooms[room_id]

    def get_room_by_sid(sid):
        room_id = pvp_sid_mapping.get(sid)
        if room_id and room_id in pvp_rooms:
            return room_id, pvp_rooms[room_id]
        return None, None

    def get_pvp_player_count(room):
        return len(room['players'])

    def get_pvp_opponent(room, sid):
        for player_sid, player_data in room['players'].items():
            if player_sid != sid:
                return {'sid': player_sid, **player_data}
        return None

    def resolve_player_collision(desired_x, desired_y, other_x, other_y, min_dist):
        """Return adjusted position so players do not overlap. Only moves the caller."""
        dx = desired_x - other_x
        dy = desired_y - other_y
        dist = math.sqrt(dx * dx + dy * dy)
        if dist < 0.001:
            # Avoid divide-by-zero; push along x-axis
            return other_x + min_dist, desired_y, True
        if dist >= min_dist:
            return desired_x, desired_y, False
        overlap = min_dist - dist
        nx = dx / dist
        ny = dy / dist
        return desired_x + nx * overlap, desired_y + ny * overlap, True

    def get_pvp_aggregate_status():
        total_players = sum(len(room['players']) for room in pvp_rooms.values())
        active_rooms = len([room for room in pvp_rooms.values() if room['players']])
        open_slots = sum(MAX_PVP_PLAYERS - len(room['players']) for room in pvp_rooms.values())
        if open_slots == 0:
            open_slots = MAX_PVP_PLAYERS  # room can be created on demand
        return {
            'totalPlayers': total_players,
            'activeRooms': active_rooms,
            'openSlots': open_slots
        }

    def broadcast_pvp_status():
        socketio.emit('pvp_status', get_pvp_aggregate_status())

    @socketio.on('pvp_get_status')
    def handle_pvp_get_status(data):
        """Return aggregate PVP status (mode selection) and room status if applicable"""
        room_id, room = get_room_by_sid(request.sid)
        if room:
            emit('pvp_room_status', {
                'roomId': room_id,
                'playerCount': get_pvp_player_count(room),
                'battleActive': room['battle_active']
            })
        emit('pvp_status', get_pvp_aggregate_status())

    @socketio.on('pvp_join')
    def handle_pvp_join(data):
        """Handle player joining PVP arena (assigns an open room or creates a new one)"""
        sid = request.sid
        username = data.get('username', 'Guest')
        character = data.get('character', 'knight')
        bullets = data.get('bullets', 0)
        lives = data.get('lives', 5)

        # If already in a room, just resend state
        existing_room_id, existing_room = get_room_by_sid(sid)
        if existing_room:
            opponent = get_pvp_opponent(existing_room, sid)
            emit('pvp_room_state', {
                'roomId': existing_room_id,
                'playerCount': get_pvp_player_count(existing_room),
                'playerNumber': existing_room['players'][sid]['player_number'],
                'opponent': opponent
            })
            emit('pvp_room_status', {
                'roomId': existing_room_id,
                'playerCount': get_pvp_player_count(existing_room),
                'battleActive': existing_room['battle_active']
            })
            return

        room_id, room = get_or_create_open_room()
        room_name = get_pvp_room_name(room_id)

        # Capture existing opponent before adding player
        existing_opponent = None
        existing_opponent_sid = None
        for player_sid, player_data in room['players'].items():
            existing_opponent = {'sid': player_sid, **player_data}
            existing_opponent_sid = player_sid
            break

        join_room(room_name)
        pvp_sid_mapping[sid] = room_id

        player_number = len(room['player_order']) + 1
        room['players'][sid] = {
            'username': username,
            'character': character,
            'bullets': bullets,
            'lives': lives,
            'x': 100 if player_number == 1 else 700,
            'y': 300,
            'player_number': player_number
        }
        room['player_order'].append(sid)

        opponent = get_pvp_opponent(room, sid)

        emit('pvp_room_state', {
            'roomId': room_id,
            'playerCount': get_pvp_player_count(room),
            'playerNumber': player_number,
            'opponent': opponent
        })
        emit('pvp_room_status', {
            'roomId': room_id,
            'playerCount': get_pvp_player_count(room),
            'battleActive': room['battle_active']
        })

        if existing_opponent and existing_opponent_sid:
            new_player_data = {
                'username': username,
                'character': character,
                'bullets': bullets,
                'lives': lives,
                'player_number': player_number
            }
            socketio.emit('pvp_opponent_joined', {
                'opponent': new_player_data,
                'playerCount': get_pvp_player_count(room)
            }, to=existing_opponent_sid)

            socketio.emit('pvp_room_state', {
                'roomId': room_id,
                'playerCount': get_pvp_player_count(room),
                'playerNumber': existing_opponent.get('player_number', 1),
                'opponent': new_player_data
            }, to=existing_opponent_sid)

            socketio.emit('pvp_match_ready', {
                'message': 'Both players are in the arena!',
                'playerCount': get_pvp_player_count(room),
                'player1': room['players'].get(room['player_order'][0]) if len(room['player_order']) > 0 else None,
                'player2': room['players'].get(room['player_order'][1]) if len(room['player_order']) > 1 else None
            }, room=room_name)

            if get_pvp_player_count(room) >= 2 and not room['battle_active']:
                room['battle_active'] = True
                socketio.emit('pvp_battle_start', {
                    'message': 'Battle starting!',
                    'player1': room['players'].get(room['player_order'][0]) if len(room['player_order']) > 0 else None,
                    'player2': room['players'].get(room['player_order'][1]) if len(room['player_order']) > 1 else None
                }, room=room_name)

        broadcast_pvp_status()

    @socketio.on('pvp_ready')
    def handle_pvp_ready(data):
        sid = request.sid
        room_id, room = get_room_by_sid(sid)
        if not room or sid not in room['players']:
            return
        room['ready_players'].add(sid)
        if len(room['ready_players']) >= 2 and get_pvp_player_count(room) >= 2:
            room['battle_active'] = True
            emit('pvp_battle_start', {}, room=get_pvp_room_name(room_id))

    @socketio.on('pvp_move')
    def handle_pvp_move(data):
        sid = request.sid
        room_id, room = get_room_by_sid(sid)
        if not room or sid not in room['players']:
            return
        desired_x = data.get('x')
        desired_y = data.get('y')
        if desired_x is None or desired_y is None:
            return

        corrected = False
        min_dist = PVP_PLAYER_RADIUS * 2
        for other_sid, other in room['players'].items():
            if other_sid == sid:
                continue
            desired_x, desired_y, did_correct = resolve_player_collision(
                desired_x, desired_y, other.get('x', desired_x), other.get('y', desired_y), min_dist
            )
            corrected = corrected or did_correct

        room['players'][sid]['x'] = desired_x
        room['players'][sid]['y'] = desired_y
        emit('pvp_opponent_position', {
            'x': desired_x,
            'y': desired_y
        }, room=get_pvp_room_name(room_id), include_self=False)

        # Authoritative position for the mover (prevents overlap/jitter)
        emit('pvp_self_position', {
            'x': desired_x,
            'y': desired_y
        }, to=sid)

    @socketio.on('pvp_shoot')
    def handle_pvp_shoot(data):
        sid = request.sid
        room_id, room = get_room_by_sid(sid)
        if not room or sid not in room['players']:
            return
        emit('pvp_opponent_shot', {
            'bulletX': data.get('bulletX'),
            'bulletY': data.get('bulletY'),
            'dx': data.get('dx'),
            'dy': data.get('dy'),
            'character': data.get('character')
        }, room=get_pvp_room_name(room_id), include_self=False)

    @socketio.on('pvp_hit_opponent')
    def handle_pvp_hit_opponent(data):
        sid = request.sid
        room_id, room = get_room_by_sid(sid)
        if not room or sid not in room['players']:
            return
        opponent = get_pvp_opponent(room, sid)
        if not opponent:
            return
        opponent_sid = opponent['sid']
        if opponent_sid not in room['players']:
            return
        room['players'][opponent_sid]['lives'] -= 1
        new_lives = room['players'][opponent_sid]['lives']
        emit('pvp_player_hit', {
            'target': opponent_sid,
            'lives': new_lives
        }, room=get_pvp_room_name(room_id))
        if new_lives <= 0:
            room['battle_active'] = False
            room['ready_players'].clear()

    @socketio.on('pvp_stats_update')
    def handle_pvp_stats_update(data):
        sid = request.sid
        room_id, room = get_room_by_sid(sid)
        if not room or sid not in room['players']:
            return
        if data.get('bullets') is not None:
            room['players'][sid]['bullets'] = data.get('bullets')
        if data.get('lives') is not None:
            room['players'][sid]['lives'] = data.get('lives')
        emit('pvp_opponent_stats', {
            'bullets': room['players'][sid]['bullets'],
            'lives': room['players'][sid]['lives']
        }, room=get_pvp_room_name(room_id), include_self=False)

    @socketio.on('pvp_chat_send')
    def handle_pvp_chat_send(data):
        sid = request.sid
        room_id, room = get_room_by_sid(sid)
        if not room:
            return
        content = (data.get('content', '') or '')[:280]
        username = data.get('username', 'Anonymous')
        character = data.get('character', 'knight')
        if not content:
            return
        emit('pvp_chat_message', {
            'username': username,
            'character': character,
            'content': content
        }, room=get_pvp_room_name(room_id), include_self=False)

    @socketio.on('pvp_player_away')
    def handle_pvp_player_away(data):
        sid = request.sid
        room_id, room = get_room_by_sid(sid)
        if not room:
            return
        username = data.get('username', 'Unknown')
        emit('pvp_player_away', {'username': username}, room=get_pvp_room_name(room_id), include_self=False)

    @socketio.on('pvp_player_returned')
    def handle_pvp_player_returned(data):
        sid = request.sid
        room_id, room = get_room_by_sid(sid)
        if not room:
            return
        username = data.get('username', 'Unknown')
        emit('pvp_player_returned', {'username': username}, room=get_pvp_room_name(room_id), include_self=False)

    @socketio.on('pvp_leave')
    def handle_pvp_leave(data):
        sid = request.sid
        cleanup_pvp_player(sid)

    def cleanup_pvp_player(sid):
        room_id, room = get_room_by_sid(sid)
        if not room or sid not in room['players']:
            return
        username = room['players'][sid]['username']
        del room['players'][sid]
        if sid in room['player_order']:
            room['player_order'].remove(sid)
        room['ready_players'].discard(sid)
        if sid in pvp_sid_mapping:
            del pvp_sid_mapping[sid]
        leave_room(get_pvp_room_name(room_id))
        emit('pvp_opponent_left', {
            'username': username
        }, room=get_pvp_room_name(room_id))
        if room['battle_active']:
            room['battle_active'] = False
            room['ready_players'].clear()
        if len(room['players']) == 0:
            del pvp_rooms[room_id]
        broadcast_pvp_status()

    # ==================== KING OF THE ZONE HANDLERS ====================
    # Server-authoritative mode:
    # - Zone center/radius, shrink cadence, projectiles, obstacle collision and powerups are owned by server.
    # - Clients send intent (move + shoot aim), server validates and broadcasts deltas.
    import time

    KOZ_TARGET_SCORE = 220
    KOZ_TIME_LIMIT = 300  # seconds
    KOZ_SCORE_PER_SEC = 4
    KOZ_CORE_BONUS_PER_SEC = 3
    KOZ_SHRINK_INTERVAL = 16  # seconds between shrink phases
    KOZ_SHRINK_DURATION = 6   # seconds to animate each shrink
    KOZ_SHRINK_STEP = 220
    KOZ_MIN_RADIUS = 760
    KOZ_ZONE_START_RADIUS = 3400
    KOZ_MAP_WIDTH = 9800
    KOZ_MAP_HEIGHT = 7600
    KOZ_CONTESTED_RELOCATE_SECONDS = 18
    KOZ_DRIFT_SPEED = 22
    KOZ_STORM_MAX_HP = 100
    KOZ_STORM_DAMAGE = 9
    KOZ_STORM_REGEN = 5
    KOZ_STORM_FINAL_MULT = 1.7
    KOZ_PULSE_INTERVAL = 14
    KOZ_PULSE_PULL = 52
    KOZ_RESPAWN_PENALTY = 12
    KOZ_BASE_SPEED = 150  # pixels per second
    KOZ_COMBAT_MAX_HP = 100
    KOZ_KILL_SCORE = 16
    KOZ_DEATH_PENALTY = 8
    KOZ_TICK_RATE = 0.05  # 20Hz
    KOZ_SCORE_TICK = 1.0
    KOZ_STATE_BROADCAST_INTERVAL = 0.2
    KOZ_POWERUP_MAX = 10
    KOZ_POWERUP_RESPAWN_DELAY = 4
    KOZ_POWERUP_RADIUS = 18
    KOZ_OBSTACLE_COUNT = 26
    KOZ_OBSTACLE_MIN_RADIUS = 44
    KOZ_OBSTACLE_MAX_RADIUS = 95

    KOZ_HERO_DEFAULT_WEAPON = {
        'knight': 'bulwark-disc',
        'wizard': 'arcane-orb',
        'archer': 'piercing-arrow',
        'warrior': 'rage-axe'
    }

    KOZ_WEAPON_CONFIG = {
        'bulwark-disc': {
            'speed': 980.0,
            'radius': 10,
            'damage': 18,
            'lifetime': 1.8,
            'cooldown': 0.46,
            'spread': [0.0],
            'pierce': 0,
            'bounces': 1,
            'splash': 0,
            'color': '#8ed7ff'
        },
        'arcane-orb': {
            'speed': 840.0,
            'radius': 11,
            'damage': 20,
            'lifetime': 1.65,
            'cooldown': 0.58,
            'spread': [-0.08, 0.08],
            'pierce': 0,
            'bounces': 0,
            'splash': 74,
            'color': '#ff9f5a'
        },
        'piercing-arrow': {
            'speed': 1220.0,
            'radius': 7,
            'damage': 16,
            'lifetime': 1.9,
            'cooldown': 0.34,
            'spread': [0.0],
            'pierce': 1,
            'bounces': 0,
            'splash': 0,
            'color': '#8ef7cc'
        },
        'rage-axe': {
            'speed': 900.0,
            'radius': 12,
            'damage': 24,
            'lifetime': 1.6,
            'cooldown': 0.62,
            'spread': [0.0],
            'pierce': 0,
            'bounces': 0,
            'splash': 34,
            'color': '#ffc46b'
        }
    }

    KOZ_POWERUP_CONFIG = {
        'speed-boost': {'label': 'Speed Boost', 'duration': 7.0},
        'shield': {'label': 'Shield', 'amount': 35},
        'rapid-fire': {'label': 'Rapid Fire', 'duration': 6.0},
        'heal': {'label': 'Heal', 'amount': 30},
        'vision-ping': {'label': 'Vision Ping', 'duration': 3.5},
        'ammo-pack': {'label': 'Ammo Pack', 'amount': 12}
    }

    def clamp(val, minv, maxv):
        return max(minv, min(maxv, val))

    def pick_zone_center(radius):
        # KOZ ring center is stable in world space so the arena feels massive.
        return (KOZ_MAP_WIDTH / 2.0, KOZ_MAP_HEIGHT / 2.0)

    def get_unique_display_name(room, base_name):
        base = (base_name or 'Player').strip() or 'Player'
        existing = set(room.get('score_labels', {}).values())
        if base not in existing:
            return base
        idx = 2
        while f"{base} {idx}" in existing:
            idx += 1
        return f"{base} {idx}"

    def get_koz_room_name(room_id):
        return f"{KOZ_ROOM_PREFIX}_{room_id}"

    def resolve_weapon_type(character, explicit):
        maybe = str(explicit or '').strip().lower()
        if maybe in KOZ_WEAPON_CONFIG:
            return maybe
        hero = str(character or 'knight').strip().lower()
        return KOZ_HERO_DEFAULT_WEAPON.get(hero, 'bulwark-disc')

    def get_weapon_cfg(player):
        return KOZ_WEAPON_CONFIG.get(player.get('weapon_type'), KOZ_WEAPON_CONFIG['bulwark-disc'])

    def circle_hits_obstacle(x, y, radius, obstacle):
        dx = x - obstacle['x']
        dy = y - obstacle['y']
        r = radius + obstacle['radius']
        return (dx * dx + dy * dy) <= (r * r)

    def resolve_circle_obstacle(x, y, radius, obstacle):
        dx = x - obstacle['x']
        dy = y - obstacle['y']
        dist = math.sqrt(dx * dx + dy * dy)
        min_dist = radius + obstacle['radius']
        if dist < 0.001:
            return obstacle['x'] + min_dist, y, True
        if dist >= min_dist:
            return x, y, False
        overlap = min_dist - dist
        nx = dx / dist
        ny = dy / dist
        return x + nx * overlap, y + ny * overlap, True

    def is_spawn_clear(room, x, y, radius):
        for obstacle in room.get('obstacles', {}).values():
            if circle_hits_obstacle(x, y, radius, obstacle):
                return False
        for other in room.get('players', {}).values():
            ox = other.get('x', x)
            oy = other.get('y', y)
            if math.hypot(x - ox, y - oy) < (radius + KOZ_PLAYER_RADIUS + 20):
                return False
        return True

    def find_spawn_point(room, prefer_zone_ring=False):
        zone = room['zone']
        for _ in range(90):
            if prefer_zone_ring:
                angle = random.random() * math.pi * 2
                radius = random.uniform(max(KOZ_MIN_RADIUS * 0.85, zone['radius'] * 0.72), zone['radius'] * 1.02)
                x = zone['x'] + math.cos(angle) * radius
                y = zone['y'] + math.sin(angle) * radius
            else:
                x = random.uniform(KOZ_PLAYER_RADIUS + 30, KOZ_MAP_WIDTH - KOZ_PLAYER_RADIUS - 30)
                y = random.uniform(KOZ_PLAYER_RADIUS + 30, KOZ_MAP_HEIGHT - KOZ_PLAYER_RADIUS - 30)
            x = clamp(x, KOZ_PLAYER_RADIUS + 5, KOZ_MAP_WIDTH - KOZ_PLAYER_RADIUS - 5)
            y = clamp(y, KOZ_PLAYER_RADIUS + 5, KOZ_MAP_HEIGHT - KOZ_PLAYER_RADIUS - 5)
            if is_spawn_clear(room, x, y, KOZ_PLAYER_RADIUS):
                return x, y
        return (
            clamp(zone['x'], KOZ_PLAYER_RADIUS + 5, KOZ_MAP_WIDTH - KOZ_PLAYER_RADIUS - 5),
            clamp(zone['y'], KOZ_PLAYER_RADIUS + 5, KOZ_MAP_HEIGHT - KOZ_PLAYER_RADIUS - 5)
        )

    def serialize_obstacles(room):
        return [
            {
                'id': obs['id'],
                'x': obs['x'],
                'y': obs['y'],
                'radius': obs['radius'],
                'type': obs['type'],
                'destructible': bool(obs.get('destructible')),
                'hp': obs.get('hp', 0)
            }
            for obs in room.get('obstacles', {}).values()
        ]

    def serialize_powerups(room):
        return [
            {
                'id': p['id'],
                'type': p['type'],
                'x': p['x'],
                'y': p['y'],
                'radius': p.get('radius', KOZ_POWERUP_RADIUS),
                'spawnedAt': p.get('spawned_at', time.time())
            }
            for p in room.get('powerups', {}).values()
        ]

    def serialize_players(room):
        payload = []
        for sid, player in room.get('players', {}).items():
            payload.append({
                'sid': sid,
                'x': player.get('x', 0),
                'y': player.get('y', 0),
                'character': player.get('character', 'knight'),
                'weapon_type': player.get('weapon_type'),
                'username': player.get('username', 'Player'),
                'hp': player.get('combat_hp', KOZ_COMBAT_MAX_HP)
            })
        return payload

    def get_next_shrink_seconds(room, now):
        shrink = room['shrink']
        if shrink.get('active'):
            return 0
        return max(0.0, shrink.get('next_at', now) - now)

    def spawn_powerup(room, forced_type=None):
        if len(room['powerups']) >= KOZ_POWERUP_MAX:
            return None
        available_types = list(KOZ_POWERUP_CONFIG.keys())
        ptype = forced_type if forced_type in KOZ_POWERUP_CONFIG else random.choice(available_types)

        for _ in range(120):
            x = random.uniform(KOZ_POWERUP_RADIUS + 20, KOZ_MAP_WIDTH - KOZ_POWERUP_RADIUS - 20)
            y = random.uniform(KOZ_POWERUP_RADIUS + 20, KOZ_MAP_HEIGHT - KOZ_POWERUP_RADIUS - 20)
            if not is_spawn_clear(room, x, y, KOZ_POWERUP_RADIUS):
                continue
            room['powerup_seq'] += 1
            powerup = {
                'id': f"pow_{room['powerup_seq']}",
                'type': ptype,
                'x': x,
                'y': y,
                'radius': KOZ_POWERUP_RADIUS,
                'spawned_at': time.time()
            }
            room['powerups'][powerup['id']] = powerup
            return powerup
        return None

    def spawn_initial_powerups(room):
        initial = min(6, KOZ_POWERUP_MAX)
        for _ in range(initial):
            spawn_powerup(room)

    def generate_koz_obstacles(zone):
        obstacles = {}
        obstacle_types = ['rock', 'crate', 'pillar', 'wall']
        seq = 0
        attempts = 0
        while len(obstacles) < KOZ_OBSTACLE_COUNT and attempts < KOZ_OBSTACLE_COUNT * 60:
            attempts += 1
            radius = random.randint(KOZ_OBSTACLE_MIN_RADIUS, KOZ_OBSTACLE_MAX_RADIUS)
            x = random.uniform(radius + 60, KOZ_MAP_WIDTH - radius - 60)
            y = random.uniform(radius + 60, KOZ_MAP_HEIGHT - radius - 60)
            # Keep central zone readable.
            if math.hypot(x - zone['x'], y - zone['y']) < zone['radius'] * 0.26:
                continue
            blocked = False
            for obs in obstacles.values():
                if math.hypot(x - obs['x'], y - obs['y']) < (radius + obs['radius'] + 50):
                    blocked = True
                    break
            if blocked:
                continue
            seq += 1
            destructible = (seq % 4 == 0)
            obstacles[f"obs_{seq}"] = {
                'id': f"obs_{seq}",
                'x': x,
                'y': y,
                'radius': radius,
                'type': random.choice(obstacle_types),
                'destructible': destructible,
                'hp': 2 if destructible else 9999
            }
        return obstacles

    def create_koz_room():
        global koz_room_counter
        room_id = str(koz_room_counter)
        koz_room_counter += 1
        now = time.time()
        base_radius = KOZ_ZONE_START_RADIUS
        zx, zy = pick_zone_center(base_radius)
        tx, ty = pick_zone_center(base_radius)
        zone = {
            'x': zx,
            'y': zy,
            'radius': float(base_radius),
            'base_radius': float(base_radius),
            'core_radius': max(220, int(base_radius * 0.35)),
            'target_x': tx,
            'target_y': ty
        }
        room = {
            'players': {},
            'team_scores': {},
            'score_labels': {},
            'zone': zone,
            'time_left': KOZ_TIME_LIMIT,
            'last_tick': now,
            'last_pulse': now,
            'contested_seconds': 0,
            'controller': None,
            'task_running': False,
            'round': 1,
            'shrink_step': KOZ_SHRINK_STEP,
            'match_over': False,
            'storm_level': 1,
            'phase': 1,
            'finale': False,
            'drift_speed': KOZ_DRIFT_SPEED,
            'score_accumulator': 0.0,
            'last_state_broadcast': 0.0,
            'projectiles': {},
            'projectile_seq': 0,
            'powerups': {},
            'powerup_seq': 0,
            'next_powerup_at': now + 2.0,
            'shrink': {
                'active': False,
                'from_radius': float(base_radius),
                'to_radius': float(base_radius),
                'start_at': now,
                'end_at': now,
                'next_at': now + KOZ_SHRINK_INTERVAL
            }
        }
        room['obstacles'] = generate_koz_obstacles(zone)
        spawn_initial_powerups(room)
        koz_rooms[room_id] = room
        return room_id

    def get_or_create_open_koz_room():
        for room_id, room in koz_rooms.items():
            if len(room['players']) < KOZ_MAX_PLAYERS and not room['match_over']:
                return room_id, room
        room_id = create_koz_room()
        return room_id, koz_rooms[room_id]

    def get_koz_room_by_sid(sid):
        room_id = koz_sid_mapping.get(sid)
        if room_id and room_id in koz_rooms:
            return room_id, koz_rooms[room_id]
        return None, None

    def randomize_zone(room, reset_radius=False):
        radius = room['zone']['base_radius'] if reset_radius else room['zone']['radius']
        zx, zy = pick_zone_center(radius)
        tx, ty = pick_zone_center(radius)
        room['zone']['x'] = zx
        room['zone']['y'] = zy
        room['zone']['radius'] = float(radius)
        room['zone']['core_radius'] = max(220, int(radius * 0.35))
        room['zone']['target_x'] = tx
        room['zone']['target_y'] = ty
        room['contested_seconds'] = 0
        room['round'] += 1
        room['shrink_step'] = min(room['shrink_step'] + 8, 260)
        now = time.time()
        room['shrink']['active'] = False
        room['shrink']['next_at'] = now + KOZ_SHRINK_INTERVAL

    def compute_control(room):
        zone = room['zone']
        inside_ids = []
        core_ids = []
        for sid, p in room['players'].items():
            dx = p['x'] - zone['x']
            dy = p['y'] - zone['y']
            dist = math.sqrt(dx * dx + dy * dy)
            if dist <= zone['radius']:
                inside_ids.append(sid)
                if dist <= zone.get('core_radius', zone['radius'] * 0.35):
                    core_ids.append(sid)
        if len(inside_ids) == 1:
            return inside_ids[0], False, inside_ids, core_ids
        if len(inside_ids) > 1:
            return None, True, inside_ids, core_ids
        return None, False, inside_ids, core_ids

    def apply_powerup(room_id, room, sid, powerup):
        now = time.time()
        player = room['players'].get(sid)
        if not player:
            return
        ptype = powerup['type']
        cfg = KOZ_POWERUP_CONFIG.get(ptype, {})
        effects = player.setdefault('effects', {
            'speed_until': 0.0,
            'rapid_until': 0.0,
            'vision_until': 0.0,
            'shield': 0
        })
        effect_payload = {
            'type': ptype,
            'label': cfg.get('label', ptype),
            'username': player['username']
        }

        if ptype == 'speed-boost':
            duration = cfg.get('duration', 7.0)
            effects['speed_until'] = max(effects.get('speed_until', 0.0), now + duration)
            effect_payload['duration'] = duration
        elif ptype == 'shield':
            amount = cfg.get('amount', 35)
            effects['shield'] = int(clamp(effects.get('shield', 0) + amount, 0, 70))
            effect_payload['shield'] = effects['shield']
        elif ptype == 'rapid-fire':
            duration = cfg.get('duration', 6.0)
            effects['rapid_until'] = max(effects.get('rapid_until', 0.0), now + duration)
            effect_payload['duration'] = duration
        elif ptype == 'heal':
            amount = cfg.get('amount', 30)
            player['combat_hp'] = int(clamp(player.get('combat_hp', KOZ_COMBAT_MAX_HP) + amount, 0, KOZ_COMBAT_MAX_HP))
            effect_payload['combatHp'] = player['combat_hp']
        elif ptype == 'vision-ping':
            duration = cfg.get('duration', 3.5)
            effects['vision_until'] = max(effects.get('vision_until', 0.0), now + duration)
            nearby = []
            for other_sid, other in room['players'].items():
                if other_sid == sid:
                    continue
                if math.hypot(other['x'] - player['x'], other['y'] - player['y']) <= 900:
                    nearby.append({
                        'sid': other_sid,
                        'x': other['x'],
                        'y': other['y'],
                        'username': other['username'],
                        'character': other['character']
                    })
            socketio.emit('koz_vision_ping', {
                'duration': duration,
                'reveals': nearby
            }, to=sid)
            effect_payload['duration'] = duration
            effect_payload['revealCount'] = len(nearby)
        elif ptype == 'ammo-pack':
            amount = cfg.get('amount', 12)
            player['bullets'] = int(clamp(player.get('bullets', 0) + amount, 0, 999))
            effect_payload['bullets'] = player['bullets']

        socketio.emit('koz_powerup_effect', effect_payload, to=sid)

    def handle_player_down(room_id, room, target_sid, killer_sid, reason):
        target = room['players'].get(target_sid)
        if not target:
            return
        if reason == 'storm':
            room['team_scores'][target_sid] = max(0, room['team_scores'].get(target_sid, 0) - KOZ_RESPAWN_PENALTY)
            target['score'] = max(0, target.get('score', 0) - KOZ_RESPAWN_PENALTY)
        else:
            room['team_scores'][target_sid] = max(0, room['team_scores'].get(target_sid, 0) - KOZ_DEATH_PENALTY)
            target['score'] = max(0, target.get('score', 0) - KOZ_DEATH_PENALTY)
            target['deaths'] = int(target.get('deaths', 0) + 1)
            if killer_sid and killer_sid in room['players'] and killer_sid != target_sid:
                killer = room['players'][killer_sid]
                killer['kills'] = int(killer.get('kills', 0) + 1)
                room['team_scores'][killer_sid] = room['team_scores'].get(killer_sid, 0) + KOZ_KILL_SCORE
                killer['score'] = killer.get('score', 0) + KOZ_KILL_SCORE

        target['combat_hp'] = KOZ_COMBAT_MAX_HP
        target['zone_hp'] = max(target.get('zone_hp', KOZ_STORM_MAX_HP), int(KOZ_STORM_MAX_HP * 0.6))
        spawn_x, spawn_y = find_spawn_point(room, prefer_zone_ring=True)
        target['x'] = spawn_x
        target['y'] = spawn_y

        socketio.emit('koz_player_down', {
            'username': target['username'],
            'sid': target_sid,
            'reason': reason
        }, room=get_koz_room_name(room_id))
        socketio.emit('koz_player_position', {
            'sid': target_sid,
            'x': target['x'],
            'y': target['y'],
            'character': target['character'],
            'weapon_type': target.get('weapon_type'),
            'username': target['username'],
            'hp': target.get('combat_hp', KOZ_COMBAT_MAX_HP)
        }, room=get_koz_room_name(room_id), include_self=False)
        socketio.emit('koz_self_position', {
            'x': target['x'],
            'y': target['y']
        }, to=target_sid)

    def apply_damage(room_id, room, target_sid, shooter_sid, damage, projectile):
        target = room['players'].get(target_sid)
        if not target:
            return
        shooter = room['players'].get(shooter_sid)
        if not shooter:
            shooter_sid = None
        dmg = max(1, int(damage))
        effects = target.setdefault('effects', {
            'speed_until': 0.0,
            'rapid_until': 0.0,
            'vision_until': 0.0,
            'shield': 0
        })
        shield = int(effects.get('shield', 0))
        absorbed = min(shield, dmg)
        dmg -= absorbed
        effects['shield'] = max(0, shield - absorbed)

        if dmg > 0:
            target['combat_hp'] = max(0, int(target.get('combat_hp', KOZ_COMBAT_MAX_HP) - dmg))

        socketio.emit('koz_damage_feedback', {
            'target': target_sid,
            'shooter': shooter_sid,
            'hp': target.get('combat_hp', KOZ_COMBAT_MAX_HP),
            'damage': dmg,
            'absorbed': absorbed,
            'weaponType': projectile.get('weapon_type'),
            'shooterX': shooter.get('x') if shooter else None,
            'shooterY': shooter.get('y') if shooter else None
        }, to=target_sid)

        down = target.get('combat_hp', KOZ_COMBAT_MAX_HP) <= 0
        if down:
            handle_player_down(room_id, room, target_sid, shooter_sid, 'combat')

        socketio.emit('koz_player_hit', {
            'target': target_sid,
            'hp': target.get('combat_hp', KOZ_COMBAT_MAX_HP),
            'down': down,
            'targetName': target['username'],
            'killer': shooter_sid,
            'killerName': shooter.get('username') if shooter else None
        }, room=get_koz_room_name(room_id))

    def spawn_projectiles_for_shot(room, sid, aim_x, aim_y):
        player = room['players'][sid]
        weapon = get_weapon_cfg(player)
        angle = math.atan2(aim_y - player['y'], aim_x - player['x'])
        spawn_distance = KOZ_PLAYER_RADIUS + weapon['radius'] + 6
        spawn_x = player['x'] + math.cos(angle) * spawn_distance
        spawn_y = player['y'] + math.sin(angle) * spawn_distance

        projectiles = []
        for spread in weapon.get('spread', [0.0]):
            shot_angle = angle + spread
            room['projectile_seq'] += 1
            projectile = {
                'id': f"proj_{room['projectile_seq']}",
                'x': spawn_x,
                'y': spawn_y,
                'vx': math.cos(shot_angle) * weapon['speed'],
                'vy': math.sin(shot_angle) * weapon['speed'],
                'radius': weapon['radius'],
                'damage': weapon['damage'],
                'lifetime': weapon['lifetime'],
                'age': 0.0,
                'weapon_type': player['weapon_type'],
                'splash': weapon.get('splash', 0),
                'pierce': int(weapon.get('pierce', 0)),
                'bounces': int(weapon.get('bounces', 0)),
                'shooter': sid,
                'character': player['character'],
                'color': weapon.get('color', '#ffffff')
            }
            room['projectiles'][projectile['id']] = projectile
            projectiles.append(projectile)
        return projectiles

    def step_projectiles(room_id, room, dt):
        updates = []
        removed = []
        destroyed_obstacles = []

        for proj_id, projectile in list(room['projectiles'].items()):
            projectile['age'] += dt
            if projectile['age'] > projectile['lifetime']:
                removed.append({'id': proj_id, 'reason': 'lifetime'})
                del room['projectiles'][proj_id]
                continue

            projectile['x'] += projectile['vx'] * dt
            projectile['y'] += projectile['vy'] * dt

            bounced = False
            if projectile['x'] <= projectile['radius'] or projectile['x'] >= KOZ_MAP_WIDTH - projectile['radius']:
                if projectile['bounces'] > 0:
                    projectile['bounces'] -= 1
                    projectile['vx'] *= -1
                    projectile['x'] = clamp(projectile['x'], projectile['radius'], KOZ_MAP_WIDTH - projectile['radius'])
                    bounced = True
                else:
                    removed.append({'id': proj_id, 'reason': 'world'})
                    del room['projectiles'][proj_id]
                    continue
            if projectile['y'] <= projectile['radius'] or projectile['y'] >= KOZ_MAP_HEIGHT - projectile['radius']:
                if projectile['bounces'] > 0:
                    projectile['bounces'] -= 1
                    projectile['vy'] *= -1
                    projectile['y'] = clamp(projectile['y'], projectile['radius'], KOZ_MAP_HEIGHT - projectile['radius'])
                    bounced = True
                else:
                    removed.append({'id': proj_id, 'reason': 'world'})
                    del room['projectiles'][proj_id]
                    continue

            hit_obstacle = False
            for obs_id, obstacle in list(room.get('obstacles', {}).items()):
                if circle_hits_obstacle(projectile['x'], projectile['y'], projectile['radius'], obstacle):
                    hit_obstacle = True
                    if obstacle.get('destructible'):
                        obstacle['hp'] = max(0, int(obstacle.get('hp', 1) - 1))
                        if obstacle['hp'] <= 0:
                            destroyed_obstacles.append(obs_id)
                            del room['obstacles'][obs_id]
                    removed.append({'id': proj_id, 'reason': 'obstacle'})
                    del room['projectiles'][proj_id]
                    break
            if hit_obstacle:
                continue

            hit_target = None
            for target_sid, target in room['players'].items():
                if target_sid == projectile['shooter']:
                    continue
                if math.hypot(projectile['x'] - target['x'], projectile['y'] - target['y']) <= (KOZ_PLAYER_RADIUS + projectile['radius']):
                    hit_target = target_sid
                    break

            if hit_target:
                apply_damage(room_id, room, hit_target, projectile['shooter'], projectile['damage'], projectile)
                splash = projectile.get('splash', 0)
                if splash > 0:
                    for splash_sid, splash_target in room['players'].items():
                        if splash_sid in (projectile['shooter'], hit_target):
                            continue
                        if math.hypot(projectile['x'] - splash_target['x'], projectile['y'] - splash_target['y']) <= splash:
                            apply_damage(room_id, room, splash_sid, projectile['shooter'], max(1, int(projectile['damage'] * 0.55)), projectile)

                if projectile['pierce'] > 0:
                    projectile['pierce'] -= 1
                    projectile['x'] += projectile['vx'] * dt * 0.25
                    projectile['y'] += projectile['vy'] * dt * 0.25
                else:
                    removed.append({'id': proj_id, 'reason': 'hit'})
                    del room['projectiles'][proj_id]
                    continue

            if proj_id in room['projectiles']:
                updates.append({
                    'id': proj_id,
                    'x': projectile['x'],
                    'y': projectile['y'],
                    'vx': projectile['vx'],
                    'vy': projectile['vy'],
                    'age': projectile['age'],
                    'bounced': bounced
                })

        return updates, removed, destroyed_obstacles

    def emit_koz_state(room_id, room, contested, now):
        shrink = room['shrink']
        shrink_progress = 0.0
        if shrink.get('active'):
            denom = max(0.001, shrink['end_at'] - shrink['start_at'])
            shrink_progress = clamp((now - shrink['start_at']) / denom, 0.0, 1.0)
        socketio.emit('koz_state', {
            'zone': room['zone'],
            'controller': room['controller'],
            'controllerName': room['score_labels'].get(room['controller']),
            'contested': contested,
            'teamScores': room['team_scores'],
            'scoreLabels': room['score_labels'],
            'timeLeft': room['time_left'],
            'round': room['round'],
            'phase': room['phase'],
            'nextShrinkIn': get_next_shrink_seconds(room, now),
            'shrink': {
                'active': bool(shrink.get('active')),
                'progress': shrink_progress,
                'fromRadius': shrink.get('from_radius'),
                'toRadius': shrink.get('to_radius')
            },
            'storm': {
                'level': room['storm_level'],
                'damage': KOZ_STORM_DAMAGE * (KOZ_STORM_FINAL_MULT if room['finale'] else 1.0),
                'regen': KOZ_STORM_REGEN
            }
        }, room=get_koz_room_name(room_id))

    def koz_tick_loop(room_id):
        room = koz_rooms.get(room_id)
        if not room:
            return
        room['task_running'] = True

        while room_id in koz_rooms and len(koz_rooms[room_id]['players']) > 0:
            room = koz_rooms[room_id]
            if room['match_over']:
                break

            now = time.time()
            dt = now - room['last_tick']
            if dt < (KOZ_TICK_RATE * 0.5):
                time.sleep(0.01)
                continue
            dt = min(dt, 0.12)
            room['last_tick'] = now
            room['score_accumulator'] += dt

            # Zone drift
            dx = room['zone']['target_x'] - room['zone']['x']
            dy = room['zone']['target_y'] - room['zone']['y']
            dist = math.sqrt(dx * dx + dy * dy)
            step = room['drift_speed'] * dt
            if dist <= step:
                room['zone']['x'] = room['zone']['target_x']
                room['zone']['y'] = room['zone']['target_y']
            elif dist > 0:
                room['zone']['x'] += (dx / dist) * step
                room['zone']['y'] += (dy / dist) * step

            # Shrink timeline
            shrink = room['shrink']
            if shrink['active']:
                denom = max(0.001, shrink['end_at'] - shrink['start_at'])
                progress = clamp((now - shrink['start_at']) / denom, 0.0, 1.0)
                room['zone']['radius'] = shrink['from_radius'] + (shrink['to_radius'] - shrink['from_radius']) * progress
                room['zone']['core_radius'] = max(220, int(room['zone']['radius'] * 0.35))
                if progress >= 1.0:
                    shrink['active'] = False
                    shrink['next_at'] = now + KOZ_SHRINK_INTERVAL
                    tx, ty = pick_zone_center(room['zone']['radius'])
                    room['zone']['target_x'] = tx
                    room['zone']['target_y'] = ty
                    socketio.emit('koz_zone_event', {
                        'type': 'shrink_end',
                        'zone': room['zone']
                    }, room=get_koz_room_name(room_id))
            elif room['zone']['radius'] > KOZ_MIN_RADIUS and now >= shrink['next_at']:
                from_radius = float(room['zone']['radius'])
                to_radius = float(max(KOZ_MIN_RADIUS, room['zone']['radius'] - room['shrink_step']))
                shrink['active'] = True
                shrink['from_radius'] = from_radius
                shrink['to_radius'] = to_radius
                shrink['start_at'] = now
                shrink['end_at'] = now + KOZ_SHRINK_DURATION
                socketio.emit('koz_zone_event', {
                    'type': 'shrink_start',
                    'zone': room['zone'],
                    'fromRadius': from_radius,
                    'toRadius': to_radius,
                    'duration': KOZ_SHRINK_DURATION
                }, room=get_koz_room_name(room_id))

            if room['zone']['radius'] <= KOZ_MIN_RADIUS and not room['finale']:
                room['finale'] = True
                room['shrink_step'] = max(room['shrink_step'], 240)
                socketio.emit('koz_zone_event', {
                    'type': 'finale',
                    'zone': room['zone']
                }, room=get_koz_room_name(room_id))

            pad = room['zone']['radius'] + 60
            room['zone']['x'] = clamp(room['zone']['x'], pad, KOZ_MAP_WIDTH - pad)
            room['zone']['y'] = clamp(room['zone']['y'], pad, KOZ_MAP_HEIGHT - pad)

            # Pulse pressure
            if now - room['last_pulse'] >= KOZ_PULSE_INTERVAL:
                room['last_pulse'] = now
                for sid, p in room['players'].items():
                    dxp = room['zone']['x'] - p['x']
                    dyp = room['zone']['y'] - p['y']
                    distp = math.sqrt(dxp * dxp + dyp * dyp)
                    if distp > 1:
                        pull = KOZ_PULSE_PULL if distp > room['zone']['radius'] else KOZ_PULSE_PULL * 0.5
                        p['x'] = clamp(p['x'] + (dxp / distp) * pull, KOZ_PLAYER_RADIUS + 5, KOZ_MAP_WIDTH - KOZ_PLAYER_RADIUS - 5)
                        p['y'] = clamp(p['y'] + (dyp / distp) * pull, KOZ_PLAYER_RADIUS + 5, KOZ_MAP_HEIGHT - KOZ_PLAYER_RADIUS - 5)
                        socketio.emit('koz_player_position', {
                            'sid': sid,
                            'x': p['x'],
                            'y': p['y'],
                            'character': p['character'],
                            'weapon_type': p.get('weapon_type'),
                            'username': p['username'],
                            'hp': p.get('combat_hp', KOZ_COMBAT_MAX_HP)
                        }, room=get_koz_room_name(room_id), include_self=False)
                        socketio.emit('koz_self_position', {'x': p['x'], 'y': p['y']}, to=sid)
                socketio.emit('koz_zone_event', {'type': 'pulse'}, room=get_koz_room_name(room_id))

            # Player state and storm updates
            storm_mult = KOZ_STORM_FINAL_MULT if room['finale'] else 1.0
            storm_damage = KOZ_STORM_DAMAGE * storm_mult
            for sid, p in list(room['players'].items()):
                effects = p.setdefault('effects', {
                    'speed_until': 0.0,
                    'rapid_until': 0.0,
                    'vision_until': 0.0,
                    'shield': 0
                })
                distp = math.hypot(p['x'] - room['zone']['x'], p['y'] - room['zone']['y'])
                outside = distp > room['zone']['radius']
                if outside:
                    p['zone_hp'] = max(0, p.get('zone_hp', KOZ_STORM_MAX_HP) - storm_damage * dt)
                else:
                    p['zone_hp'] = min(KOZ_STORM_MAX_HP, p.get('zone_hp', KOZ_STORM_MAX_HP) + KOZ_STORM_REGEN * dt)

                speed_mult = 1.0
                if outside:
                    speed_mult *= 0.84
                if p['zone_hp'] < 35:
                    speed_mult *= 0.82
                if now < effects.get('speed_until', 0):
                    speed_mult *= 1.35
                p['speed_multiplier'] = speed_mult
                p['rapid_fire'] = now < effects.get('rapid_until', 0)

                if p['zone_hp'] <= 0:
                    p['zone_hp'] = int(KOZ_STORM_MAX_HP * 0.55)
                    handle_player_down(room_id, room, sid, None, 'storm')

                if now - p.get('last_self_emit', 0) >= 0.15:
                    p['last_self_emit'] = now
                    socketio.emit('koz_self_state', {
                        'zoneHp': p.get('zone_hp', KOZ_STORM_MAX_HP),
                        'outside': outside,
                        'speedMultiplier': p.get('speed_multiplier', 1.0),
                        'combatHp': p.get('combat_hp', KOZ_COMBAT_MAX_HP),
                        'bullets': p.get('bullets', 0),
                        'shield': effects.get('shield', 0),
                        'nextShrinkIn': get_next_shrink_seconds(room, now),
                        'storm': {
                            'level': room['storm_level'],
                            'damage': storm_damage,
                            'regen': KOZ_STORM_REGEN
                        },
                        'phase': room['phase']
                    }, to=sid)

            # Powerup spawning
            if now >= room.get('next_powerup_at', 0) and len(room['powerups']) < KOZ_POWERUP_MAX:
                spawned = spawn_powerup(room)
                room['next_powerup_at'] = now + KOZ_POWERUP_RESPAWN_DELAY
                if spawned:
                    socketio.emit('koz_powerup_spawned', spawned, room=get_koz_room_name(room_id))

            # Powerup pickup
            for sid, p in list(room['players'].items()):
                for powerup_id, powerup in list(room['powerups'].items()):
                    if math.hypot(p['x'] - powerup['x'], p['y'] - powerup['y']) <= (KOZ_PLAYER_RADIUS + powerup.get('radius', KOZ_POWERUP_RADIUS)):
                        del room['powerups'][powerup_id]
                        apply_powerup(room_id, room, sid, powerup)
                        socketio.emit('koz_powerup_collected', {
                            'id': powerup_id,
                            'type': powerup['type'],
                            'by': sid,
                            'username': p['username']
                        }, room=get_koz_room_name(room_id))
                        room['next_powerup_at'] = min(room.get('next_powerup_at', now + KOZ_POWERUP_RESPAWN_DELAY), now + KOZ_POWERUP_RESPAWN_DELAY)

            # Projectiles (server authoritative)
            projectile_updates, projectile_removed, obstacle_removed = step_projectiles(room_id, room, dt)
            if projectile_updates:
                socketio.emit('koz_projectile_positions', {'updates': projectile_updates}, room=get_koz_room_name(room_id))
            if projectile_removed:
                socketio.emit('koz_projectile_removed', {'items': projectile_removed}, room=get_koz_room_name(room_id))
            if obstacle_removed:
                socketio.emit('koz_obstacles_removed', {'ids': obstacle_removed}, room=get_koz_room_name(room_id))

            # Score/time ticks at 1Hz
            while room['score_accumulator'] >= KOZ_SCORE_TICK:
                room['score_accumulator'] -= KOZ_SCORE_TICK
                room['time_left'] = max(0, room['time_left'] - 1)
                controller, contested, _, core_ids = compute_control(room)
                if contested:
                    room['contested_seconds'] += 1
                else:
                    room['contested_seconds'] = 0

                if controller and not contested:
                    room['team_scores'].setdefault(controller, 0)
                    room['team_scores'][controller] += KOZ_SCORE_PER_SEC
                    if controller in room['players']:
                        room['players'][controller]['score'] = room['players'][controller].get('score', 0) + KOZ_SCORE_PER_SEC
                    if controller in core_ids:
                        room['team_scores'][controller] += KOZ_CORE_BONUS_PER_SEC
                        if controller in room['players']:
                            room['players'][controller]['score'] = room['players'][controller].get('score', 0) + KOZ_CORE_BONUS_PER_SEC

                if controller != room['controller'] or contested:
                    room['controller'] = controller
                    socketio.emit('koz_control_changed', {
                        'controller': controller,
                        'controllerName': room['score_labels'].get(controller),
                        'contested': contested
                    }, room=get_koz_room_name(room_id))

                if room['contested_seconds'] >= KOZ_CONTESTED_RELOCATE_SECONDS:
                    randomize_zone(room, reset_radius=False)
                    socketio.emit('koz_zone_event', {
                        'type': 'contested_relocate',
                        'zone': room['zone'],
                        'round': room['round']
                    }, room=get_koz_room_name(room_id))

                if room['time_left'] <= 45 and not room['finale']:
                    room['finale'] = True
                    room['shrink_step'] = max(room['shrink_step'], 260)
                    socketio.emit('koz_zone_event', {'type': 'finale', 'zone': room['zone']}, room=get_koz_room_name(room_id))

            # Phase/storm level tracks radius ratio
            radius_ratio = room['zone']['radius'] / max(1.0, room['zone']['base_radius'])
            room['phase'] = max(1, min(6, int((1 - radius_ratio) * 6) + 1))
            room['storm_level'] = room['phase']

            # Match end conditions
            winner_id = None
            for pid, score in room['team_scores'].items():
                if score >= KOZ_TARGET_SCORE:
                    winner_id = pid
                    break
            if room['time_left'] <= 0 or winner_id:
                room['match_over'] = True
                if not winner_id:
                    winner_id = max(room['team_scores'].items(), key=lambda x: x[1])[0] if room['team_scores'] else None
                socketio.emit('koz_match_end', {
                    'winner': winner_id,
                    'winnerName': room['score_labels'].get(winner_id),
                    'teamScores': room['team_scores'],
                    'timeLeft': room['time_left']
                }, room=get_koz_room_name(room_id))
                break

            controller, contested, _, _ = compute_control(room)
            if now - room.get('last_state_broadcast', 0.0) >= KOZ_STATE_BROADCAST_INTERVAL:
                room['last_state_broadcast'] = now
                emit_koz_state(room_id, room, contested, now)

            time.sleep(KOZ_TICK_RATE)

        room = koz_rooms.get(room_id)
        if room:
            room['task_running'] = False

    def get_koz_aggregate_status():
        total_players = sum(len(room['players']) for room in koz_rooms.values())
        active_rooms = len([room for room in koz_rooms.values() if room['players']])
        open_slots = sum(KOZ_MAX_PLAYERS - len(room['players']) for room in koz_rooms.values())
        if open_slots == 0:
            open_slots = KOZ_MAX_PLAYERS
        return {
            'totalPlayers': total_players,
            'activeRooms': active_rooms,
            'openSlots': open_slots
        }

    @socketio.on('koz_get_status')
    def handle_koz_get_status(data):
        emit('koz_status', get_koz_aggregate_status())

    @socketio.on('koz_join')
    def handle_koz_join(data):
        sid = request.sid
        username = data.get('username', 'Guest')
        character = str(data.get('character', 'knight')).lower()
        weapon_type = resolve_weapon_type(character, data.get('weapon_type') or data.get('selected_weapon'))

        existing_room_id, existing_room = get_koz_room_by_sid(sid)
        if existing_room:
            existing_room.setdefault('score_labels', {})
            for pid, pdata in existing_room['players'].items():
                existing_room['score_labels'].setdefault(pid, pdata.get('username', 'Player'))
                if 'combat_hp' not in pdata:
                    pdata['combat_hp'] = KOZ_COMBAT_MAX_HP
            emit('koz_room_state', {
                'roomId': existing_room_id,
                'zone': existing_room['zone'],
                'teamScores': existing_room['team_scores'],
                'scoreLabels': existing_room.get('score_labels', {}),
                'timeLeft': existing_room['time_left'],
                'round': existing_room['round'],
                'phase': existing_room.get('phase', 1),
                'nextShrinkIn': get_next_shrink_seconds(existing_room, time.time()),
                'storm': {
                    'level': existing_room.get('storm_level', 1),
                    'damage': KOZ_STORM_DAMAGE,
                    'regen': KOZ_STORM_REGEN
                },
                'selfId': sid,
                'map': {'width': KOZ_MAP_WIDTH, 'height': KOZ_MAP_HEIGHT},
                'rules': {
                    'targetScore': KOZ_TARGET_SCORE,
                    'timeLimit': KOZ_TIME_LIMIT,
                    'scorePerSec': KOZ_SCORE_PER_SEC,
                    'coreBonus': KOZ_CORE_BONUS_PER_SEC,
                    'stormMax': KOZ_STORM_MAX_HP
                },
                'obstacles': serialize_obstacles(existing_room),
                'powerups': serialize_powerups(existing_room),
                'players': serialize_players(existing_room)
            })
            player = existing_room['players'].get(sid)
            if player:
                emit('koz_self_position', {'x': player.get('x', 0), 'y': player.get('y', 0)}, to=sid)
                effects = player.get('effects', {})
                emit('koz_self_state', {
                    'zoneHp': player.get('zone_hp', KOZ_STORM_MAX_HP),
                    'outside': math.hypot(player.get('x', 0) - existing_room['zone']['x'], player.get('y', 0) - existing_room['zone']['y']) > existing_room['zone']['radius'],
                    'speedMultiplier': player.get('speed_multiplier', 1.0),
                    'combatHp': player.get('combat_hp', KOZ_COMBAT_MAX_HP),
                    'bullets': player.get('bullets', 0),
                    'shield': effects.get('shield', 0),
                    'nextShrinkIn': get_next_shrink_seconds(existing_room, time.time()),
                    'storm': {
                        'level': existing_room.get('storm_level', 1),
                        'damage': KOZ_STORM_DAMAGE * (KOZ_STORM_FINAL_MULT if existing_room.get('finale') else 1.0),
                        'regen': KOZ_STORM_REGEN
                    },
                    'phase': existing_room.get('phase', 1)
                }, to=sid)
            return

        room_id, room = get_or_create_open_koz_room()
        room_name = get_koz_room_name(room_id)
        join_room(room_name)
        koz_sid_mapping[sid] = room_id
        display_name = get_unique_display_name(room, username)
        spawn_x, spawn_y = find_spawn_point(room, prefer_zone_ring=True)

        room['players'][sid] = {
            'username': display_name,
            'character': character,
            'weapon_type': weapon_type,
            'score': 0,
            'kills': 0,
            'deaths': 0,
            'x': spawn_x,
            'y': spawn_y,
            'zone_hp': KOZ_STORM_MAX_HP,
            'combat_hp': KOZ_COMBAT_MAX_HP,
            'last_move': time.time(),
            'last_shot_at': 0.0,
            'speed_multiplier': 1.0,
            'rapid_fire': False,
            'bullets': int(clamp(data.get('bullets', 60), 0, 999)),
            'effects': {
                'speed_until': 0.0,
                'rapid_until': 0.0,
                'vision_until': 0.0,
                'shield': 0
            }
        }
        room['team_scores'].setdefault(sid, 0)
        room['score_labels'][sid] = display_name

        emit('koz_room_state', {
            'roomId': room_id,
            'zone': room['zone'],
            'teamScores': room['team_scores'],
            'scoreLabels': room['score_labels'],
            'timeLeft': room['time_left'],
            'round': room['round'],
            'phase': room.get('phase', 1),
            'nextShrinkIn': get_next_shrink_seconds(room, time.time()),
            'storm': {
                'level': room.get('storm_level', 1),
                'damage': KOZ_STORM_DAMAGE,
                'regen': KOZ_STORM_REGEN
            },
            'selfId': sid,
            'map': {'width': KOZ_MAP_WIDTH, 'height': KOZ_MAP_HEIGHT},
            'rules': {
                'targetScore': KOZ_TARGET_SCORE,
                'timeLimit': KOZ_TIME_LIMIT,
                'scorePerSec': KOZ_SCORE_PER_SEC,
                'coreBonus': KOZ_CORE_BONUS_PER_SEC,
                'stormMax': KOZ_STORM_MAX_HP
            },
            'obstacles': serialize_obstacles(room),
            'powerups': serialize_powerups(room),
            'players': serialize_players(room)
        })
        emit('koz_self_position', {'x': spawn_x, 'y': spawn_y}, to=sid)
        emit('koz_self_state', {
            'zoneHp': KOZ_STORM_MAX_HP,
            'outside': False,
            'speedMultiplier': 1.0,
            'combatHp': KOZ_COMBAT_MAX_HP,
            'bullets': room['players'][sid].get('bullets', 0),
            'shield': 0,
            'nextShrinkIn': get_next_shrink_seconds(room, time.time()),
            'storm': {
                'level': room.get('storm_level', 1),
                'damage': KOZ_STORM_DAMAGE,
                'regen': KOZ_STORM_REGEN
            },
            'phase': room.get('phase', 1)
        }, to=sid)

        socketio.emit('koz_player_joined', {
            'sid': sid,
            'username': display_name,
            'character': character,
            'weapon_type': weapon_type
        }, room=room_name, include_self=False)

        if not room['task_running']:
            socketio.start_background_task(koz_tick_loop, room_id)

        emit('koz_status', get_koz_aggregate_status())

    @socketio.on('koz_move')
    def handle_koz_move(data):
        sid = request.sid
        room_id, room = get_koz_room_by_sid(sid)
        if not room or sid not in room['players']:
            return
        player = room['players'][sid]
        desired_x = data.get('x', player['x'])
        desired_y = data.get('y', player['y'])

        now = time.time()
        last_move = player.get('last_move', now)
        dt = max(0.02, min(0.25, now - last_move))
        player['last_move'] = now
        max_step = KOZ_BASE_SPEED * dt * player.get('speed_multiplier', 1.0)
        dx = desired_x - player['x']
        dy = desired_y - player['y']
        dist = math.sqrt(dx * dx + dy * dy)
        if dist > max_step and dist > 0:
            desired_x = player['x'] + (dx / dist) * max_step
            desired_y = player['y'] + (dy / dist) * max_step

        margin = KOZ_PLAYER_RADIUS + 4
        desired_x = clamp(desired_x, margin, KOZ_MAP_WIDTH - margin)
        desired_y = clamp(desired_y, margin, KOZ_MAP_HEIGHT - margin)

        min_dist = KOZ_PLAYER_RADIUS * 2
        for other_sid, other in room['players'].items():
            if other_sid == sid:
                continue
            desired_x, desired_y, _ = resolve_player_collision(
                desired_x, desired_y, other.get('x', desired_x), other.get('y', desired_y), min_dist
            )

        for obstacle in room.get('obstacles', {}).values():
            desired_x, desired_y, _ = resolve_circle_obstacle(desired_x, desired_y, KOZ_PLAYER_RADIUS, obstacle)

        desired_x = clamp(desired_x, margin, KOZ_MAP_WIDTH - margin)
        desired_y = clamp(desired_y, margin, KOZ_MAP_HEIGHT - margin)
        room['players'][sid]['x'] = desired_x
        room['players'][sid]['y'] = desired_y
        emit('koz_player_position', {
            'sid': sid,
            'x': desired_x,
            'y': desired_y,
            'character': room['players'][sid]['character'],
            'weapon_type': room['players'][sid].get('weapon_type'),
            'username': room['players'][sid]['username'],
            'hp': room['players'][sid].get('combat_hp', KOZ_COMBAT_MAX_HP)
        }, room=get_koz_room_name(room_id), include_self=False)

        emit('koz_self_position', {'x': desired_x, 'y': desired_y}, to=sid)

    @socketio.on('koz_shoot')
    def handle_koz_shoot(data):
        sid = request.sid
        room_id, room = get_koz_room_by_sid(sid)
        if not room or sid not in room['players']:
            return
        player = room['players'][sid]
        weapon = get_weapon_cfg(player)
        now = time.time()
        rapid_mult = 0.68 if player.get('rapid_fire') else 1.0
        cooldown = weapon['cooldown'] * rapid_mult
        if now - player.get('last_shot_at', 0) < cooldown:
            emit('koz_shot_rejected', {'reason': 'cooldown', 'remaining': max(0.0, cooldown - (now - player.get('last_shot_at', 0)))}, to=sid)
            return
        if player.get('bullets', 0) <= 0:
            emit('koz_shot_rejected', {'reason': 'ammo'}, to=sid)
            return
        if len(room.get('projectiles', {})) > 700:
            emit('koz_shot_rejected', {'reason': 'busy'}, to=sid)
            return

        aim_x = data.get('aimX')
        aim_y = data.get('aimY')
        if aim_x is None or aim_y is None:
            # backward compatibility for older clients sending vector dx/dy
            dx = float(data.get('dx', 0.0))
            dy = float(data.get('dy', 0.0))
            mag = math.hypot(dx, dy) or 1.0
            aim_x = player['x'] + (dx / mag) * 1000
            aim_y = player['y'] + (dy / mag) * 1000
        try:
            aim_x = float(aim_x)
            aim_y = float(aim_y)
        except (TypeError, ValueError):
            emit('koz_shot_rejected', {'reason': 'aim'}, to=sid)
            return
        if not math.isfinite(aim_x) or not math.isfinite(aim_y):
            emit('koz_shot_rejected', {'reason': 'aim'}, to=sid)
            return

        player['last_shot_at'] = now
        player['bullets'] = max(0, int(player.get('bullets', 0) - 1))
        spawned = spawn_projectiles_for_shot(room, sid, aim_x, aim_y)
        payload = []
        for projectile in spawned:
            payload.append({
                'id': projectile['id'],
                'x': projectile['x'],
                'y': projectile['y'],
                'vx': projectile['vx'],
                'vy': projectile['vy'],
                'radius': projectile['radius'],
                'weaponType': projectile['weapon_type'],
                'character': projectile['character'],
                'shooter': projectile['shooter'],
                'color': projectile.get('color')
            })
        socketio.emit('koz_projectile_spawned', {'projectiles': payload}, room=get_koz_room_name(room_id))

        # Legacy compatibility for older clients still listening for koz_bullet.
        if payload:
            first = payload[0]
            socketio.emit('koz_bullet', {
                'bulletX': first['x'],
                'bulletY': first['y'],
                'dx': first['vx'],
                'dy': first['vy'],
                'character': player['character'],
                'shooter': sid
            }, room=get_koz_room_name(room_id), include_self=False)

    @socketio.on('koz_hit_player')
    def handle_koz_hit_player(data):
        # Deprecated client-driven hit event. Damage is now server-authoritative via projectile simulation.
        return

    @socketio.on('koz_leave')
    def handle_koz_leave(data):
        cleanup_koz_player(request.sid)

    def cleanup_koz_player(sid):
        room_id, room = get_koz_room_by_sid(sid)
        if not room or sid not in room['players']:
            return
        username = room['players'][sid]['username']
        del room['players'][sid]
        if sid in room['team_scores']:
            del room['team_scores'][sid]
        if sid in room['score_labels']:
            del room['score_labels'][sid]
        if room.get('controller') == sid:
            room['controller'] = None
        if sid in koz_sid_mapping:
            del koz_sid_mapping[sid]
        # Clean up active projectiles owned by this player.
        for proj_id, projectile in list(room.get('projectiles', {}).items()):
            if projectile.get('shooter') == sid:
                del room['projectiles'][proj_id]
        leave_room(get_koz_room_name(room_id))
        emit('koz_player_left', {'username': username, 'sid': sid}, room=get_koz_room_name(room_id))
        if len(room['players']) == 0:
            del koz_rooms[room_id]
        emit('koz_status', get_koz_aggregate_status())
