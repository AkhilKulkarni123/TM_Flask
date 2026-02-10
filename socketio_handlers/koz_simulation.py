import math
import random
from typing import Dict, List, Optional, Tuple

from .koz_manager import KozManager, circle_rect_intersects, clamp, distance


class KozSimulation:
    def __init__(self, manager: KozManager):
        self.m = manager

    def _player_speed_multiplier(self, player: Dict, now: float) -> float:
        mult = 1.0
        if player.get('speedUntil', 0.0) > now:
            mult *= 1.35
        if player.get('overclockUntil', 0.0) > now:
            mult *= 1.20
        return mult

    def _player_damage_multiplier(self, player: Dict, now: float) -> float:
        mult = 1.0
        if player.get('damageUntil', 0.0) > now:
            mult *= 1.3
        if player.get('overclockUntil', 0.0) > now:
            mult *= 1.15
        return mult

    def _apply_circle_wall_resolution(self, obj: Dict, radius: float) -> None:
        px = clamp(obj['x'], radius, self.m.MAP_WIDTH - radius)
        py = clamp(obj['y'], radius, self.m.MAP_HEIGHT - radius)

        for wall in self.m.OBSTACLES:
            if not circle_rect_intersects(px, py, radius, wall):
                continue

            left = wall['x']
            right = wall['x'] + wall['w']
            top = wall['y']
            bottom = wall['y'] + wall['h']

            nearest_x = clamp(px, left, right)
            nearest_y = clamp(py, top, bottom)
            dx = px - nearest_x
            dy = py - nearest_y

            if abs(dx) > abs(dy):
                push = radius if dx >= 0 else -radius
                px = nearest_x + push
                obj['vx'] = 0.0
            else:
                push = radius if dy >= 0 else -radius
                py = nearest_y + push
                obj['vy'] = 0.0

            px = clamp(px, radius, self.m.MAP_WIDTH - radius)
            py = clamp(py, radius, self.m.MAP_HEIGHT - radius)

        obj['x'] = px
        obj['y'] = py

    def _step_player_movement(self, player: Dict, dt: float, now: float) -> None:
        if not player.get('alive', False):
            return

        hero = player.get('hero', 'knight')
        base_speed = self.m.HERO_BASE_SPEED.get(hero, 310.0)
        speed = base_speed * self._player_speed_multiplier(player, now)

        inp = player.get('input', {})
        axis_x = (1 if inp.get('right') else 0) - (1 if inp.get('left') else 0)
        axis_y = (1 if inp.get('down') else 0) - (1 if inp.get('up') else 0)

        mag = math.hypot(axis_x, axis_y)
        if mag > 0:
            axis_x /= mag
            axis_y /= mag

        target_vx = axis_x * speed
        target_vy = axis_y * speed

        accel = min(1.0, 16.0 * dt)
        player['vx'] = player.get('vx', 0.0) + (target_vx - player.get('vx', 0.0)) * accel
        player['vy'] = player.get('vy', 0.0) + (target_vy - player.get('vy', 0.0)) * accel

        if mag == 0:
            friction = max(0.0, 1.0 - (10.0 * dt))
            player['vx'] *= friction
            player['vy'] *= friction

        player['x'] += player['vx'] * dt
        player['y'] += player['vy'] * dt

        self._apply_circle_wall_resolution(player, self.m.PLAYER_RADIUS)

    def _maybe_respawn_player(self, sid: str, player: Dict, now: float) -> None:
        if player.get('alive'):
            return
        if player.get('spectator'):
            return
        if player.get('respawnAt', 0.0) > now:
            return

        active_ids = self.m._active_player_ids()
        try:
            idx = active_ids.index(sid)
        except ValueError:
            idx = 0
        sx, sy = self.m._next_spawn(idx + int(now) % max(1, len(self.m.SPAWN_POINTS)))

        player['x'] = sx
        player['y'] = sy
        player['vx'] = 0.0
        player['vy'] = 0.0
        player['hp'] = player.get('maxHp', 100)
        player['alive'] = True
        player['ammo'] = 3
        player['nextAmmoAt'] = now + 0.9
        player['stormTickAt'] = now + self.m.STORM_TICK_SECONDS

    def _step_ammo_regen(self, player: Dict, now: float) -> None:
        while player.get('ammo', 0) < 3 and player.get('nextAmmoAt', now + 99) <= now:
            player['ammo'] = min(3, player.get('ammo', 0) + 1)
            player['nextAmmoAt'] = player.get('nextAmmoAt', now) + 0.9

    def _step_zone(self, now: float) -> None:
        zone = self.m.zone
        if zone.get('shrinkEnd', 0.0) > now:
            start = zone.get('shrinkStart', now)
            end = zone.get('shrinkEnd', now)
            from_radius = zone.get('radiusBeforeShrink', zone['radius'])
            to_radius = zone.get('targetRadius', zone['radius'])
            progress = 1.0
            if end > start:
                progress = clamp((now - start) / (end - start), 0.0, 1.0)
            zone['radius'] = from_radius + (to_radius - from_radius) * progress
            if progress >= 1.0:
                zone['radius'] = to_radius
                zone['shrinkStart'] = 0.0
                zone['shrinkEnd'] = 0.0
                zone['nextShrinkAt'] = now + self.m.SHRINK_INTERVAL_SECONDS
                self.m.socketio.emit('koz:zone_event', {
                    'type': 'shrink_complete',
                    'zone': {
                        'x': zone['x'],
                        'y': zone['y'],
                        'radius': zone['radius'],
                    }
                }, room=self.m.ROOM_NAME)
            return

        if zone.get('nextShrinkAt', 0.0) <= now and zone['radius'] > self.m.MIN_ZONE_RADIUS:
            from_radius = zone['radius']
            to_radius = max(self.m.MIN_ZONE_RADIUS, zone['radius'] * 0.84)
            zone['radiusBeforeShrink'] = from_radius
            zone['targetRadius'] = to_radius
            zone['shrinkStart'] = now
            zone['shrinkEnd'] = now + self.m.SHRINK_DURATION_SECONDS
            self.m.socketio.emit('koz:zone_event', {
                'type': 'shrink_start',
                'duration': self.m.SHRINK_DURATION_SECONDS,
                'zone': {
                    'x': zone['x'],
                    'y': zone['y'],
                    'radius': zone['radius'],
                    'targetRadius': to_radius,
                }
            }, room=self.m.ROOM_NAME)

    def _drop_core(self, x: float, y: float, now: float) -> None:
        self.m.core['heldBy'] = None
        self.m.core['x'] = clamp(x, 30.0, self.m.MAP_WIDTH - 30.0)
        self.m.core['y'] = clamp(y, 30.0, self.m.MAP_HEIGHT - 30.0)
        self.m.core['dropUnlockAt'] = now + 0.8

    def _apply_damage(self, target_sid: str, target: Dict, attacker_sid: Optional[str], damage: float, reason: str, now: float, shooter_pos: Optional[Tuple[float, float]] = None) -> None:
        if not target.get('alive'):
            return

        final_damage = max(1, int(round(damage)))
        if target.get('shieldUntil', 0.0) > now:
            final_damage = max(1, int(round(final_damage * 0.58)))

        target['hp'] = max(0, target.get('hp', 100) - final_damage)

        payload = {
            'target': target_sid,
            'damage': final_damage,
            'hp': target.get('hp', 0),
            'attacker': attacker_sid,
            'reason': reason,
        }
        if shooter_pos:
            payload['shooterX'] = shooter_pos[0]
            payload['shooterY'] = shooter_pos[1]

        self.m.socketio.emit('koz:hit', payload, room=self.m.ROOM_NAME)

        if target['hp'] > 0:
            return

        target['alive'] = False
        target['deaths'] = target.get('deaths', 0) + 1
        target['respawnAt'] = now + self.m.RESPAWN_SECONDS

        killer = None
        if attacker_sid and attacker_sid in self.m.players and attacker_sid != target_sid:
            killer = self.m.players[attacker_sid]
            killer['kills'] = killer.get('kills', 0) + 1
            killer['score'] = killer.get('score', 0) + self.m.KILL_SCORE

        if self.m.core.get('heldBy') == target_sid:
            self._drop_core(target.get('x', self.m.MAP_WIDTH / 2.0), target.get('y', self.m.MAP_HEIGHT / 2.0), now)

        killfeed_entry = self.m.add_killfeed(killer, target, reason, now)

        self.m.socketio.emit('koz:player_died', {
            'sid': target_sid,
            'killer': attacker_sid,
            'respawnIn': self.m.RESPAWN_SECONDS,
            'reason': reason,
        }, room=self.m.ROOM_NAME)
        self.m.socketio.emit('koz:killfeed', killfeed_entry, room=self.m.ROOM_NAME)

    def _step_storm(self, sid: str, player: Dict, now: float) -> None:
        if not player.get('alive') or player.get('spectator'):
            return

        dist = distance(player['x'], player['y'], self.m.zone['x'], self.m.zone['y'])
        outside = dist > self.m.zone['radius']
        if outside and player.get('stormTickAt', now + 99.0) <= now:
            self._apply_damage(sid, player, None, self.m.STORM_DAMAGE_PER_TICK, 'storm', now)
            player['stormTickAt'] = now + self.m.STORM_TICK_SECONDS
        elif not outside:
            player['stormTickAt'] = now + self.m.STORM_TICK_SECONDS

    def _step_projectiles(self, now: float, dt: float) -> None:
        removed: List[str] = []

        for projectile_id, projectile in list(self.m.projectiles.items()):
            projectile['life'] -= dt
            if projectile['life'] <= 0:
                removed.append(projectile_id)
                continue

            projectile['x'] += projectile.get('vx', 0.0) * dt
            projectile['y'] += projectile.get('vy', 0.0) * dt

            radius = projectile.get('radius', 6)
            if projectile['x'] < radius or projectile['x'] > self.m.MAP_WIDTH - radius or projectile['y'] < radius or projectile['y'] > self.m.MAP_HEIGHT - radius:
                removed.append(projectile_id)
                continue

            wall_hit = any(circle_rect_intersects(projectile['x'], projectile['y'], radius, wall) for wall in self.m.OBSTACLES)
            if wall_hit:
                removed.append(projectile_id)
                continue

            owner_sid = projectile.get('owner')
            victim_sid: Optional[str] = None
            victim: Optional[Dict] = None

            for sid, player in self.m.players.items():
                if sid == owner_sid:
                    continue
                if player.get('spectator') or not player.get('alive'):
                    continue
                if distance(projectile['x'], projectile['y'], player['x'], player['y']) <= (self.m.PLAYER_RADIUS + radius):
                    victim_sid = sid
                    victim = player
                    break

            if victim_sid and victim:
                damage = projectile.get('damage', 20)
                self._apply_damage(
                    victim_sid,
                    victim,
                    owner_sid,
                    damage,
                    'projectile',
                    now,
                    shooter_pos=(projectile['x'], projectile['y'])
                )

                splash = projectile.get('splash', 0)
                if splash and splash > 0:
                    for sid, player in self.m.players.items():
                        if sid in (owner_sid, victim_sid):
                            continue
                        if player.get('spectator') or not player.get('alive'):
                            continue
                        if distance(projectile['x'], projectile['y'], player['x'], player['y']) <= splash:
                            self._apply_damage(
                                sid,
                                player,
                                owner_sid,
                                max(1, int(damage * 0.55)),
                                'splash',
                                now,
                                shooter_pos=(projectile['x'], projectile['y'])
                            )

                removed.append(projectile_id)

        for projectile_id in removed:
            self.m.projectiles.pop(projectile_id, None)

    def _spawn_powerup(self, now: float) -> Optional[Dict]:
        if len(self.m.powerups) >= self.m.MAX_POWERUPS:
            return None

        random.shuffle(self.m.POWERUP_SPAWNS)
        chosen = None

        for sx, sy in self.m.POWERUP_SPAWNS:
            occupied = False
            for powerup in self.m.powerups.values():
                if distance(sx, sy, powerup['x'], powerup['y']) < 50:
                    occupied = True
                    break
            if not occupied:
                chosen = (sx, sy)
                break

        if not chosen:
            chosen = random.choice(self.m.POWERUP_SPAWNS)

        ptype = random.choice(self.m.POWERUP_TYPES)
        self.m.powerup_seq += 1
        powerup = {
            'id': f'pow_{self.m.powerup_seq}',
            'type': ptype,
            'x': float(chosen[0]),
            'y': float(chosen[1]),
            'radius': 18,
            'spawnedAt': now,
        }
        self.m.powerups[powerup['id']] = powerup
        return powerup

    def _apply_powerup(self, player: Dict, powerup: Dict, now: float) -> None:
        ptype = powerup.get('type')
        if ptype == 'heal':
            player['hp'] = min(player.get('maxHp', 100), player.get('hp', 100) + 38)
        elif ptype == 'speed':
            player['speedUntil'] = max(player.get('speedUntil', 0.0), now + 5.0)
        elif ptype == 'shield':
            player['shieldUntil'] = max(player.get('shieldUntil', 0.0), now + 5.5)
        elif ptype == 'damage':
            player['damageUntil'] = max(player.get('damageUntil', 0.0), now + 6.0)
        elif ptype == 'ammo':
            player['ammo'] = 3
            player['nextAmmoAt'] = now + 0.9

    def _step_powerups(self, now: float) -> None:
        if now >= self.m.next_powerup_at:
            powerup = self._spawn_powerup(now)
            self.m.next_powerup_at = now + self.m.POWERUP_SPAWN_SECONDS
            if powerup:
                self.m.socketio.emit('koz:powerup_spawn', powerup, room=self.m.ROOM_NAME)

        for sid, player in self.m.players.items():
            if player.get('spectator') or not player.get('alive'):
                continue

            for powerup_id, powerup in list(self.m.powerups.items()):
                if distance(player['x'], player['y'], powerup['x'], powerup['y']) <= (self.m.PLAYER_RADIUS + powerup.get('radius', 16)):
                    self._apply_powerup(player, powerup, now)
                    self.m.powerups.pop(powerup_id, None)
                    self.m.socketio.emit('koz:powerup_picked', {
                        'id': powerup_id,
                        'type': powerup.get('type'),
                        'by': sid,
                    }, room=self.m.ROOM_NAME)
                    break

    def _step_core(self, now: float, dt: float) -> None:
        holder_sid = self.m.core.get('heldBy')
        if holder_sid:
            holder = self.m.players.get(holder_sid)
            if holder and holder.get('alive') and not holder.get('spectator'):
                self.m.core['x'] = holder['x']
                self.m.core['y'] = holder['y']
                holder['overclockMeter'] = min(100.0, holder.get('overclockMeter', 0.0) + self.m.OVERCLOCK_CHARGE_PER_SECOND * dt)
                if holder.get('overclockMeter', 0.0) >= 100.0 and holder.get('overclockUntil', 0.0) <= now:
                    holder['overclockUntil'] = now + self.m.OVERCLOCK_DURATION_SECONDS
                    holder['overclockMeter'] = 0.0
                    self.m.socketio.emit('koz:overclock', {
                        'sid': holder_sid,
                        'duration': self.m.OVERCLOCK_DURATION_SECONDS,
                    }, room=self.m.ROOM_NAME)
            else:
                drop_x = self.m.core.get('x', self.m.MAP_WIDTH / 2.0)
                drop_y = self.m.core.get('y', self.m.MAP_HEIGHT / 2.0)
                self._drop_core(drop_x, drop_y, now)
                holder_sid = None

        if holder_sid:
            return

        if now < self.m.core.get('dropUnlockAt', 0.0):
            return

        for sid, player in self.m.players.items():
            if player.get('spectator') or not player.get('alive'):
                continue
            if distance(player['x'], player['y'], self.m.core['x'], self.m.core['y']) <= (self.m.PLAYER_RADIUS + self.m.core.get('radius', 20)):
                self.m.core['heldBy'] = sid
                self.m.socketio.emit('koz:core_pickup', {
                    'sid': sid,
                }, room=self.m.ROOM_NAME)
                break

    def _step_score_tick(self, now: float) -> None:
        while self.m.next_score_tick <= now:
            holder_sid = self.m.core.get('heldBy')
            if holder_sid and holder_sid in self.m.players:
                holder = self.m.players[holder_sid]
                if holder.get('alive') and not holder.get('spectator'):
                    holder['score'] = holder.get('score', 0) + self.m.CORE_SCORE_PER_SECOND
                    holder['coreSeconds'] = holder.get('coreSeconds', 0) + 1
            self.m.next_score_tick += 1.0

    def handle_shoot(self, sid: str, aim_x: float, aim_y: float, now: float) -> Tuple[bool, str, List[Dict]]:
        if self.m.state != 'ACTIVE':
            return False, 'inactive', []

        player = self.m.players.get(sid)
        if not player:
            return False, 'unknown_player', []
        if player.get('spectator'):
            return False, 'spectator', []
        if not player.get('alive'):
            return False, 'dead', []

        weapon_type = self.m.normalize_weapon(player.get('hero', 'knight'), player.get('weaponType'))
        weapon = self.m.WEAPON_CONFIG[weapon_type]
        cooldown = weapon.get('cooldown', 0.45)

        if now - player.get('lastShotAt', 0.0) < cooldown:
            return False, 'cooldown', []
        if player.get('ammo', 0) <= 0:
            return False, 'ammo', []

        dx = float(aim_x) - player['x']
        dy = float(aim_y) - player['y']
        mag = math.hypot(dx, dy)
        if mag <= 0.001:
            return False, 'aim', []

        player['lastShotAt'] = now
        player['ammo'] = max(0, player.get('ammo', 0) - 1)
        if player['ammo'] < 3 and player.get('nextAmmoAt', 0.0) < now:
            player['nextAmmoAt'] = now + 0.9

        base_angle = math.atan2(dy, dx)
        spread = [0.0]
        if player.get('overclockUntil', 0.0) > now:
            spread = [-0.16, 0.0, 0.16]

        spawned = []
        for offset in spread:
            angle = base_angle + offset
            vx = math.cos(angle) * weapon['speed']
            vy = math.sin(angle) * weapon['speed']

            spawn_distance = self.m.PLAYER_RADIUS + weapon.get('radius', 6) + 6
            sx = player['x'] + math.cos(angle) * spawn_distance
            sy = player['y'] + math.sin(angle) * spawn_distance

            self.m.projectile_seq += 1
            projectile_id = f'pr_{self.m.projectile_seq}'
            projectile = {
                'id': projectile_id,
                'owner': sid,
                'x': sx,
                'y': sy,
                'vx': vx,
                'vy': vy,
                'radius': weapon.get('radius', 6),
                'damage': weapon.get('damage', 20) * self._player_damage_multiplier(player, now),
                'weaponType': weapon_type,
                'color': weapon.get('color', '#ffffff'),
                'life': weapon.get('lifetime', 1.4),
                'splash': weapon.get('splash', 0),
            }
            self.m.projectiles[projectile_id] = projectile
            spawned.append({
                'id': projectile_id,
                'x': projectile['x'],
                'y': projectile['y'],
                'vx': projectile['vx'],
                'vy': projectile['vy'],
                'radius': projectile['radius'],
                'owner': sid,
                'weaponType': weapon_type,
                'color': projectile['color'],
            })

        return True, 'ok', spawned

    def step(self, now: float, dt: float) -> None:
        self.m.evaluate_state_machine(now)

        if self.m.state != 'ACTIVE':
            return

        self.m.time_left = max(0.0, (self.m.match_end_time or now) - now)

        self._step_zone(now)

        for sid, player in self.m.players.items():
            self._maybe_respawn_player(sid, player, now)
            if not player.get('spectator') and player.get('alive'):
                self._step_ammo_regen(player, now)
                self._step_player_movement(player, dt, now)
                self._step_storm(sid, player, now)

        self._step_projectiles(now, dt)
        self._step_powerups(now)
        self._step_core(now, dt)
        self._step_score_tick(now)

        scoreboard = self.m._scoreboard_entries()
        if scoreboard and scoreboard[0].get('score', 0) >= self.m.SCORE_TARGET:
            self.m.finish_match(now, reason='score_target')
            return

        if self.m.time_left <= 0:
            self.m.finish_match(now, reason='time_limit')
