import math
import random
import time
import threading
from typing import Dict, List, Optional, Tuple

from __init__ import app, db
from model.boss_room import SlitherRushStats


class SlitherRushManager:
    ROOM_PREFIX = 'slitherrush_arena'

    TICK_RATE = 25
    TICK_INTERVAL = 1.0 / TICK_RATE
    SNAPSHOT_INTERVAL = 1.0 / 12.0
    LEADERBOARD_INTERVAL = 0.5

    WIDTH = 4800
    HEIGHT = 3000

    MAX_PLAYERS_PER_ARENA = 24
    MIN_PLAYERS_TO_START = 1
    WAITING_COUNTDOWN = 0.0
    MATCH_DURATION = 300.0
    ENDING_DURATION = 12.0

    START_LENGTH = 16
    MIN_LENGTH = 8
    BASE_SPEED = 175.0
    BOOST_MULTIPLIER = 1.55
    BOOST_BURN_PER_SECOND = 4.2
    BOOST_MINI_ORB_INTERVAL = 0.14

    HEAD_RADIUS = 11.0
    ORB_PICKUP_RADIUS = 19.0
    MIN_ORBS_PER_ARENA = 320
    MAX_ORBS_PER_ARENA = 1200

    KILL_BONUS = 25
    SURVIVAL_BONUS_PER_SECOND = 1

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
        self._orb_counter = 1
        self.loop_started = False

    # ----------------------------- Arena helpers -----------------------------

    def _new_arena_id(self) -> str:
        arena_id = str(self._arena_counter)
        self._arena_counter += 1
        return arena_id

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
            'countdown_end_at': None,
            'match_end_at': None,
            'ending_end_at': None,
            'end_reason': None,
            'players': {},
            'energy_orbs': [],
            'ready_players': set(),
            'party_targets': {},
            'last_snapshot_at': 0.0,
            'last_leaderboard_at': 0.0,
            'stats_persisted': False,
            'results': [],
        }
        self._ensure_orb_floor(arena)
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

        still_in_party = any(
            p.get('party_id') == party_id
            for p in arena['players'].values()
        )
        if not still_in_party:
            self.party_to_arena.pop(party_id, None)

    def _select_arena_for_join(self, party_id: Optional[str]) -> Dict:
        if party_id:
            arena_id = self.party_to_arena.get(party_id)
            if arena_id and arena_id in self.arenas:
                arena = self.arenas[arena_id]
                if len(arena['players']) < arena['max_players']:
                    return arena

        waiting_candidates = [
            a for a in self.arenas.values()
            if a['state'] == 'waiting' and len(a['players']) < a['max_players']
        ]
        if waiting_candidates:
            waiting_candidates.sort(key=lambda x: x['created_at'])
            return waiting_candidates[0]

        active_candidates = [
            a for a in self.arenas.values()
            if a['state'] in ('active', 'ending') and len(a['players']) < a['max_players']
        ]
        if active_candidates:
            active_candidates.sort(key=lambda x: x['created_at'])
            return active_candidates[0]

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
        width = arena['bounds']['width']
        height = arena['bounds']['height']
        margin = 120
        attempts = 64
        for _ in range(attempts):
            x = random.uniform(margin, width - margin)
            y = random.uniform(margin, height - margin)
            ok = True
            for p in arena['players'].values():
                body = p.get('slither_segments') or []
                if not body:
                    continue
                hx = body[0]['x']
                hy = body[0]['y']
                if math.hypot(x - hx, y - hy) < 160:
                    ok = False
                    break
            if ok:
                return x, y
        return (width / 2.0, height / 2.0)

    def _build_segments(self, head_x: float, head_y: float, direction: Tuple[float, float], length: int) -> List[Dict]:
        # Tail extends opposite movement direction.
        dx, dy = direction
        spacing = 10.0
        segments = []
        for i in range(max(3, int(length))):
            segments.append({
                'x': head_x - (dx * spacing * i),
                'y': head_y - (dy * spacing * i),
            })
        return segments

    def _color_for_player(self, arena: Dict) -> str:
        used = {p.get('color') for p in arena['players'].values()}
        for c in self.COLORS:
            if c not in used:
                return c
        return random.choice(self.COLORS)

    def _assign_player_state_for_new_match(self, arena: Dict, player: Dict) -> None:
        player['ready'] = False
        player['status'] = 'alive'
        player['boost_active'] = False
        player['boost_burn_accum'] = 0.0
        player['boost_drop_accum'] = 0.0
        player['score'] = 0
        player['kills'] = 0
        player['orbs_collected'] = 0
        player['length'] = float(self.START_LENGTH)
        player['survival_started_at'] = time.time()
        player['died_at'] = None
        player['spectating'] = None

        direction = self._normalize_direction(player.get('direction') or {'x': 1, 'y': 0})
        player['direction'] = {'x': direction[0], 'y': direction[1]}

        sx, sy = self._random_spawn(arena)
        player['slither_segments'] = self._build_segments(sx, sy, direction, self.START_LENGTH)

    def _assign_spectator_state(self, player: Dict) -> None:
        player['status'] = 'spectator'
        player['boost_active'] = False
        player['ready'] = False
        player['boost_burn_accum'] = 0.0
        player['boost_drop_accum'] = 0.0
        player['slither_segments'] = []

    def _alive_players(self, arena: Dict) -> List[Dict]:
        return [p for p in arena['players'].values() if p.get('status') == 'alive']

    def _ready_players(self, arena: Dict) -> List[Dict]:
        return [p for p in arena['players'].values() if p.get('ready')]

    def _find_player(self, arena: Dict, sid: str) -> Optional[Dict]:
        return arena['players'].get(sid)

    # ----------------------------- Orb helpers -------------------------------

    def _next_orb_id(self) -> str:
        oid = f"orb_{self._orb_counter}"
        self._orb_counter += 1
        return oid

    def _spawn_orb(self, arena: Dict, x: Optional[float] = None, y: Optional[float] = None, value: int = 1) -> None:
        if len(arena['energy_orbs']) >= self.MAX_ORBS_PER_ARENA:
            return
        width = arena['bounds']['width']
        height = arena['bounds']['height']
        if x is None:
            x = random.uniform(20, width - 20)
        if y is None:
            y = random.uniform(20, height - 20)
        arena['energy_orbs'].append({
            'id': self._next_orb_id(),
            'x': float(max(8.0, min(width - 8.0, x))),
            'y': float(max(8.0, min(height - 8.0, y))),
            'value': int(max(1, min(6, value))),
        })

    def _ensure_orb_floor(self, arena: Dict) -> None:
        target = self.MIN_ORBS_PER_ARENA
        deficit = max(0, target - len(arena['energy_orbs']))
        spawn_budget = min(deficit, max(12, min(48, int(deficit * 0.22))))
        for _ in range(spawn_budget):
            self._spawn_orb(arena, value=1)

    def _drop_death_orbs(self, arena: Dict, player: Dict) -> None:
        body = player.get('slither_segments') or []
        if not body:
            return
        # Convert body into a trail of higher-value orbs.
        step = 2
        for i in range(0, len(body), step):
            pt = body[i]
            self._spawn_orb(arena, pt['x'], pt['y'], value=3)

    # --------------------------- Arena lifecycle -----------------------------

    def _maybe_start_countdown(self, arena: Dict, now: float) -> None:
        ready_count = len(self._ready_players(arena))
        if ready_count <= 0:
            arena['countdown_end_at'] = None
            return
        if arena['countdown_end_at'] is None:
            arena['countdown_end_at'] = now + self.WAITING_COUNTDOWN

    def _start_match(self, arena: Dict, now: float) -> None:
        entrants = self._ready_players(arena)
        if not entrants:
            return

        arena['state'] = 'active'
        arena['match_end_at'] = now + self.MATCH_DURATION
        arena['countdown_end_at'] = None
        arena['end_reason'] = None
        arena['results'] = []
        arena['stats_persisted'] = False

        for p in entrants:
            self._assign_player_state_for_new_match(arena, p)
        for p in arena['players'].values():
            if p not in entrants:
                self._assign_spectator_state(p)

    def _finalize_results(self, arena: Dict) -> List[Dict]:
        entries = []
        for p in arena['players'].values():
            survival = 0
            if p.get('survival_started_at'):
                end_t = p.get('died_at') or time.time()
                survival = max(0, int(end_t - p['survival_started_at']))
            bonus = survival * self.SURVIVAL_BONUS_PER_SECOND
            final_score = int(p.get('score', 0) + bonus)
            p['final_score'] = final_score
            entries.append({
                'player_id': p['player_id'],
                'user_id': p.get('user_id'),
                'username': p.get('username', 'Player'),
                'status': p.get('status'),
                'survival_seconds': survival,
                'survival_bonus': bonus,
                'length': int(max(0, round(p.get('length', 0)))),
                'score': final_score,
                'kills': int(p.get('kills', 0)),
                'orbs_collected': int(p.get('orbs_collected', 0)),
            })

        # Winner ranking: survival, then length, then score.
        def rank_key(item):
            survived = 1 if item.get('status') == 'alive' else 0
            return (survived, item.get('length', 0), item.get('score', 0), item.get('kills', 0))

        entries.sort(key=rank_key, reverse=True)
        for idx, item in enumerate(entries, start=1):
            item['rank'] = idx
        arena['results'] = entries
        return entries

    def _persist_match_stats(self, arena: Dict) -> None:
        if arena.get('stats_persisted'):
            return
        results = arena.get('results') or []
        if not results:
            return

        winners = {r['player_id'] for r in results if r.get('rank') == 1}

        try:
            with app.app_context():
                changed = False
                for r in results:
                    uid = r.get('user_id')
                    if not uid:
                        continue

                    stat = SlitherRushStats.query.filter_by(user_id=uid).first()
                    if not stat:
                        stat = SlitherRushStats(user_id=uid, username=r.get('username', 'Player'))
                        db.session.add(stat)

                    stat.username = r.get('username', stat.username)
                    stat.matches_played = int((stat.matches_played or 0) + 1)
                    if r.get('player_id') in winners:
                        stat.matches_won = int((stat.matches_won or 0) + 1)
                    stat.total_score = int((stat.total_score or 0) + int(r.get('score', 0)))
                    stat.total_kills = int((stat.total_kills or 0) + int(r.get('kills', 0)))
                    stat.total_orbs = int((stat.total_orbs or 0) + int(r.get('orbs_collected', 0)))
                    stat.total_survival_seconds = int((stat.total_survival_seconds or 0) + int(r.get('survival_seconds', 0)))
                    stat.best_length = max(int(stat.best_length or 0), int(r.get('length', 0)))
                    stat.best_score = max(int(stat.best_score or 0), int(r.get('score', 0)))
                    stat.last_played_at = time.time()
                    changed = True

                if changed:
                    db.session.commit()
        except Exception:
            db.session.rollback()
            return

        arena['stats_persisted'] = True

    def _end_match(self, arena: Dict, reason: str, now: float) -> None:
        if arena['state'] != 'active':
            return
        arena['state'] = 'ending'
        arena['ending_end_at'] = now + self.ENDING_DURATION
        arena['match_end_at'] = None
        arena['end_reason'] = reason

        results = self._finalize_results(arena)
        self._persist_match_stats(arena)

        payload = {
            'arena_id': arena['arena_id'],
            'reason': reason,
            'results': results,
        }
        self.socketio.emit('slitherrush_end', payload, room=arena['room'])

    def _reset_after_ending(self, arena: Dict) -> None:
        arena['state'] = 'active'
        arena['countdown_end_at'] = None
        arena['ending_end_at'] = None
        arena['end_reason'] = None
        arena['results'] = []
        arena['match_end_at'] = None
        arena['energy_orbs'].clear()
        arena['party_targets'].clear()
        arena['ready_players'].clear()
        for p in arena['players'].values():
            p['ready'] = False
            self._assign_spectator_state(p)
        self._ensure_orb_floor(arena)

    # --------------------------- Public operations ---------------------------

    def join_player(self, sid: str, payload: Optional[Dict], user_id: Optional[int], username_fallback: str) -> Dict:
        payload = payload or {}
        requested_name = str(payload.get('username') or '').strip()
        username = (requested_name or username_fallback or 'Guest')[:24]

        party_id = payload.get('party_id')
        if party_id is not None:
            party_id = str(party_id).strip()[:64] or None

        if sid in self.sid_to_arena:
            existing_arena = self.arenas.get(self.sid_to_arena[sid])
            if existing_arena and sid in existing_arena['players']:
                player = existing_arena['players'][sid]
                player['username'] = username
                player['party_id'] = party_id
                if user_id:
                    player['user_id'] = user_id
                return {
                    'arena_id': existing_arena['arena_id'],
                    'role': player.get('status', 'spectator'),
                    'player_id': sid,
                }

        arena = self._select_arena_for_join(party_id)
        if party_id:
            self.party_to_arena[party_id] = arena['arena_id']

        role = 'spectator'
        ready = False
        join_live = len(arena['players']) < arena['max_players'] and arena['state'] in ('waiting', 'active')
        if join_live:
            if arena['state'] == 'waiting':
                role = 'queued'
                ready = True
            else:
                role = 'player'

        direction = self._normalize_direction(payload.get('direction'))
        sx, sy = self._random_spawn(arena)
        segments = self._build_segments(sx, sy, direction, self.START_LENGTH)

        player = {
            'player_id': sid,
            'sid': sid,
            'user_id': user_id,
            'username': username,
            'party_id': party_id,
            'color': self._color_for_player(arena),
            'direction': {'x': direction[0], 'y': direction[1]},
            'pending_direction': {'x': direction[0], 'y': direction[1]},
            'speed': self.BASE_SPEED,
            'length': float(self.START_LENGTH),
            'score': 0,
            'kills': 0,
            'orbs_collected': 0,
            'status': 'spectator',
            'ready': ready,
            'boost_active': False,
            'boost_burn_accum': 0.0,
            'boost_drop_accum': 0.0,
            'slither_segments': segments,
            'joined_at': time.time(),
            'survival_started_at': None,
            'died_at': None,
            'spectating': None,
            'last_input_at': time.time(),
        }

        if arena['state'] == 'active' and join_live:
            self._assign_player_state_for_new_match(arena, player)
        elif not ready:
            self._assign_spectator_state(player)

        arena['players'][sid] = player
        self.sid_to_arena[sid] = arena['arena_id']

        if ready:
            arena['ready_players'].add(sid)

        return {
            'arena_id': arena['arena_id'],
            'role': 'spectator' if player['status'] == 'spectator' and not ready else 'player',
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
        arena['ready_players'].discard(sid)
        if not player:
            return None

        self._cleanup_party_mapping(player.get('party_id'), arena_id)

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

        if arena['state'] == 'active':
            if player.get('status') != 'alive':
                self._assign_player_state_for_new_match(arena, player)
                player['ready'] = False
            return arena

        if arena['state'] != 'waiting':
            return arena

        player['ready'] = True
        arena['ready_players'].add(sid)
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
        if not player:
            return
        if player.get('status') != 'alive':
            return

        ndir = self._normalize_direction(payload.get('direction'), fallback=(player['direction']['x'], player['direction']['y']))
        cur = player['direction']
        dot = (ndir[0] * cur['x']) + (ndir[1] * cur['y'])
        # Prevent immediate 180 to avoid body fold-through.
        if dot > -0.85:
            player['pending_direction'] = {'x': ndir[0], 'y': ndir[1]}

        player['boost_active'] = bool(payload.get('boost', False))
        player['last_input_at'] = time.time()

    # ---------------------------- Serialization -----------------------------

    def _serialize_body(self, body: List[Dict]) -> List[Dict]:
        if len(body) <= 80:
            return [{'x': round(p['x'], 1), 'y': round(p['y'], 1)} for p in body]
        # Keep head detail, sample mid/tail for compact payloads.
        sampled = body[:30]
        sampled.extend(body[30::2])
        sampled = sampled[:80]
        return [{'x': round(p['x'], 1), 'y': round(p['y'], 1)} for p in sampled]

    def _leaderboard(self, arena: Dict) -> List[Dict]:
        rows = []
        for p in arena['players'].values():
            rows.append({
                'id': p['player_id'],
                'username': p.get('username', 'Player'),
                'score': int(p.get('score', 0)),
                'length': int(max(0, round(p.get('length', 0)))),
                'kills': int(p.get('kills', 0)),
                'status': p.get('status', 'spectator'),
            })
        rows.sort(key=lambda r: (r['status'] == 'alive', r['score'], r['length'], r['kills']), reverse=True)
        return rows[:5]

    def _alive_count_for_payload(self, arena: Dict) -> int:
        if arena['state'] == 'active':
            return len(self._alive_players(arena))
        return len(self._ready_players(arena))

    def serialize_state_for_sid(self, arena: Dict, sid: str, now: float) -> Dict:
        players_payload = []
        for p in arena['players'].values():
            body = p.get('slither_segments') or []
            head = body[0] if body else None
            players_payload.append({
                'id': p['player_id'],
                'username': p.get('username', 'Player'),
                'head': ({'x': round(head['x'], 1), 'y': round(head['y'], 1)} if head else None),
                'body': self._serialize_body(body) if body else [],
                'length': int(max(0, round(p.get('length', 0)))),
                'score': int(p.get('score', 0)),
                'kills': int(p.get('kills', 0)),
                'status': p.get('status', 'spectator'),
                'boost_active': bool(p.get('boost_active', False)),
                'spectating': p.get('spectating'),
                'color': p.get('color'),
            })

        countdown = 0
        if arena['state'] == 'waiting' and arena.get('countdown_end_at'):
            countdown = max(0, int(math.ceil(arena['countdown_end_at'] - now)))

        time_left = 0
        if arena['state'] == 'active' and arena.get('match_end_at'):
            time_left = max(0, int(math.ceil(arena['match_end_at'] - now)))

        payload = {
            'arena_id': arena['arena_id'],
            'self_id': sid,
            'state': arena['state'],
            'bounds': arena['bounds'],
            'tick_rate': arena['tick_rate'],
            'players': players_payload,
            'energy_orbs': [
                {'x': round(o['x'], 1), 'y': round(o['y'], 1), 'value': int(o.get('value', 1))}
                for o in arena['energy_orbs']
            ],
            'alive_count': self._alive_count_for_payload(arena),
            'leaderboard': self._leaderboard(arena),
            'countdown': countdown,
            'time_left': time_left,
        }
        return payload

    # ------------------------------ Tick logic ------------------------------

    def _apply_boost(self, arena: Dict, player: Dict, dt: float) -> float:
        speed = self.BASE_SPEED
        if not player.get('boost_active'):
            return speed

        if player.get('length', 0) <= self.MIN_LENGTH:
            player['boost_active'] = False
            return speed

        speed *= self.BOOST_MULTIPLIER
        player['boost_burn_accum'] += self.BOOST_BURN_PER_SECOND * dt
        player['boost_drop_accum'] += dt

        while player['boost_burn_accum'] >= 1.0 and player['length'] > self.MIN_LENGTH:
            player['boost_burn_accum'] -= 1.0
            player['length'] -= 1.0
            body = player.get('slither_segments') or []
            if body:
                tail = body[-1]
                self._spawn_orb(arena, tail['x'], tail['y'], value=1)

        if player['boost_drop_accum'] >= self.BOOST_MINI_ORB_INTERVAL:
            player['boost_drop_accum'] = 0.0
            body = player.get('slither_segments') or []
            if body:
                tail = body[-1]
                self._spawn_orb(arena, tail['x'], tail['y'], value=1)

        return speed

    def _step_move_players(self, arena: Dict, dt: float) -> List[Tuple[str, str, Optional[str]]]:
        deaths: List[Tuple[str, str, Optional[str]]] = []  # (victim, reason, killer)
        width = arena['bounds']['width']
        height = arena['bounds']['height']

        alive_players = self._alive_players(arena)
        alive_by_id = {p['player_id']: p for p in alive_players}

        # 1) Move heads and shift body arrays.
        for p in alive_players:
            ndir = p.get('pending_direction') or p.get('direction')
            p['direction'] = {'x': ndir['x'], 'y': ndir['y']}

            speed = self._apply_boost(arena, p, dt)
            body = p.get('slither_segments') or []
            if not body:
                continue
            head = body[0]
            nx = head['x'] + p['direction']['x'] * speed * dt
            ny = head['y'] + p['direction']['y'] * speed * dt
            body.insert(0, {'x': nx, 'y': ny})

            target_len = max(self.MIN_LENGTH, int(round(p.get('length', self.START_LENGTH))))
            while len(body) > target_len:
                body.pop()
            while len(body) < target_len and body:
                body.append({'x': body[-1]['x'], 'y': body[-1]['y']})

            if nx <= 0 or ny <= 0 or nx >= width or ny >= height:
                deaths.append((p['player_id'], 'wall', None))

        alive_players = self._alive_players(arena)
        alive_by_id = {p['player_id']: p for p in alive_players}

        # 2) Head to body collisions.
        for p in alive_players:
            body = p.get('slither_segments') or []
            if not body:
                continue
            head = body[0]
            hx, hy = head['x'], head['y']
            for other in alive_players:
                if other['player_id'] == p['player_id']:
                    continue
                other_body = other.get('slither_segments') or []
                # Skip very first points so head-to-head is resolved separately.
                for seg in other_body[2:]:
                    if (hx - seg['x']) ** 2 + (hy - seg['y']) ** 2 <= (self.HEAD_RADIUS ** 2):
                        deaths.append((p['player_id'], 'body', other['player_id']))
                        break
                else:
                    continue
                break

        # 3) Head to head collisions.
        alive_players = self._alive_players(arena)
        for i in range(len(alive_players)):
            a = alive_players[i]
            ab = a.get('slither_segments') or []
            if not ab:
                continue
            ah = ab[0]
            for j in range(i + 1, len(alive_players)):
                b = alive_players[j]
                bb = b.get('slither_segments') or []
                if not bb:
                    continue
                bh = bb[0]
                if (ah['x'] - bh['x']) ** 2 + (ah['y'] - bh['y']) ** 2 > ((self.HEAD_RADIUS * 2.0) ** 2):
                    continue

                la = a.get('length', 0)
                lb = b.get('length', 0)
                if la > lb:
                    deaths.append((b['player_id'], 'head_to_head', a['player_id']))
                elif lb > la:
                    deaths.append((a['player_id'], 'head_to_head', b['player_id']))
                else:
                    deaths.append((a['player_id'], 'head_to_head', None))
                    deaths.append((b['player_id'], 'head_to_head', None))

        return deaths

    def _resolve_deaths(self, arena: Dict, deaths: List[Tuple[str, str, Optional[str]]], now: float) -> None:
        if not deaths:
            return

        seen = set()
        unique = []
        for victim_id, reason, killer_id in deaths:
            if victim_id in seen:
                continue
            seen.add(victim_id)
            unique.append((victim_id, reason, killer_id))

        for victim_id, reason, killer_id in unique:
            victim = arena['players'].get(victim_id)
            if not victim or victim.get('status') != 'alive':
                continue

            if killer_id and killer_id in arena['players'] and killer_id != victim_id:
                killer = arena['players'][killer_id]
                killer['kills'] = int(killer.get('kills', 0) + 1)
                killer['score'] = int(killer.get('score', 0) + self.KILL_BONUS)

            victim['died_at'] = now
            self._drop_death_orbs(arena, victim)
            self._assign_spectator_state(victim)

            self.socketio.emit('slitherrush_death', {
                'player_id': victim_id,
                'killer_id': killer_id,
                'reason': reason,
            }, room=arena['room'])

    def _step_orb_pickups(self, arena: Dict) -> None:
        alive = self._alive_players(arena)
        if not alive or not arena['energy_orbs']:
            return

        kept = []
        for orb in arena['energy_orbs']:
            collected_by = None
            for p in alive:
                body = p.get('slither_segments') or []
                if not body:
                    continue
                head = body[0]
                if (head['x'] - orb['x']) ** 2 + (head['y'] - orb['y']) ** 2 <= (self.ORB_PICKUP_RADIUS ** 2):
                    collected_by = p
                    break

            if collected_by is None:
                kept.append(orb)
                continue

            value = int(max(1, orb.get('value', 1)))
            collected_by['score'] = int(collected_by.get('score', 0) + 1)
            collected_by['orbs_collected'] = int(collected_by.get('orbs_collected', 0) + 1)
            collected_by['length'] = float(collected_by.get('length', self.START_LENGTH) + max(1.0, value * 0.55))

        arena['energy_orbs'] = kept

    def _update_spectator_targets(self, arena: Dict) -> None:
        alive = self._alive_players(arena)
        alive_ids = [p['player_id'] for p in alive]
        if not alive_ids:
            for p in arena['players'].values():
                if p.get('status') == 'spectator':
                    p['spectating'] = None
            arena['party_targets'].clear()
            return

        alive_sorted = sorted(alive, key=lambda p: (p.get('length', 0), p.get('score', 0)), reverse=True)
        default_target = alive_sorted[0]['player_id']

        for p in arena['players'].values():
            if p.get('status') != 'spectator':
                continue

            party_id = p.get('party_id')
            target = p.get('spectating')

            if party_id:
                party_alive = [a for a in alive if a.get('party_id') == party_id]
                if party_alive:
                    target = party_alive[0]['player_id']
                    arena['party_targets'][party_id] = target
                else:
                    mapped = arena['party_targets'].get(party_id)
                    if mapped in alive_ids:
                        target = mapped
                    else:
                        target = default_target
                        arena['party_targets'][party_id] = target
            else:
                if target not in alive_ids:
                    target = default_target

            p['spectating'] = target

    def _step_waiting(self, arena: Dict, now: float) -> None:
        self._maybe_start_countdown(arena, now)

        ready_count = len(self._ready_players(arena))
        countdown_end_at = arena.get('countdown_end_at')

        should_start = False
        if ready_count >= self.MIN_PLAYERS_TO_START:
            should_start = True
        elif countdown_end_at is not None and ready_count >= 1 and now >= countdown_end_at:
            should_start = True

        if should_start:
            self._start_match(arena, now)

    def _step_active(self, arena: Dict, now: float, dt: float) -> None:
        deaths = self._step_move_players(arena, dt)
        self._resolve_deaths(arena, deaths, now)
        self._step_orb_pickups(arena)
        self._ensure_orb_floor(arena)
        self._update_spectator_targets(arena)

        # Endless slither mode: keep the arena running continuously.
        if arena.get('match_end_at') and arena['match_end_at'] > 0 and now >= arena['match_end_at']:
            self._end_match(arena, 'time_limit', now)

    def _step_ending(self, arena: Dict, now: float) -> None:
        self._update_spectator_targets(arena)
        if arena.get('ending_end_at') and now >= arena['ending_end_at']:
            self._reset_after_ending(arena)

    def tick(self, now: float, dt: float) -> None:
        for arena in list(self.arenas.values()):
            state = arena.get('state')
            if state == 'waiting':
                self._step_waiting(arena, now)
            elif state == 'active':
                self._step_active(arena, now, dt)
            elif state == 'ending':
                self._step_ending(arena, now)

            # Cleanup empty arenas.
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
        total_players = sum(len(a['players']) for a in self.arenas.values())
        active_rooms = sum(1 for a in self.arenas.values() if a['players'])
        open_slots = sum(max(0, a['max_players'] - len(a['players'])) for a in self.arenas.values())
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
