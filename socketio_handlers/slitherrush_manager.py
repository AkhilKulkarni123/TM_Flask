import math
import random
import threading
import time
from typing import Dict, List, Optional, Tuple


class SlitherRushManager:
    ROOM_PREFIX = 'slitherrush_arena'

    TICK_RATE = 30
    TICK_INTERVAL = 1.0 / TICK_RATE
    SNAPSHOT_INTERVAL = 1.0 / 15.0
    LEADERBOARD_INTERVAL = 0.45

    WIDTH = 4400
    HEIGHT = 2800

    MAX_PLAYERS_PER_ARENA = 32

    SNAKE_START_LENGTH = 14
    SNAKE_MIN_LENGTH = 10
    SNAKE_MAX_LENGTH = 40
    PLAYER_SPEED = 225.0

    HEAD_RADIUS = 11.0
    BULLET_SPEED = 640.0
    BULLET_RADIUS = 4.0
    BULLET_LIFETIME = 1.35
    FIRE_COOLDOWN_SECONDS = 0.22
    MAX_BULLETS_PER_ARENA = 240

    MAX_HP = 3
    SPAWN_PROTECT_SECONDS = 0.6

    COLORS = [
        '#56d8ff', '#ffb457', '#94f88f', '#ff7e9f', '#bfa7ff',
        '#ffd86f', '#67f0d2', '#8ec5ff', '#ff9d6e', '#d2ff7a',
    ]

    def __init__(self, socketio):
        self.socketio = socketio
        self.lock = threading.RLock()

        self.arenas: Dict[str, Dict] = {}
        self.sid_to_arena: Dict[str, str] = {}
        self.party_to_arena: Dict[str, str] = {}

        self._arena_counter = 1
        self._bullet_counter = 1
        self.loop_started = False

    # ----------------------------- Arena helpers -----------------------------

    def _new_arena_id(self) -> str:
        arena_id = str(self._arena_counter)
        self._arena_counter += 1
        return arena_id

    def _new_bullet_id(self) -> str:
        bullet_id = f"b_{self._bullet_counter}"
        self._bullet_counter += 1
        return bullet_id

    def _arena_room_name(self, arena_id: str) -> str:
        return f"{self.ROOM_PREFIX}_{arena_id}"

    def _create_arena(self) -> Dict:
        arena_id = self._new_arena_id()
        now = time.time()
        arena = {
            'arena_id': arena_id,
            'room': self._arena_room_name(arena_id),
            'bounds': {'width': self.WIDTH, 'height': self.HEIGHT},
            'tick_rate': self.TICK_RATE,
            'max_players': self.MAX_PLAYERS_PER_ARENA,
            'state': 'active',
            'created_at': now,
            'players': {},
            'bullets': [],
            'last_snapshot_at': 0.0,
            'last_leaderboard_at': 0.0,
        }
        self.arenas[arena_id] = arena
        return arena

    def _cleanup_party_mapping(self, party_id: Optional[str], arena_id: str) -> None:
        if not party_id:
            return
        mapped = self.party_to_arena.get(party_id)
        if mapped != arena_id:
            return

        arena = self.arenas.get(arena_id)
        if not arena:
            self.party_to_arena.pop(party_id, None)
            return

        has_party_member = any(
            p.get('party_id') == party_id
            for p in arena['players'].values()
        )
        if not has_party_member:
            self.party_to_arena.pop(party_id, None)

    def _select_arena_for_join(self, party_id: Optional[str]) -> Dict:
        if party_id:
            mapped_id = self.party_to_arena.get(party_id)
            if mapped_id and mapped_id in self.arenas:
                mapped_arena = self.arenas[mapped_id]
                if len(mapped_arena['players']) < mapped_arena['max_players']:
                    return mapped_arena
                self.party_to_arena.pop(party_id, None)

        candidates = [
            arena for arena in self.arenas.values()
            if len(arena['players']) < arena['max_players']
        ]
        if candidates:
            candidates.sort(key=lambda a: a.get('created_at', 0))
            return candidates[0]

        return self._create_arena()

    # ----------------------------- Player helpers ----------------------------

    def _normalize_direction(self, raw: Optional[Dict], fallback: Tuple[float, float] = (1.0, 0.0)) -> Tuple[float, float]:
        if not isinstance(raw, dict):
            return fallback
        try:
            x = float(raw.get('x', fallback[0]))
            y = float(raw.get('y', fallback[1]))
        except (TypeError, ValueError):
            return fallback

        if not math.isfinite(x) or not math.isfinite(y):
            return fallback

        mag = math.hypot(x, y)
        if mag < 1e-6:
            return fallback

        return (x / mag, y / mag)

    def _random_spawn(self, arena: Dict) -> Tuple[float, float]:
        width = float(arena['bounds']['width'])
        height = float(arena['bounds']['height'])
        margin = 120.0

        for _ in range(90):
            x = random.uniform(margin, width - margin)
            y = random.uniform(margin, height - margin)

            valid = True
            for player in arena['players'].values():
                body = player.get('slither_segments') or []
                if not body:
                    continue
                head = body[0]
                if (head['x'] - x) ** 2 + (head['y'] - y) ** 2 < (140.0 ** 2):
                    valid = False
                    break
            if valid:
                return (x, y)

        return (width * 0.5, height * 0.5)

    def _build_segments(self, head_x: float, head_y: float, direction: Tuple[float, float], length: int) -> List[Dict]:
        dx, dy = direction
        spacing = 10.0
        segment_count = max(self.SNAKE_MIN_LENGTH, int(length))
        return [
            {
                'x': head_x - (dx * spacing * i),
                'y': head_y - (dy * spacing * i),
            }
            for i in range(segment_count)
        ]

    def _color_for_player(self, arena: Dict) -> str:
        used = {p.get('color') for p in arena['players'].values()}
        for color in self.COLORS:
            if color not in used:
                return color
        return random.choice(self.COLORS)

    def _respawn_player(self, arena: Dict, player: Dict, keep_score: bool = True) -> None:
        if not keep_score:
            player['score'] = 0
            player['kills'] = 0
            player['deaths'] = 0

        player['status'] = 'alive'
        player['hp'] = self.MAX_HP
        player['max_hp'] = self.MAX_HP
        player['shooting'] = False
        player['spawn_protect_until'] = time.time() + self.SPAWN_PROTECT_SECONDS

        base_dir = player.get('direction') or {'x': 1, 'y': 0}
        direction = self._normalize_direction(base_dir)
        player['direction'] = {'x': direction[0], 'y': direction[1]}
        player['pending_direction'] = {'x': direction[0], 'y': direction[1]}

        sx, sy = self._random_spawn(arena)
        player['slither_segments'] = self._build_segments(sx, sy, direction, int(player.get('length', self.SNAKE_START_LENGTH)))

    def _spawn_bullet(self, arena: Dict, owner: Dict) -> None:
        bullets = arena.get('bullets')
        if bullets is None:
            bullets = []
            arena['bullets'] = bullets
        if len(bullets) >= self.MAX_BULLETS_PER_ARENA:
            return

        body = owner.get('slither_segments') or []
        if not body:
            return

        head = body[0]
        direction = owner.get('direction') or {'x': 1, 'y': 0}
        dx, dy = self._normalize_direction(direction)

        bullets.append({
            'id': self._new_bullet_id(),
            'owner_id': owner['player_id'],
            'x': float(head['x'] + (dx * (self.HEAD_RADIUS + 6.0))),
            'y': float(head['y'] + (dy * (self.HEAD_RADIUS + 6.0))),
            'vx': float(dx * self.BULLET_SPEED),
            'vy': float(dy * self.BULLET_SPEED),
            'ttl': float(self.BULLET_LIFETIME),
        })

    # --------------------------- Public operations ---------------------------

    def join_player(self, sid: str, payload: Optional[Dict], user_id: Optional[int], username_fallback: str) -> Dict:
        payload = payload or {}
        requested_name = str(payload.get('username') or '').strip()
        username = (requested_name or username_fallback or 'Guest')[:24]

        party_id = payload.get('party_id')
        if party_id is not None:
            party_id = str(party_id).strip()[:64] or None

        existing_arena_id = self.sid_to_arena.get(sid)
        if existing_arena_id:
            existing_arena = self.arenas.get(existing_arena_id)
            if existing_arena and sid in existing_arena['players']:
                player = existing_arena['players'][sid]
                player['username'] = username
                player['party_id'] = party_id
                if user_id:
                    player['user_id'] = user_id
                if player.get('status') != 'alive':
                    self._respawn_player(existing_arena, player, keep_score=True)
                return {
                    'arena_id': existing_arena['arena_id'],
                    'role': 'player',
                    'player_id': sid,
                }

        arena = self._select_arena_for_join(party_id)
        if party_id:
            self.party_to_arena[party_id] = arena['arena_id']

        direction = self._normalize_direction(payload.get('direction'))
        player = {
            'player_id': sid,
            'sid': sid,
            'user_id': user_id,
            'username': username,
            'party_id': party_id,
            'color': self._color_for_player(arena),
            'direction': {'x': direction[0], 'y': direction[1]},
            'pending_direction': {'x': direction[0], 'y': direction[1]},
            'length': float(self.SNAKE_START_LENGTH),
            'speed': float(self.PLAYER_SPEED),
            'score': 0,
            'kills': 0,
            'deaths': 0,
            'hp': self.MAX_HP,
            'max_hp': self.MAX_HP,
            'status': 'alive',
            'shooting': False,
            'last_fire_at': 0.0,
            'spawn_protect_until': 0.0,
            'slither_segments': [],
            'joined_at': time.time(),
            'last_input_at': time.time(),
        }

        self._respawn_player(arena, player, keep_score=True)
        arena['players'][sid] = player
        self.sid_to_arena[sid] = arena['arena_id']

        return {
            'arena_id': arena['arena_id'],
            'role': 'player',
            'player_id': sid,
        }

    def leave_player(self, sid: str) -> Optional[Dict]:
        arena_id = self.sid_to_arena.pop(sid, None)
        if not arena_id:
            return None

        arena = self.arenas.get(arena_id)
        if not arena:
            return None

        player = arena['players'].pop(sid, None)
        if not player:
            return None

        self._cleanup_party_mapping(player.get('party_id'), arena_id)

        # Remove bullets owned by disconnected player.
        arena['bullets'] = [
            b for b in (arena.get('bullets') or [])
            if b.get('owner_id') != sid
        ]

        if not arena['players']:
            self.arenas.pop(arena_id, None)

        return {'arena': arena, 'player': player}

    def set_player_ready(self, sid: str) -> Optional[Dict]:
        arena_id = self.sid_to_arena.get(sid)
        if not arena_id:
            return None

        arena = self.arenas.get(arena_id)
        if not arena:
            return None

        player = arena['players'].get(sid)
        if not player:
            return None

        self._respawn_player(arena, player, keep_score=True)
        return arena

    def update_player_input(self, sid: str, payload: Optional[Dict]) -> None:
        payload = payload or {}

        arena_id = self.sid_to_arena.get(sid)
        if not arena_id:
            return

        arena = self.arenas.get(arena_id)
        if not arena:
            return

        player = arena['players'].get(sid)
        if not player or player.get('status') != 'alive':
            return

        cur = player.get('direction') or {'x': 1, 'y': 0}
        ndir = self._normalize_direction(payload.get('direction'), fallback=(cur['x'], cur['y']))
        player['pending_direction'] = {'x': ndir[0], 'y': ndir[1]}

        # Keep backward compatibility with older client payloads using `boost`.
        shoot = bool(payload.get('shoot') or payload.get('fire') or payload.get('boost'))
        player['shooting'] = shoot
        player['last_input_at'] = time.time()

    # ---------------------------- Serialization -----------------------------

    def _serialize_body(self, body: List[Dict]) -> List[Dict]:
        return [
            {'x': round(point['x'], 1), 'y': round(point['y'], 1)}
            for point in (body or [])
        ]

    def _leaderboard(self, arena: Dict) -> List[Dict]:
        rows = []
        for player in arena['players'].values():
            rows.append({
                'id': player['player_id'],
                'username': player.get('username', 'Player'),
                'score': int(player.get('score', 0)),
                'length': int(max(0, round(player.get('length', self.SNAKE_START_LENGTH)))),
                'kills': int(player.get('kills', 0)),
                'status': player.get('status', 'alive'),
            })

        rows.sort(key=lambda row: (row['score'], row['kills'], row['length']), reverse=True)
        return rows[:5]

    def _alive_count_for_payload(self, arena: Dict) -> int:
        return sum(1 for player in arena['players'].values() if player.get('status') == 'alive')

    def serialize_state_for_sid(self, arena: Dict, sid: str, now: float) -> Dict:
        players_payload = []
        for player in arena['players'].values():
            body = player.get('slither_segments') or []
            head = body[0] if body else None
            players_payload.append({
                'id': player['player_id'],
                'username': player.get('username', 'Player'),
                'head': ({'x': round(head['x'], 1), 'y': round(head['y'], 1)} if head else None),
                'body': self._serialize_body(body),
                'length': int(max(0, round(player.get('length', self.SNAKE_START_LENGTH)))),
                'score': int(player.get('score', 0)),
                'kills': int(player.get('kills', 0)),
                'deaths': int(player.get('deaths', 0)),
                'hp': int(player.get('hp', self.MAX_HP)),
                'max_hp': int(player.get('max_hp', self.MAX_HP)),
                'status': player.get('status', 'alive'),
                'boost_active': False,
                'spectating': None,
                'color': player.get('color'),
            })

        bullets_payload = [
            {
                'x': round(float(bullet.get('x', 0.0)), 1),
                'y': round(float(bullet.get('y', 0.0)), 1),
                'owner_id': bullet.get('owner_id'),
            }
            for bullet in (arena.get('bullets') or [])
        ]

        return {
            'arena_id': arena['arena_id'],
            'self_id': sid,
            'state': 'active',
            'bounds': arena['bounds'],
            'tick_rate': arena['tick_rate'],
            'players': players_payload,
            'bullets': bullets_payload,
            'energy_orbs': [],
            'alive_count': self._alive_count_for_payload(arena),
            'leaderboard': self._leaderboard(arena),
            'countdown': 0,
            'time_left': 0,
        }

    # ------------------------------ Tick logic ------------------------------

    def _step_move_players(self, arena: Dict, now: float, dt: float) -> None:
        bounds = arena['bounds']
        min_x = self.HEAD_RADIUS
        min_y = self.HEAD_RADIUS
        max_x = float(bounds['width']) - self.HEAD_RADIUS
        max_y = float(bounds['height']) - self.HEAD_RADIUS

        for player in arena['players'].values():
            if player.get('status') != 'alive':
                continue

            pending = player.get('pending_direction') or player.get('direction') or {'x': 1, 'y': 0}
            dx, dy = self._normalize_direction(pending)
            player['direction'] = {'x': dx, 'y': dy}

            body = player.get('slither_segments') or []
            if not body:
                self._respawn_player(arena, player, keep_score=True)
                body = player.get('slither_segments') or []
                if not body:
                    continue

            head = body[0]
            nx = max(min_x, min(max_x, float(head['x']) + (dx * self.PLAYER_SPEED * dt)))
            ny = max(min_y, min(max_y, float(head['y']) + (dy * self.PLAYER_SPEED * dt)))

            body.insert(0, {'x': nx, 'y': ny})

            target_len = int(max(self.SNAKE_MIN_LENGTH, min(self.SNAKE_MAX_LENGTH, round(player.get('length', self.SNAKE_START_LENGTH)))))
            while len(body) > target_len:
                body.pop()
            while len(body) < target_len and body:
                body.append({'x': body[-1]['x'], 'y': body[-1]['y']})

            if player.get('shooting') and now - float(player.get('last_fire_at', 0.0)) >= self.FIRE_COOLDOWN_SECONDS:
                player['last_fire_at'] = now
                self._spawn_bullet(arena, player)

    def _step_bullets(self, arena: Dict, now: float, dt: float) -> None:
        kept = []
        players = arena['players']
        bounds = arena['bounds']
        hit_radius_sq = (self.HEAD_RADIUS + self.BULLET_RADIUS) ** 2

        for bullet in arena.get('bullets') or []:
            bx = float(bullet.get('x', 0.0)) + (float(bullet.get('vx', 0.0)) * dt)
            by = float(bullet.get('y', 0.0)) + (float(bullet.get('vy', 0.0)) * dt)
            ttl = float(bullet.get('ttl', 0.0)) - dt

            if ttl <= 0:
                continue
            if bx < 0 or by < 0 or bx > bounds['width'] or by > bounds['height']:
                continue

            bullet['x'] = bx
            bullet['y'] = by
            bullet['ttl'] = ttl

            owner_id = bullet.get('owner_id')
            hit_target = None
            for target in players.values():
                if target.get('status') != 'alive':
                    continue
                if target.get('player_id') == owner_id:
                    continue
                if now < float(target.get('spawn_protect_until', 0.0)):
                    continue

                body = target.get('slither_segments') or []
                if not body:
                    continue
                head = body[0]
                if (head['x'] - bx) ** 2 + (head['y'] - by) ** 2 <= hit_radius_sq:
                    hit_target = target
                    break

            if not hit_target:
                kept.append(bullet)
                continue

            hit_target['hp'] = int(hit_target.get('hp', self.MAX_HP) - 1)
            if hit_target['hp'] > 0:
                continue

            hit_target['deaths'] = int(hit_target.get('deaths', 0) + 1)
            hit_target['score'] = max(0, int(hit_target.get('score', 0) - 1))
            hit_target['length'] = float(max(self.SNAKE_MIN_LENGTH, int(hit_target.get('length', self.SNAKE_START_LENGTH)) - 2))

            owner = players.get(owner_id)
            if owner and owner.get('player_id') != hit_target.get('player_id'):
                owner['kills'] = int(owner.get('kills', 0) + 1)
                owner['score'] = int(owner.get('score', 0) + 1)
                owner['length'] = float(min(self.SNAKE_MAX_LENGTH, int(owner.get('length', self.SNAKE_START_LENGTH)) + 2))

            killer_id = owner_id if owner else None
            self._respawn_player(arena, hit_target, keep_score=True)

            self.socketio.emit(
                'slitherrush_death',
                {
                    'player_id': hit_target.get('player_id'),
                    'killer_id': killer_id,
                    'reason': 'shot',
                },
                room=arena['room'],
            )

        arena['bullets'] = kept

    def tick(self, now: float, dt: float) -> None:
        for arena in list(self.arenas.values()):
            self._step_move_players(arena, now, dt)
            self._step_bullets(arena, now, dt)

            if not arena['players']:
                self.arenas.pop(arena['arena_id'], None)

    # ---------------------------- Emit operations ----------------------------

    def emit_state(self, arena: Dict, now: float) -> None:
        for sid in list(arena['players'].keys()):
            payload = self.serialize_state_for_sid(arena, sid, now)
            self.socketio.emit('slitherrush_state', payload, to=sid)

    def emit_leaderboard(self, arena: Dict) -> None:
        payload = {'leaderboard': self._leaderboard(arena)}
        self.socketio.emit('slitherrush_leaderboard_update', payload, room=arena['room'])

    def emit_status_snapshot(self, to_sid: Optional[str] = None) -> Dict:
        total_players = sum(len(arena['players']) for arena in self.arenas.values())
        active_rooms = sum(1 for arena in self.arenas.values() if arena['players'])
        open_slots = sum(max(0, arena['max_players'] - len(arena['players'])) for arena in self.arenas.values())
        if open_slots <= 0:
            open_slots = self.MAX_PLAYERS_PER_ARENA

        payload = {
            'totalPlayers': int(total_players),
            'activeRooms': int(active_rooms),
            'openSlots': int(open_slots),
        }

        if to_sid:
            self.socketio.emit('slitherrush_status', payload, to=to_sid)
        else:
            self.socketio.emit('slitherrush_status', payload)
        return payload
