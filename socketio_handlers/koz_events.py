import time
from typing import Optional

from flask import request
from flask_socketio import join_room, leave_room

from .koz_manager import KozManager
from .koz_simulation import KozSimulation


_koz_manager: Optional[KozManager] = None
_koz_simulation: Optional[KozSimulation] = None



def _ensure_loop_started() -> None:
    if _koz_manager is None:
        return
    if _koz_manager.loop_started:
        return

    _koz_manager.loop_started = True
    _koz_manager.socketio.start_background_task(_tick_loop)



def _tick_loop() -> None:
    global _koz_manager, _koz_simulation
    last = time.time()

    while _koz_manager is not None and _koz_simulation is not None:
        now = time.time()
        dt = max(0.0, min(0.1, now - last))
        last = now

        with _koz_manager.lock:
            _koz_simulation.step(now, dt)

            if now >= _koz_manager.next_snapshot_at:
                _koz_manager.next_snapshot_at = now + _koz_manager.SNAPSHOT_INTERVAL
                snapshot = _koz_manager.serialize_snapshot(now)
                _koz_manager.socketio.emit('koz:state', snapshot, room=_koz_manager.ROOM_NAME)

            if now >= _koz_manager.next_match_state_at:
                _koz_manager.next_match_state_at = now + 1.0
                _koz_manager.socketio.emit('koz:match_state', _koz_manager.serialize_match_state(now), room=_koz_manager.ROOM_NAME)
                _koz_manager.socketio.emit('koz:lobby_update', _koz_manager.serialize_lobby(now), room=_koz_manager.ROOM_NAME)

        sleep_for = _koz_manager.TICK_INTERVAL if _koz_manager.players else 0.2
        time.sleep(sleep_for)



def cleanup_disconnected_player(sid: str) -> None:
    global _koz_manager
    if _koz_manager is None:
        return

    with _koz_manager.lock:
        removed = _koz_manager.leave_player(sid)
        if removed is None:
            return

        leave_room(_koz_manager.ROOM_NAME, sid=sid)
        now = time.time()
        _koz_manager.evaluate_state_machine(now)

        _koz_manager.socketio.emit('koz:player_left', {
            'sid': sid,
            'name': removed.get('name', 'Unknown'),
        }, room=_koz_manager.ROOM_NAME)

        _koz_manager.socketio.emit('koz:lobby_update', _koz_manager.serialize_lobby(now), room=_koz_manager.ROOM_NAME)
        _koz_manager.socketio.emit('koz:match_state', _koz_manager.serialize_match_state(now), room=_koz_manager.ROOM_NAME)



def init_koz_socket(socketio) -> None:
    global _koz_manager, _koz_simulation

    if _koz_manager is None:
        _koz_manager = KozManager(socketio)
        _koz_simulation = KozSimulation(_koz_manager)

    @socketio.on('koz:join_lobby')
    def handle_koz_join_lobby(data):
        global _koz_manager
        if _koz_manager is None:
            return

        payload = data or {}
        sid = request.sid

        join_room(_koz_manager.ROOM_NAME)

        with _koz_manager.lock:
            role = _koz_manager.join_player(sid, payload)
            now = time.time()
            _koz_manager.evaluate_state_machine(now)
            lobby_snapshot = _koz_manager.serialize_lobby(now)
            match_snapshot = _koz_manager.serialize_match_state(now)

            joined_payload = {
                'sid': sid,
                'role': role,
                'room': _koz_manager.ROOM_NAME,
                'map': {'width': _koz_manager.MAP_WIDTH, 'height': _koz_manager.MAP_HEIGHT},
                'tickRate': int(round(1.0 / _koz_manager.TICK_INTERVAL)),
                'snapshotRate': int(round(1.0 / _koz_manager.SNAPSHOT_INTERVAL)),
                'minPlayers': _koz_manager.MIN_PLAYERS_TO_START,
                'activePlayers': int(lobby_snapshot.get('activePlayers', 0)),
                'lobby': lobby_snapshot,
            }
            socketio.emit('koz:joined', joined_payload, to=sid)
            socketio.emit('koz:lobby_update', lobby_snapshot, room=_koz_manager.ROOM_NAME)
            socketio.emit('koz:match_state', match_snapshot, room=_koz_manager.ROOM_NAME)
            socketio.emit('koz:state', _koz_manager.serialize_snapshot(now), to=sid)

        _ensure_loop_started()

    @socketio.on('koz:leave_lobby')
    def handle_koz_leave_lobby(_data):
        global _koz_manager
        if _koz_manager is None:
            return

        sid = request.sid

        with _koz_manager.lock:
            removed = _koz_manager.leave_player(sid)
            leave_room(_koz_manager.ROOM_NAME)
            if removed:
                now = time.time()
                _koz_manager.evaluate_state_machine(now)
                socketio.emit('koz:player_left', {
                    'sid': sid,
                    'name': removed.get('name', 'Unknown'),
                }, room=_koz_manager.ROOM_NAME)
                socketio.emit('koz:lobby_update', _koz_manager.serialize_lobby(now), room=_koz_manager.ROOM_NAME)
                socketio.emit('koz:match_state', _koz_manager.serialize_match_state(now), room=_koz_manager.ROOM_NAME)

    @socketio.on('koz:play_again')
    def handle_koz_play_again(_data):
        global _koz_manager
        if _koz_manager is None:
            return

        sid = request.sid
        with _koz_manager.lock:
            _koz_manager.set_player_role_ready(sid)
            now = time.time()
            _koz_manager.evaluate_state_machine(now)
            socketio.emit('koz:lobby_update', _koz_manager.serialize_lobby(now), room=_koz_manager.ROOM_NAME)
            socketio.emit('koz:match_state', _koz_manager.serialize_match_state(now), room=_koz_manager.ROOM_NAME)

    @socketio.on('koz:input')
    def handle_koz_input(data):
        global _koz_manager
        if _koz_manager is None:
            return

        payload = data or {}
        sid = request.sid

        with _koz_manager.lock:
            _koz_manager.update_player_input(sid, payload)

    @socketio.on('koz:shoot')
    def handle_koz_shoot(data):
        global _koz_manager, _koz_simulation
        if _koz_manager is None or _koz_simulation is None:
            return

        payload = data or {}
        sid = request.sid

        aim_x = payload.get('aimX')
        aim_y = payload.get('aimY')
        try:
            aim_x = float(aim_x)
            aim_y = float(aim_y)
        except (TypeError, ValueError):
            socketio.emit('koz:shot_rejected', {'reason': 'aim'}, to=sid)
            return

        with _koz_manager.lock:
            ok, reason, spawned = _koz_simulation.handle_shoot(sid, aim_x, aim_y, time.time())
            if not ok:
                socketio.emit('koz:shot_rejected', {'reason': reason}, to=sid)
                return

            socketio.emit('koz:projectile_spawn', {
                'projectiles': spawned,
                'owner': sid,
            }, room=_koz_manager.ROOM_NAME)

    @socketio.on('koz:request_state')
    def handle_koz_request_state(_data):
        global _koz_manager
        if _koz_manager is None:
            return

        sid = request.sid
        with _koz_manager.lock:
            now = time.time()
            socketio.emit('koz:lobby_update', _koz_manager.serialize_lobby(now), to=sid)
            socketio.emit('koz:match_state', _koz_manager.serialize_match_state(now), to=sid)
            socketio.emit('koz:state', _koz_manager.serialize_snapshot(now), to=sid)

    _ensure_loop_started()
