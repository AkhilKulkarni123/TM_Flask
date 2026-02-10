import time
from typing import Optional

from flask import request
from flask_login import current_user
from flask_socketio import join_room, leave_room

from .slitherrush_manager import SlitherRushManager
from .slitherrush_simulation import SlitherRushSimulation


_manager: Optional[SlitherRushManager] = None
_simulation: Optional[SlitherRushSimulation] = None


def _ensure_loop_started() -> None:
    global _manager
    if _manager is None:
        return
    if _manager.loop_started:
        return

    _manager.loop_started = True
    _manager.socketio.start_background_task(_tick_loop)


def _tick_loop() -> None:
    global _manager, _simulation
    last = time.time()

    while _manager is not None and _simulation is not None:
        now = time.time()
        dt = max(0.0, min(0.1, now - last))
        last = now

        with _manager.lock:
            _simulation.step(now, dt)

            for arena in list(_manager.arenas.values()):
                if now - arena.get('last_snapshot_at', 0.0) >= _manager.SNAPSHOT_INTERVAL:
                    arena['last_snapshot_at'] = now
                    _manager.emit_state(arena, now)

                if now - arena.get('last_leaderboard_at', 0.0) >= _manager.LEADERBOARD_INTERVAL:
                    arena['last_leaderboard_at'] = now
                    _manager.emit_leaderboard(arena)

        sleep_for = _manager.TICK_INTERVAL if _manager.arenas else 0.2
        time.sleep(sleep_for)


def cleanup_disconnected_player(sid: str) -> None:
    global _manager
    if _manager is None:
        return

    with _manager.lock:
        removed = _manager.leave_player(sid)
        if not removed:
            return

        arena = removed['arena']
        player = removed['player']
        try:
            leave_room(arena['room'], sid=sid)
        except Exception:
            pass

        _manager.socketio.emit('slitherrush_death', {
            'player_id': player.get('player_id'),
            'killer_id': None,
            'reason': 'disconnect',
        }, room=arena['room'])

        _manager.emit_status_snapshot()


def _resolve_user_identity(payload):
    default_name = str((payload or {}).get('username') or 'Guest').strip() or 'Guest'

    user_id = None
    username = default_name
    try:
        if current_user and getattr(current_user, 'is_authenticated', False):
            user_id = int(getattr(current_user, 'id'))
            username = str(getattr(current_user, 'name', None) or default_name)
    except Exception:
        pass

    provided_uid = (payload or {}).get('user_id')
    if user_id is None and provided_uid is not None:
        try:
            user_id = int(provided_uid)
        except (TypeError, ValueError):
            user_id = None

    return user_id, username[:24]


def init_slitherrush_socket(socketio) -> None:
    global _manager, _simulation

    if _manager is None:
        _manager = SlitherRushManager(socketio)
        _simulation = SlitherRushSimulation(_manager)

    @socketio.on('slitherrush_join')
    def handle_slitherrush_join(data):
        global _manager
        if _manager is None:
            return

        payload = data or {}
        sid = request.sid
        user_id, fallback_name = _resolve_user_identity(payload)

        with _manager.lock:
            joined = _manager.join_player(sid, payload, user_id=user_id, username_fallback=fallback_name)
            arena_id = joined['arena_id']
            arena = _manager.arenas.get(arena_id)
            if not arena:
                return

            join_room(arena['room'])

            socketio.emit('slitherrush_joined', {
                'arena_id': arena_id,
                'player_id': joined['player_id'],
                'role': joined.get('role', 'spectator'),
            }, to=sid)

            _manager.emit_state(arena, time.time())
            _manager.emit_status_snapshot()

        _ensure_loop_started()

    @socketio.on('slitherrush_leave')
    def handle_slitherrush_leave(_data):
        global _manager
        if _manager is None:
            return

        sid = request.sid

        with _manager.lock:
            removed = _manager.leave_player(sid)
            if not removed:
                return

            arena = removed['arena']
            player = removed['player']
            leave_room(arena['room'])

            socketio.emit('slitherrush_death', {
                'player_id': player.get('player_id'),
                'killer_id': None,
                'reason': 'left',
            }, room=arena['room'])

            _manager.emit_status_snapshot()

    @socketio.on('slitherrush_play_again')
    def handle_slitherrush_play_again(_data):
        global _manager
        if _manager is None:
            return

        sid = request.sid
        with _manager.lock:
            arena = _manager.set_player_ready(sid)
            if not arena:
                return
            _manager.emit_state(arena, time.time())

    @socketio.on('slitherrush_input')
    def handle_slitherrush_input(data):
        global _manager
        if _manager is None:
            return

        payload = data or {}
        sid = request.sid

        with _manager.lock:
            _manager.update_player_input(sid, payload)

    @socketio.on('slitherrush_get_status')
    def handle_slitherrush_get_status(_data):
        global _manager
        if _manager is None:
            return
        with _manager.lock:
            _manager.emit_status_snapshot(to_sid=request.sid)

    _ensure_loop_started()
