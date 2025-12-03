from flask_socketio import SocketIO, emit, join_room, leave_room, rooms
from flask import request
from flask_login import current_user
import json

# Boss battle state
boss_battles = {}  # Dictionary to store active boss battles

def init_boss_battle_socket(socketio):
    
    @socketio.on('join_boss_battle')
    def handle_join_boss_battle(data):
        username = data.get('username', 'Guest')
        user_id = data.get('user_id', 'guest')
        bullets = data.get('bullets', 0)
        character = data.get('character', 'default')
        
        room = 'boss_battle_room'
        join_room(room)
        
        # Initialize boss battle if it doesn't exist
        if 'boss_battle_room' not in boss_battles:
            boss_battles['boss_battle_room'] = {
                'boss_health': 1000,
                'max_health': 1000,
                'players': {}
            }
        
        # Add player to battle
        boss_battles['boss_battle_room']['players'][user_id] = {
            'username': username,
            'bullets': bullets,
            'lives': 3,
            'character': character,
            'status': 'alive'
        }
        
        # Notify all players
        emit('player_joined', {
            'username': username,
            'user_id': user_id,
            'active_players': len(boss_battles['boss_battle_room']['players']),
            'players': list(boss_battles['boss_battle_room']['players'].values())
        }, room=room)
        
        # Send current boss state to joining player
        emit('boss_state_update', {
            'boss_health': boss_battles['boss_battle_room']['boss_health'],
            'max_health': boss_battles['boss_battle_room']['max_health'],
            'active_players': len(boss_battles['boss_battle_room']['players']),
            'players': list(boss_battles['boss_battle_room']['players'].values())
        })
    
    @socketio.on('attack_boss')
    def handle_attack_boss(data):
        user_id = data.get('user_id')
        damage = data.get('damage', 10)
        username = data.get('username', 'Player')
        
        room = 'boss_battle_room'
        
        if room in boss_battles and user_id in boss_battles[room]['players']:
            player = boss_battles[room]['players'][user_id]
            
            # Check if player is alive
            if player['status'] != 'alive':
                return
            
            # Reduce boss health
            boss_battles[room]['boss_health'] -= damage
            
            if boss_battles[room]['boss_health'] < 0:
                boss_battles[room]['boss_health'] = 0
            
            # Broadcast boss health update to all players
            emit('boss_state_update', {
                'boss_health': boss_battles[room]['boss_health'],
                'max_health': boss_battles[room]['max_health'],
                'active_players': len([p for p in boss_battles[room]['players'].values() if p['status'] == 'alive']),
                'players': list(boss_battles[room]['players'].values()),
                'last_attacker': username,
                'damage': damage
            }, room=room)
            
            # Check if boss is defeated
            if boss_battles[room]['boss_health'] <= 0:
                emit('boss_defeated', {
                    'message': 'Boss has been defeated!',
                    'players': list(boss_battles[room]['players'].values())
                }, room=room)
                
                # Reset boss for next battle
                boss_battles[room]['boss_health'] = boss_battles[room]['max_health']
                boss_battles[room]['players'] = {}
    
    @socketio.on('player_hit')
    def handle_player_hit(data):
        user_id = data.get('user_id')
        username = data.get('username', 'Player')
        damage = data.get('damage', 1)
        
        room = 'boss_battle_room'
        
        if room in boss_battles and user_id in boss_battles[room]['players']:
            player = boss_battles[room]['players'][user_id]
            player['lives'] -= damage
            
            if player['lives'] <= 0:
                player['lives'] = 0
                player['status'] = 'dead'
                
                # Notify all players that this player died
                emit('player_died', {
                    'username': username,
                    'user_id': user_id,
                    'active_players': len([p for p in boss_battles[room]['players'].values() if p['status'] == 'alive'])
                }, room=room)
            
            # Update all players with new player state
            emit('boss_state_update', {
                'boss_health': boss_battles[room]['boss_health'],
                'max_health': boss_battles[room]['max_health'],
                'active_players': len([p for p in boss_battles[room]['players'].values() if p['status'] == 'alive']),
                'players': list(boss_battles[room]['players'].values())
            }, room=room)
    
    @socketio.on('leave_boss_battle')
    def handle_leave_boss_battle(data):
        user_id = data.get('user_id')
        username = data.get('username', 'Player')
        room = 'boss_battle_room'
        
        if room in boss_battles and user_id in boss_battles[room]['players']:
            del boss_battles[room]['players'][user_id]
            
            leave_room(room)
            
            # Notify remaining players
            emit('player_left', {
                'username': username,
                'user_id': user_id,
                'active_players': len(boss_battles[room]['players'])
            }, room=room)
    
    @socketio.on('disconnect')
    def handle_disconnect():
        # Clean up player from any active battles
        for room_name, battle in boss_battles.items():
            for user_id, player in list(battle['players'].items()):
                if request.sid == player.get('sid'):
                    del battle['players'][user_id]
                    emit('player_left', {
                        'username': player['username'],
                        'user_id': user_id,
                        'active_players': len(battle['players'])
                    }, room=room_name)