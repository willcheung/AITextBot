
#!/usr/bin/env python3
"""
Migration script to add google_refresh_token column to User table
Run this once to update existing database schema
"""

from app import app, db
from models import User

def migrate_add_refresh_token():
    with app.app_context():
        try:
            # Check if column already exists
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            columns = [column['name'] for column in inspector.get_columns('user')]
            
            if 'google_refresh_token' not in columns:
                print("Adding google_refresh_token column to User table...")
                with db.engine.connect() as conn:
                    conn.execute(db.text('ALTER TABLE "user" ADD COLUMN google_refresh_token TEXT'))
                    conn.commit()
                print("Column added successfully!")
            else:
                print("Column google_refresh_token already exists.")
                
        except Exception as e:
            print(f"Migration failed: {e}")
            print("Please add the column manually:")
            print('ALTER TABLE "user" ADD COLUMN google_refresh_token TEXT;')

if __name__ == "__main__":
    migrate_add_refresh_token()
