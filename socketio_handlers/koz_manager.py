import math
import random
import threading
import time
from typing import Dict, List, Optional, Tuple



def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))



def distance(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)



def circle_rect_intersects(cx: float, cy: float, radius: float, rect: Dict[str, float]) -> bool:
    rx = rect.get('x', 0.0)
    ry = rect.get('y', 0.0)
    rw = rect.get('w', 0.0)
    rh = rect.get('h', 0.0)
    nearest_x = clamp(cx, rx, rx + rw)
    nearest_y = clamp(cy, ry, ry + rh)
    dx = cx - nearest_x
    dy = cy - nearest_y
    return (dx * dx + dy * dy) <= (radius * radius)


class KozManager:
    ROOM_NAME = 'koz'

    TICK_INTERVAL = 1.0 / 30.0
    SNAPSHOT_INTERVAL = 1.0 / 15.0

    MAP_WIDTH = 4200
    MAP_HEIGHT = 2800
    PLAYER_RADIUS = 22

    MIN_PLAYERS_TO_START = 4
    MAX_ACTIVE_PLAYERS = 12

    COUNTDOWN_SECONDS = 10
    MATCH_DURATION_SECONDS = 180
    RESULTS_SECONDS = 12

    INITIAL_ZONE_RADIUS = 1260.0
    MIN_ZONE_RADIUS = 360.0
    SHRINK_INTERVAL_SECONDS = 24.0
    SHRINK_DURATION_SECONDS = 6.0

    STORM_DAMAGE_PER_TICK = 8
    STORM_TICK_SECONDS = 1.0

    RESPAWN_SECONDS = 3.0
    SCORE_TARGET = 70
    KILL_SCORE = 10
    CORE_SCORE_PER_SECOND = 1

    OVERCLOCK_CHARGE_PER_SECOND = 26.0
    OVERCLOCK_DURATION_SECONDS = 6.0

    MAX_POWERUPS = 6
    POWERUP_SPAWN_SECONDS = 7.0

    WEAPON_CONFIG = {
        'bulwark-disc': {
            'speed': 880.0,
            'damage': 23,
            'cooldown': 0.44,
            'radius': 7,
            'lifetime': 1.55,
            'splash': 0,
            'color': '#7ed3ff',
        },
        'arcane-orb': {
            'speed': 760.0,
            'damage': 29,
            'cooldown': 0.56,
            'radius': 9,
            'lifetime': 1.50,
            'splash': 70,
            'color': '#ffa76d',
        },
        'piercing-arrow': {
            'speed': 1080.0,
            'damage': 20,
            'cooldown': 0.33,
            'radius': 5,
            'lifetime': 1.30,
            'splash': 0,
            'color': '#87ffd5',
        },
        'rage-axe': {
            'speed': 700.0,
            'damage': 34,
            'cooldown': 0.60,
            'radius': 10,
            'lifetime': 1.35,
            'splash': 0,
            'color': '#ffcb6a',
        },
    }

    HERO_DEFAULT_WEAPON = {
        'knight': 'bulwark-disc',
        'wizard': 'arcane-orb',
        'archer': 'piercing-arrow',
        'warrior': 'rage-axe',
    }

    HERO_BASE_SPEED = {
        'knight': 312.0,
        'wizard': 302.0,
        'archer': 332.0,
        'warrior': 296.0,
    }

    OBSTACLES = [
        {'id': 'wall_tl', 'x': 820, 'y': 640, 'w': 680, 'h': 120},
        {'id': 'wall_tr', 'x': 2700, 'y': 640, 'w': 680, 'h': 120},
        {'id': 'wall_bl', 'x': 820, 'y': 2040, 'w': 680, 'h': 120},
        {'id': 'wall_br', 'x': 2700, 'y': 2040, 'w': 680, 'h': 120},
        {'id': 'pillar_l', 'x': 1490, 'y': 1150, 'w': 140, 'h': 500},
        {'id': 'pillar_r', 'x': 2570, 'y': 1150, 'w': 140, 'h': 500},
        {'id': 'mid_top', 'x': 1880, 'y': 840, 'w': 440, 'h': 110},
        {'id': 'mid_bot', 'x': 1880, 'y': 1850, 'w': 440, 'h': 110},
    ]

    SPAWN_POINTS = [
        (560, 560), (2100, 420), (3640, 560),
        (560, 1400), (3640, 1400),
        (560, 2240), (2100, 2380), (3640, 2240),
        (1180, 980), (3020, 980), (1180, 1820), (3020, 1820),
    ]

    POWERUP_SPAWNS = [
        (1050, 1050), (2100, 1040), (3150, 1050),
        (1050, 1760), (2100, 1760), (3150, 1760),
        (1570, 1400), (2630, 1400),
    ]

    POWERUP_TYPES = ['heal', 'speed', 'shield', 'damage', 'ammo']

    def __init__(self, socketio):
        self.socketio = socketio
        self.lock = threading.RLock()

        self.players: Dict[str, Dict] = {}

        self.state = 'LOBBY'
        self.countdown_end_at: Optional[float] = None
        self.results_end_at: Optional[float] = None

        self.match_end_time: Optional[float] = None
        self.time_left: float = float(self.MATCH_DURATION_SECONDS)

        self.zone = {
            'x': self.MAP_WIDTH / 2.0,
            'y': self.MAP_HEIGHT / 2.0,
            'radius': float(self.INITIAL_ZONE_RADIUS),
            'targetRadius': float(self.INITIAL_ZONE_RADIUS),
            'shrinkStart': 0.0,
            'shrinkEnd': 0.0,
            'nextShrinkAt': 0.0,
        }

        self.core = {
            'x': self.MAP_WIDTH / 2.0,
            'y': self.MAP_HEIGHT / 2.0,
            'radius': 20,
            'heldBy': None,
            'dropUnlockAt': 0.0,
        }

        self.projectiles: Dict[str, Dict] = {}
        self.projectile_seq = 0

        self.powerups: Dict[str, Dict] = {}
        self.powerup_seq = 0
        self.next_powerup_at = 0.0

        self.killfeed: List[Dict] = []
        self.killfeed_seq = 0

        self.last_countdown_sent = -1
        self.next_score_tick = 0.0
        self.next_snapshot_at = 0.0
        self.next_match_state_at = 0.0
        self.state_seq = 0

        self.loop_started = False

    def normalize_hero(self, hero: Optional[str]) -> str:
        key = str(hero or '').strip().lower()
        if key in self.HERO_DEFAULT_WEAPON:
            return key
        return 'knight'

    def normalize_weapon(self, hero: str, weapon: Optional[str]) -> str:
        key = str(weapon or '').strip().lower()
        if key in self.WEAPON_CONFIG:
            return key
        return self.HERO_DEFAULT_WEAPON.get(hero, 'bulwark-disc')

    def _active_player_ids(self) -> List[str]:
        return [sid for sid, player in self.players.items() if not player.get('spectator', False)]

    def _active_players(self) -> List[Dict]:
        return [player for player in self.players.values() if not player.get('spectator', False)]

    def _active_player_count(self) -> int:
        return len(self._active_player_ids())

    def _next_spawn(self, idx: int) -> Tuple[float, float]:
        if not self.SPAWN_POINTS:
            return self.MAP_WIDTH / 2.0, self.MAP_HEIGHT / 2.0
        x, y = self.SPAWN_POINTS[idx % len(self.SPAWN_POINTS)]
        return float(x), float(y)

    def _refresh_player_profile(self, player: Dict, payload: Dict) -> None:
        hero = self.normalize_hero(payload.get('hero') or payload.get('character') or player.get('hero'))
        weapon = self.normalize_weapon(hero, payload.get('weaponType') or payload.get('weapon_type') or player.get('weaponType'))
        player['name'] = str(payload.get('name') or payload.get('username') or player.get('name') or 'Guest').strip()[:24] or 'Guest'
        player['avatar'] = str(payload.get('avatar') or payload.get('avatarUrl') or payload.get('avatar_url') or player.get('avatar') or '').strip()
        player['hero'] = hero
        player['weaponType'] = weapon

    def _build_player(self, sid: str, payload: Dict, spectator: bool, join_time: float) -> Dict:
        hero = self.normalize_hero(payload.get('hero') or payload.get('character'))
        weapon = self.normalize_weapon(hero, payload.get('weaponType') or payload.get('weapon_type'))
        return {
            'sid': sid,
            'name': str(payload.get('name') or payload.get('username') or 'Guest').strip()[:24] or 'Guest',
            'avatar': str(payload.get('avatar') or payload.get('avatarUrl') or payload.get('avatar_url') or '').strip(),
            'hero': hero,
            'weaponType': weapon,
            'x': self.MAP_WIDTH / 2.0,
            'y': self.MAP_HEIGHT / 2.0,
            'vx': 0.0,
            'vy': 0.0,
            'hp': 100,
            'maxHp': 100,
            'ammo': 3,
            'nextAmmoAt': join_time + 0.9,
            'lastShotAt': 0.0,
            'alive': not spectator,
            'respawnAt': 0.0,
            'stormTickAt': join_time + self.STORM_TICK_SECONDS,
            'speedUntil': 0.0,
            'shieldUntil': 0.0,
            'damageUntil': 0.0,
            'score': 0,
            'kills': 0,
            'deaths': 0,
            'coreSeconds': 0,
            'overclockMeter': 0.0,
            'overclockUntil': 0.0,
            'spectator': spectator,
            'input': {
                'up': False,
                'down': False,
                'left': False,
                'right': False,
                'seq': 0,
            },
            'lastInputSeq': 0,
            'joinedAt': join_time,
        }

    def join_player(self, sid: str, payload: Optional[Dict]) -> str:
        now = time.time()
        payload = payload or {}

        active_count = self._active_player_count()
        if active_count == 0 and self.state in ('ACTIVE', 'RESULTS'):
            self.reset_to_lobby(now)
            active_count = self._active_player_count()

        if sid in self.players:
            player = self.players[sid]
            self._refresh_player_profile(player, payload)

            if player.get('spectator') and self.state in ('LOBBY', 'COUNTDOWN') and active_count < self.MAX_ACTIVE_PLAYERS:
                player['spectator'] = False
                player['alive'] = False
                sx, sy = self._next_spawn(active_count)
                player['x'] = sx
                player['y'] = sy

            return 'spectator' if player.get('spectator') else 'player'

        spectator = self.state in ('ACTIVE', 'RESULTS') or active_count >= self.MAX_ACTIVE_PLAYERS
        player = self._build_player(sid, payload, spectator, now)

        if not spectator:
            spawn_idx = active_count
            sx, sy = self._next_spawn(spawn_idx)
            player['x'] = sx
            player['y'] = sy

        self.players[sid] = player
        return 'spectator' if spectator else 'player'

    def leave_player(self, sid: str) -> Optional[Dict]:
        player = self.players.pop(sid, None)
        if not player:
            return None

        if self.core.get('heldBy') == sid:
            self.core['heldBy'] = None
            self.core['x'] = player.get('x', self.MAP_WIDTH / 2.0)
            self.core['y'] = player.get('y', self.MAP_HEIGHT / 2.0)
            self.core['dropUnlockAt'] = time.time() + 0.8

        for projectile_id, projectile in list(self.projectiles.items()):
            if projectile.get('owner') == sid:
                del self.projectiles[projectile_id]

        return player

    def update_player_input(self, sid: str, payload: Dict) -> None:
        player = self.players.get(sid)
        if not player or player.get('spectator'):
            return

        seq = int(payload.get('seq', player.get('lastInputSeq', 0)))
        player['input'] = {
            'up': bool(payload.get('up', False)),
            'down': bool(payload.get('down', False)),
            'left': bool(payload.get('left', False)),
            'right': bool(payload.get('right', False)),
            'seq': seq,
        }
        player['lastInputSeq'] = seq

    def set_player_role_ready(self, sid: str) -> None:
        player = self.players.get(sid)
        if not player:
            return
        if self.state in ('LOBBY', 'COUNTDOWN') and self._active_player_count() < self.MAX_ACTIVE_PLAYERS:
            player['spectator'] = False

    def reset_zone(self, now: float) -> None:
        self.zone['x'] = self.MAP_WIDTH / 2.0
        self.zone['y'] = self.MAP_HEIGHT / 2.0
        self.zone['radius'] = float(self.INITIAL_ZONE_RADIUS)
        self.zone['targetRadius'] = float(self.INITIAL_ZONE_RADIUS)
        self.zone['shrinkStart'] = 0.0
        self.zone['shrinkEnd'] = 0.0
        self.zone['nextShrinkAt'] = now + self.SHRINK_INTERVAL_SECONDS

    def start_match(self, now: float) -> None:
        self.state = 'ACTIVE'
        self.countdown_end_at = None
        self.results_end_at = None
        self.last_countdown_sent = -1

        self.projectiles.clear()
        self.projectile_seq = 0
        self.powerups.clear()
        self.powerup_seq = 0
        self.next_powerup_at = now + 2.0
        self.killfeed.clear()

        self.match_end_time = now + self.MATCH_DURATION_SECONDS
        self.time_left = float(self.MATCH_DURATION_SECONDS)

        self.reset_zone(now)

        self.core['x'] = self.MAP_WIDTH / 2.0
        self.core['y'] = self.MAP_HEIGHT / 2.0
        self.core['heldBy'] = None
        self.core['dropUnlockAt'] = now + 1.0

        active_ids = self._active_player_ids()
        for index, sid in enumerate(active_ids):
            player = self.players[sid]
            sx, sy = self._next_spawn(index)
            player['x'] = sx
            player['y'] = sy
            player['vx'] = 0.0
            player['vy'] = 0.0
            player['hp'] = player.get('maxHp', 100)
            player['alive'] = True
            player['respawnAt'] = 0.0
            player['score'] = 0
            player['kills'] = 0
            player['deaths'] = 0
            player['ammo'] = 3
            player['nextAmmoAt'] = now + 0.9
            player['stormTickAt'] = now + self.STORM_TICK_SECONDS
            player['speedUntil'] = 0.0
            player['shieldUntil'] = 0.0
            player['damageUntil'] = 0.0
            player['coreSeconds'] = 0
            player['overclockMeter'] = 0.0
            player['overclockUntil'] = 0.0

        for sid, player in self.players.items():
            if sid not in active_ids:
                player['alive'] = False

        self.next_score_tick = now + 1.0
        self.next_snapshot_at = now
        self.next_match_state_at = now

        self.socketio.emit('koz:match_start', {
            'state': self.state,
            'timeLeft': int(round(self.time_left)),
            'startedAt': now,
            'scoreTarget': self.SCORE_TARGET,
        }, room=self.ROOM_NAME)

    def finish_match(self, now: float, reason: str = 'time') -> None:
        if self.state != 'ACTIVE':
            return

        self.state = 'RESULTS'
        self.results_end_at = now + self.RESULTS_SECONDS
        self.match_end_time = now
        self.time_left = 0.0

        results = self._scoreboard_entries()
        winner = results[0] if results else None

        payload = {
            'reason': reason,
            'winner': winner,
            'results': results,
            'resetIn': self.RESULTS_SECONDS,
        }
        self.socketio.emit('koz:match_end', payload, room=self.ROOM_NAME)
        self.socketio.emit('koz:results', payload, room=self.ROOM_NAME)

    def _promote_spectators_for_lobby(self) -> None:
        active = [p for p in self.players.values() if not p.get('spectator')]
        if len(active) >= self.MAX_ACTIVE_PLAYERS:
            return

        spectators = sorted(
            [p for p in self.players.values() if p.get('spectator')],
            key=lambda item: item.get('joinedAt', 0.0)
        )
        for player in spectators:
            if len(active) >= self.MAX_ACTIVE_PLAYERS:
                break
            player['spectator'] = False
            active.append(player)

    def reset_to_lobby(self, now: float) -> None:
        self.state = 'RESET'
        self.socketio.emit('koz:match_state', self.serialize_match_state(now), room=self.ROOM_NAME)

        self.state = 'LOBBY'
        self.countdown_end_at = None
        self.results_end_at = None
        self.last_countdown_sent = -1

        self.match_end_time = None
        self.time_left = float(self.MATCH_DURATION_SECONDS)

        self.projectiles.clear()
        self.powerups.clear()
        self.killfeed.clear()

        self.reset_zone(now)

        self.core['x'] = self.MAP_WIDTH / 2.0
        self.core['y'] = self.MAP_HEIGHT / 2.0
        self.core['heldBy'] = None
        self.core['dropUnlockAt'] = now + 0.8

        self._promote_spectators_for_lobby()

        active_ids = self._active_player_ids()
        for index, sid in enumerate(active_ids):
            player = self.players[sid]
            sx, sy = self._next_spawn(index)
            player['x'] = sx
            player['y'] = sy
            player['vx'] = 0.0
            player['vy'] = 0.0
            player['alive'] = False
            player['score'] = 0
            player['kills'] = 0
            player['deaths'] = 0
            player['ammo'] = 3
            player['overclockMeter'] = 0.0
            player['overclockUntil'] = 0.0
            player['coreSeconds'] = 0

        self.socketio.emit('koz:match_state', self.serialize_match_state(now), room=self.ROOM_NAME)

    def evaluate_state_machine(self, now: float) -> None:
        active_count = self._active_player_count()

        if active_count == 0 and self.state in ('ACTIVE', 'RESULTS'):
            self.reset_to_lobby(now)
            return

        if self.state == 'LOBBY':
            if active_count >= self.MIN_PLAYERS_TO_START:
                self.state = 'COUNTDOWN'
                self.countdown_end_at = now + self.COUNTDOWN_SECONDS
                self.last_countdown_sent = self.COUNTDOWN_SECONDS
                self.socketio.emit('koz:countdown_start', {
                    'seconds': self.COUNTDOWN_SECONDS,
                    'minPlayers': self.MIN_PLAYERS_TO_START,
                }, room=self.ROOM_NAME)
            return

        if self.state == 'COUNTDOWN':
            if active_count < self.MIN_PLAYERS_TO_START:
                self.state = 'LOBBY'
                self.countdown_end_at = None
                self.last_countdown_sent = -1
                self.socketio.emit('koz:countdown_cancelled', {
                    'reason': 'players_dropped',
                    'activePlayers': active_count,
                    'minPlayers': self.MIN_PLAYERS_TO_START,
                }, room=self.ROOM_NAME)
                return

            remaining = max(0, int(math.ceil((self.countdown_end_at or now) - now)))
            if remaining != self.last_countdown_sent:
                self.last_countdown_sent = remaining
                self.socketio.emit('koz:countdown_start', {
                    'seconds': remaining,
                    'minPlayers': self.MIN_PLAYERS_TO_START,
                }, room=self.ROOM_NAME)

            if remaining <= 0:
                self.start_match(now)
            return

        if self.state == 'ACTIVE':
            if self.match_end_time is not None:
                self.time_left = max(0.0, self.match_end_time - now)
            return

        if self.state == 'RESULTS' and self.results_end_at is not None and now >= self.results_end_at:
            self.reset_to_lobby(now)

    def add_killfeed(self, killer: Optional[Dict], target: Dict, reason: str, now: float) -> Dict:
        self.killfeed_seq += 1
        entry = {
            'id': self.killfeed_seq,
            'killerSid': killer.get('sid') if killer else None,
            'killerName': killer.get('name') if killer else 'Storm',
            'targetSid': target.get('sid'),
            'targetName': target.get('name', 'Unknown'),
            'reason': reason,
            'time': now,
        }
        self.killfeed.append(entry)
        if len(self.killfeed) > 10:
            self.killfeed = self.killfeed[-10:]
        return entry

    def _scoreboard_entries(self) -> List[Dict]:
        entries = []
        for sid, player in self.players.items():
            if player.get('spectator'):
                continue
            entries.append({
                'sid': sid,
                'name': player.get('name'),
                'avatar': player.get('avatar', ''),
                'hero': player.get('hero'),
                'score': int(player.get('score', 0)),
                'kills': int(player.get('kills', 0)),
                'deaths': int(player.get('deaths', 0)),
                'coreSeconds': int(player.get('coreSeconds', 0)),
            })
        entries.sort(key=lambda item: (item.get('score', 0), item.get('kills', 0), -item.get('deaths', 0)), reverse=True)
        return entries

    def serialize_lobby(self, now: Optional[float] = None) -> Dict:
        now = now if now is not None else time.time()
        players = []
        for sid, player in self.players.items():
            players.append({
                'sid': sid,
                'name': player.get('name'),
                'avatar': player.get('avatar', ''),
                'hero': player.get('hero'),
                'weaponType': player.get('weaponType'),
                'spectator': bool(player.get('spectator')),
            })
        players.sort(key=lambda item: item.get('name', '').lower())

        countdown = 0
        if self.state == 'COUNTDOWN' and self.countdown_end_at is not None:
            countdown = max(0, int(math.ceil(self.countdown_end_at - now)))

        return {
            'state': self.state,
            'room': self.ROOM_NAME,
            'minPlayers': self.MIN_PLAYERS_TO_START,
            'min_players': self.MIN_PLAYERS_TO_START,
            'activePlayers': self._active_player_count(),
            'active_players': self._active_player_count(),
            'spectators': len([p for p in self.players.values() if p.get('spectator')]),
            'countdown': countdown,
            'players': players,
        }

    def serialize_match_state(self, now: Optional[float] = None) -> Dict:
        now = now if now is not None else time.time()

        countdown = 0
        if self.state == 'COUNTDOWN' and self.countdown_end_at is not None:
            countdown = max(0, int(math.ceil(self.countdown_end_at - now)))

        next_shrink_in = 0
        if self.state == 'ACTIVE':
            if self.zone.get('shrinkEnd', 0.0) > now:
                next_shrink_in = int(math.ceil(self.zone['shrinkEnd'] - now))
            else:
                next_shrink_in = int(max(0, math.ceil(self.zone.get('nextShrinkAt', now) - now)))

        return {
            'state': self.state,
            'timeLeft': int(max(0, math.ceil(self.time_left))),
            'countdown': countdown,
            'nextShrinkIn': max(0, next_shrink_in),
            'zoneRadius': float(self.zone.get('radius', 0.0)),
            'minPlayers': self.MIN_PLAYERS_TO_START,
            'min_players': self.MIN_PLAYERS_TO_START,
            'activePlayers': self._active_player_count(),
            'active_players': self._active_player_count(),
        }

    def serialize_snapshot(self, now: Optional[float] = None) -> Dict:
        now = now if now is not None else time.time()
        self.state_seq += 1

        players = []
        for sid, player in self.players.items():
            players.append({
                'sid': sid,
                'name': player.get('name'),
                'avatar': player.get('avatar', ''),
                'hero': player.get('hero'),
                'weaponType': player.get('weaponType'),
                'x': float(player.get('x', 0.0)),
                'y': float(player.get('y', 0.0)),
                'vx': float(player.get('vx', 0.0)),
                'vy': float(player.get('vy', 0.0)),
                'hp': int(player.get('hp', 0)),
                'maxHp': int(player.get('maxHp', 100)),
                'ammo': int(player.get('ammo', 0)),
                'alive': bool(player.get('alive', False)),
                'score': int(player.get('score', 0)),
                'kills': int(player.get('kills', 0)),
                'deaths': int(player.get('deaths', 0)),
                'spectator': bool(player.get('spectator', False)),
                'lastInputSeq': int(player.get('lastInputSeq', 0)),
                'overclockMeter': float(player.get('overclockMeter', 0.0)),
                'overclockActive': float(player.get('overclockUntil', 0.0)) > now,
                'coreHolder': self.core.get('heldBy') == sid,
            })

        countdown = 0
        if self.state == 'COUNTDOWN' and self.countdown_end_at is not None:
            countdown = max(0, int(math.ceil(self.countdown_end_at - now)))

        next_shrink_in = 0
        if self.state == 'ACTIVE':
            if self.zone.get('shrinkEnd', 0.0) > now:
                next_shrink_in = int(math.ceil(self.zone['shrinkEnd'] - now))
            else:
                next_shrink_in = int(max(0, math.ceil(self.zone.get('nextShrinkAt', now) - now)))

        return {
            'seq': self.state_seq,
            'serverTime': now,
            'room': self.ROOM_NAME,
            'map': {
                'width': self.MAP_WIDTH,
                'height': self.MAP_HEIGHT,
            },
            'match': {
                'state': self.state,
                'timeLeft': int(max(0, math.ceil(self.time_left))),
                'countdown': countdown,
                'nextShrinkIn': max(0, next_shrink_in),
                'scoreTarget': self.SCORE_TARGET,
                'minPlayers': self.MIN_PLAYERS_TO_START,
                'min_players': self.MIN_PLAYERS_TO_START,
                'activePlayers': self._active_player_count(),
                'active_players': self._active_player_count(),
            },
            'zone': {
                'x': float(self.zone.get('x', self.MAP_WIDTH / 2.0)),
                'y': float(self.zone.get('y', self.MAP_HEIGHT / 2.0)),
                'radius': float(self.zone.get('radius', self.INITIAL_ZONE_RADIUS)),
            },
            'storm': {
                'damage': self.STORM_DAMAGE_PER_TICK,
                'tickSeconds': self.STORM_TICK_SECONDS,
            },
            'core': {
                'x': float(self.core.get('x', self.MAP_WIDTH / 2.0)),
                'y': float(self.core.get('y', self.MAP_HEIGHT / 2.0)),
                'radius': int(self.core.get('radius', 20)),
                'heldBy': self.core.get('heldBy'),
            },
            'players': players,
            'projectiles': [
                {
                    'id': projectile_id,
                    'x': float(projectile.get('x', 0.0)),
                    'y': float(projectile.get('y', 0.0)),
                    'vx': float(projectile.get('vx', 0.0)),
                    'vy': float(projectile.get('vy', 0.0)),
                    'radius': int(projectile.get('radius', 6)),
                    'owner': projectile.get('owner'),
                    'weaponType': projectile.get('weaponType'),
                    'color': projectile.get('color', '#ffffff'),
                }
                for projectile_id, projectile in self.projectiles.items()
            ],
            'powerups': [
                {
                    'id': powerup_id,
                    'type': powerup.get('type'),
                    'x': float(powerup.get('x', 0.0)),
                    'y': float(powerup.get('y', 0.0)),
                    'radius': int(powerup.get('radius', 16)),
                }
                for powerup_id, powerup in self.powerups.items()
            ],
            'obstacles': self.OBSTACLES,
            'scoreboard': self._scoreboard_entries(),
            'killfeed': self.killfeed[-6:],
        }
