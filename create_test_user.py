#!/usr/bin/env python3
"""
Create test users for logging in
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from __init__ import app, db
from models.user import User

def create_test_user():
    with app.app_context():
        # ---------------------------------------------------------
        # 1) FIRST USER: testuser (original behavior untouched)
        # ---------------------------------------------------------
        existing_user = User.query.filter_by(_uid='testuser').first()
        if existing_user:
            print("❌ Test user 'testuser' already exists!")
            print(f"   Name: {existing_user.name}")
            print(f"   UID: {existing_user.uid}")
            print(f"   Role: {existing_user.role}")
            print(f"   Password: 123456")
            
            if existing_user.role != 'Admin':
                print("\n🔄 Updating user to Admin role...")
                existing_user.role = 'Admin'
                existing_user.update()
                print("✅ User is now an Admin!")
        else:
            print("Creating test admin user...")
            user = User(
                name="Test User",
                uid="testuser",
                password="123456",
                role="Admin"
            )
            user.create()

            print("\n" + "="*50)
            print("✅ ADMIN USER CREATED SUCCESSFULLY!")
            print("="*50)
            print("Username: testuser")
            print("Password: 123456")
            print("Name: Test User")
            print("Role: Admin")
            print("="*50)

        # ---------------------------------------------------------
        # 2) SECOND USER: testplayer (NEW — regular user)
        # ---------------------------------------------------------
        print("\nChecking for additional test user 'testplayer'...")

        existing_player = User.query.filter_by(_uid='testplayer').first()
        if existing_player:
            print("❌ User 'testplayer' already exists!")
            print(f"   Name: {existing_player.name}")
            print(f"   UID: {existing_player.uid}")
            print(f"   Role: {existing_player.role}")
            print(f"   Password: 123456")
        else:
            print("Creating second test user...")
            player = User(
                name="Test Player",
                uid="testplayer",
                password="123456",
                role="User"    # normal user for gameplay login
            )
            player.create()

            print("\n" + "="*50)
            print("✅ SECOND TEST USER CREATED SUCCESSFULLY!")
            print("="*50)
            print("Username: testplayer")
            print("Password: 123456")
            print("Name: Test Player")
            print("Role: User")
            print("="*50)

        print("\n📌 You now have TWO test accounts ready for your frontend!")
        print("🎮 testuser   (Admin)")
        print("🎮 testplayer (User)\n")


if __name__ == '__main__':
    create_test_user()
