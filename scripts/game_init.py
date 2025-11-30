#!/usr/bin/env python3

"""
Game Initialization Script
Creates all necessary database tables for the Snakes and Ladders game
"""

import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from __init__ import app, db
from model.user import User
from model.game_progress import GameProgress, SquareCompletion
from model.boss_room import BossRoom, BossPlayer, BossBattleStats
from datetime import datetime


def init_db():
    """Initialize the database with all tables"""
    print("=" * 60)
    print("Snakes and Ladders Game - Database Initialization")
    print("=" * 60)
    
    with app.app_context():
        try:
            print("\n📊 Creating database tables...")
            
            # Create all tables
            db.create_all()
            
            print("✅ Game Progress tables created")
            print("✅ Boss Battle tables created")
            print("✅ Square Completion tracking created")
            
            # Verify tables
            inspector = db.inspect(db.engine)
            tables = inspector.get_table_names()
            
            print(f"\n📋 Created tables ({len(tables)}):")
            for table in tables:
                print(f"  - {table}")
            
            print("\n" + "=" * 60)
            print("✅ Database initialization complete!")
            print("=" * 60)
            
            return True
            
        except Exception as e:
            print(f"\n❌ Error during initialization: {str(e)}")
            return False


def create_test_data():
    """Create test data for development"""
    print("\n" + "=" * 60)
    print("Creating Test Data")
    print("=" * 60)
    
    with app.app_context():
        try:
            # Check if test user already exists
            test_user = User.query.filter_by(uid='testplayer').first()
            
            if not test_user:
                print("\n👤 Creating test user...")
                test_user = User(
                    name='Test Player',
                    uid='testplayer',
                    password='test123',
                    role='User'
                )
                db.session.add(test_user)
                db.session.flush()
                print(f"✅ Created test user: {test_user.name}")
            else:
                print(f"\n👤 Test user already exists: {test_user.name}")
            
            # Create game progress for test user
            progress = GameProgress.query.filter_by(user_id=test_user.id).first()
            if not progress:
                print("🎮 Creating game progress...")
                progress = GameProgress(user_id=test_user.id)
                progress.current_position = 5
                progress.completed_squares = [1, 2, 3, 4]
                progress.bullets = 50
                progress.lives = 3
                progress.time_played_minutes = 30
                db.session.add(progress)
                db.session.flush()
                
                # Add some square completions
                for square_num in [1, 2, 3, 4]:
                    completion = SquareCompletion(
                        game_progress_id=progress.id,
                        square_number=square_num,
                        bullets_earned=10,
                        time_spent_seconds=120
                    )
                    db.session.add(completion)
                
                print(f"✅ Created game progress for {test_user.name}")
            else:
                print(f"🎮 Game progress already exists for {test_user.name}")
            
            # Create boss stats
            boss_stats = BossBattleStats.query.filter_by(user_id=test_user.id).first()
            if not boss_stats:
                print("⚔️ Creating boss battle stats...")
                boss_stats = BossBattleStats(user_id=test_user.id)
                boss_stats.battles_joined = 2
                boss_stats.battles_won = 1
                boss_stats.total_damage_dealt = 350
                boss_stats.total_bullets_used = 35
                db.session.add(boss_stats)
                print(f"✅ Created boss stats for {test_user.name}")
            else:
                print(f"⚔️ Boss stats already exist for {test_user.name}")
            
            db.session.commit()
            
            print("\n" + "=" * 60)
            print("✅ Test data creation complete!")
            print("=" * 60)
            print(f"\n🔑 Test Login Credentials:")
            print(f"   Username: testplayer")
            print(f"   Password: test123")
            print("=" * 60)
            
            return True
            
        except Exception as e:
            print(f"\n❌ Error creating test data: {str(e)}")
            db.session.rollback()
            return False


def show_stats():
    """Show current database statistics"""
    print("\n" + "=" * 60)
    print("Current Database Statistics")
    print("=" * 60)
    
    with app.app_context():
        try:
            total_users = User.query.count()
            total_progress = GameProgress.query.count()
            total_completions = SquareCompletion.query.count()
            total_rooms = BossRoom.query.count()
            active_rooms = BossRoom.query.filter_by(is_active=True).count()
            
            print(f"\n👥 Total Users: {total_users}")
            print(f"🎮 Players with Progress: {total_progress}")
            print(f"✅ Total Square Completions: {total_completions}")
            print(f"🏰 Boss Battle Rooms: {total_rooms} ({active_rooms} active)")
            
            if total_progress > 0:
                avg_position = db.session.query(
                    db.func.avg(GameProgress.current_position)
                ).scalar()
                total_bullets = db.session.query(
                    db.func.sum(GameProgress.bullets)
                ).scalar()
                
                print(f"📊 Average Position: {round(avg_position, 1) if avg_position else 0}")
                print(f"💰 Total Bullets Collected: {total_bullets or 0}")
            
            print("=" * 60)
            
        except Exception as e:
            print(f"\n❌ Error getting stats: {str(e)}")


def reset_all():
    """Reset all game data (WARNING: Destructive!)"""
    print("\n" + "=" * 60)
    print("⚠️  WARNING: RESET ALL GAME DATA")
    print("=" * 60)
    
    confirm = input("\nType 'RESET' to confirm deletion of all game data: ")
    
    if confirm != 'RESET':
        print("❌ Reset cancelled.")
        return
    
    with app.app_context():
        try:
            print("\n🗑️  Deleting all game data...")
            
            SquareCompletion.query.delete()
            BossPlayer.query.delete()
            BossBattleStats.query.delete()
            BossRoom.query.delete()
            GameProgress.query.delete()
            
            db.session.commit()
            
            print("✅ All game data deleted.")
            print("   (User accounts preserved)")
            
        except Exception as e:
            print(f"\n❌ Error during reset: {str(e)}")
            db.session.rollback()


def main():
    """Main function"""
    print("\n🐍 Snakes and Ladders Game - Database Manager\n")
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == 'init':
            init_db()
        elif command == 'test':
            init_db()
            create_test_data()
        elif command == 'stats':
            show_stats()
        elif command == 'reset':
            reset_all()
        else:
            print(f"Unknown command: {command}")
            print_usage()
    else:
        print_usage()


def print_usage():
    """Print usage information"""
    print("Usage: python game_init.py [command]")
    print("\nCommands:")
    print("  init   - Initialize database tables")
    print("  test   - Initialize database and create test data")
    print("  stats  - Show current database statistics")
    print("  reset  - Reset all game data (WARNING: Destructive!)")
    print("\nExample:")
    print("  python game_init.py init")
    print("  python game_init.py test")


if __name__ == '__main__':
    main()