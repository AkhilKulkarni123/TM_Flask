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
# Mode selection viewers (not yet in arena)
pvp_mode_viewers = set()


# Multiplayer collision tuning (server authoritative)
# PVP sprite renders ~60px wide, so radius ~28-30 keeps collisions fair.
PVP_PLAYER_RADIUS = 28
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
    
    # ADD this function right before:  @socketio.on('boss_join_room')
    def _broadcast_victory(room_id):
        """Collect final stats and broadcast boss_defeated to all players"""
        if room_id not in boss_battles:
            return
        if not boss_battles[room_id].get('victory_pending'):
            return

        # Clear the flag first to prevent double-broadcast
        boss_battles[room_id]['victory_pending'] = False

        all_player_stats = []
        for player_sid, player_data in boss_battles[room_id]['players'].items():
            bullets_hit = player_data.get('bullets_hit', 0)
            bullets_fired = max(player_data.get('bullets_fired', 0), bullets_hit)
            all_player_stats.append({
                'sid': player_sid,
                'username': player_data.get('username', 'Unknown'),
                'character': player_data.get('character', 'knight'),
                'damage_dealt': player_data.get('damage_dealt', 0),
                'bullets_fired': bullets_fired,
                'bullets_hit': bullets_hit,
                'lives': player_data.get('lives', 0),
                'lives_lost': player_data.get('lives_lost', 0),
                'powerups_collected': player_data.get('powerups_collected', [])
            })

        socketio.emit('boss_defeated', {
            'message': 'The boss has been defeated!',
            'players': get_room_players_list(room_id),
            'all_player_stats': all_player_stats
        }, room=room_id)

        # Reset boss for next battle
        boss_battles[room_id]['boss_health'] = boss_battles[room_id]['max_health']
        # Reset reported flags
        for p in boss_battles[room_id]['players'].values():
            p['stats_reported'] = False
        print(f"[BOSS] Victory broadcast sent for room {room_id} with {len(all_player_stats)} player stats.")



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
            if sid in boss_battles[room_id]['players']:
                player = boss_battles[room_id]['players'][sid]
                if 'bullets_fired' not in player:
                    player['bullets_fired'] = 0
                if 'bullets_hit' not in player:
                    player['bullets_hit'] = 0
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
        # Track damage dealt and bullets hit by this player
        if 'damage_dealt' not in player:
            player['damage_dealt'] = 0
        if 'bullets_hit' not in player:
            player['bullets_hit'] = 0
        if 'bullets_fired' not in player:
            player['bullets_fired'] = 0
            
        player['damage_dealt'] += damage
        player['bullets_hit'] = player.get('bullets_hit', 0) + 1

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
        # Check if boss is defeated
        # ADD this replacement block:
        if boss_battles[room_id]['boss_health'] <= 0:
            # Mark room as pending victory — wait for stats reports before broadcasting win
            boss_battles[room_id]['victory_pending'] = True
            boss_battles[room_id]['victory_reporter'] = sid
            # Ask all players to report their stats immediately
            emit('boss_request_final_stats', {
                'room_id': room_id
            }, room=room_id)
            print(f"[BOSS] Boss defeated in room {room_id}! Waiting for player stats reports.")

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
        if sid in pvp_mode_viewers:
            pvp_mode_viewers.discard(sid)

        # Clean up SLITHERRUSH arenas
        try:
            from socketio_handlers.slitherrush_events import cleanup_disconnected_player as cleanup_slitherrush_player
            cleanup_slitherrush_player(sid)
        except Exception:
            pass


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

    # REMOVE the existing handle_report_stats function and REPLACE with:
    @socketio.on('boss_report_stats')
    def handle_report_stats(data):
        """Handle player reporting their battle stats — triggers victory once all players report"""
        room_id = data.get('room_id')
        sid = request.sid

        if not room_id or room_id not in boss_battles:
            return

        if sid not in boss_battles[room_id]['players']:
            return

        player = boss_battles[room_id]['players'][sid]

        # Only take lives_lost and powerups from client — server already tracks
        # bullets_fired (via boss_player_shoot) and bullets_hit (via boss_damage) accurately
        if 'lives_lost' in data:
            player['lives_lost'] = int(data['lives_lost'] or 0)
        if 'powerups_collected' in data:
            player['powerups_collected'] = data['powerups_collected']
        if 'damage_dealt' in data:
            client_damage = int(data['damage_dealt'] or 0)
            player['damage_dealt'] = max(player.get('damage_dealt', 0), client_damage)

        player['stats_reported'] = True
        print(f"[BOSS] Player {player.get('username')} reported stats: bullets_fired={player.get('bullets_fired')}, bullets_hit={player.get('bullets_hit')}, damage={player.get('damage_dealt')}, lives_lost={player.get('lives_lost')}")

        if boss_battles[room_id].get('victory_pending'):
            all_players = boss_battles[room_id]['players']
            all_reported = all(p.get('stats_reported') for p in all_players.values())
            if all_reported:
                _broadcast_victory(room_id)
            elif not boss_battles[room_id].get('victory_timer_set'):
                boss_battles[room_id]['victory_timer_set'] = True
                def delayed_victory():
                    if room_id in boss_battles and boss_battles[room_id].get('victory_pending'):
                        _broadcast_victory(room_id)
                timer = threading.Timer(3.0, delayed_victory)
                timer.daemon = True
                timer.start()
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
        status = get_pvp_aggregate_status()
        status['viewerCount'] = len(pvp_mode_viewers)
        emit('pvp_status', status)

    @socketio.on('pvp_mode_viewer_join')
    def handle_pvp_mode_viewer_join(data):
        sid = request.sid
        pvp_mode_viewers.add(sid)
        status = get_pvp_aggregate_status()
        status['viewerCount'] = len(pvp_mode_viewers)
        emit('pvp_status', status)

    @socketio.on('pvp_mode_viewer_leave')
    def handle_pvp_mode_viewer_leave(data):
        sid = request.sid
        if sid in pvp_mode_viewers:
            pvp_mode_viewers.discard(sid)
        status = get_pvp_aggregate_status()
        status['viewerCount'] = len(pvp_mode_viewers)
        emit('pvp_status', status)

    @socketio.on('pvp_join')
    def handle_pvp_join(data):
        """Handle player joining PVP arena (assigns an open room or creates a new one)"""
        data = data or {}
        sid = request.sid
        username = data.get('username', 'Guest')
        character = data.get('character', 'knight')
        bullets = data.get('bullets', 0)
        lives = data.get('lives', 5)
        requested_room_id = str(data.get('room_id') or data.get('roomId') or '').strip()

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

        if requested_room_id and requested_room_id in pvp_rooms and len(pvp_rooms[requested_room_id]['players']) < MAX_PVP_PLAYERS:
            room_id = requested_room_id
            room = pvp_rooms[room_id]
        else:
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
                'message': 'Both players are in the arena! Press Ready to start.',
                'playerCount': get_pvp_player_count(room),
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
            emit('pvp_battle_start', {
                'message': 'Battle starting!',
                'player1': room['players'].get(room['player_order'][0]) if len(room['player_order']) > 0 else None,
                'player2': room['players'].get(room['player_order'][1]) if len(room['player_order']) > 1 else None
            }, room=get_pvp_room_name(room_id))

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
