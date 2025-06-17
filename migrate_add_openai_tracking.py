
#!/usr/bin/env python3
"""
Migration script to add OpenAI tracking columns to TextInput table.
Run this once to update your existing database.
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from sqlalchemy import text

def migrate_add_openai_tracking():
    """Add OpenAI tracking columns to TextInput table"""
    with app.app_context():
        try:
            # Check if columns already exist
            result = db.session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'text_input' 
                AND column_name IN ('openai_status', 'openai_error_message')
            """)).fetchall()
            
            existing_columns = [row[0] for row in result]
            
            if 'openai_status' not in existing_columns:
                print("Adding openai_status column...")
                db.session.execute(text("""
                    ALTER TABLE text_input 
                    ADD COLUMN openai_status VARCHAR(50) DEFAULT 'pending'
                """))
                
            if 'openai_error_message' not in existing_columns:
                print("Adding openai_error_message column...")
                db.session.execute(text("""
                    ALTER TABLE text_input 
                    ADD COLUMN openai_error_message TEXT
                """))
            
            # Update existing records to have default status
            if 'openai_status' not in existing_columns:
                print("Updating existing records with default status...")
                db.session.execute(text("""
                    UPDATE text_input 
                    SET openai_status = 'success' 
                    WHERE processing_status = 'completed' AND openai_status IS NULL
                """))
            
            db.session.commit()
            print("Migration completed successfully!")
            
        except Exception as e:
            print(f"Migration failed: {str(e)}")
            db.session.rollback()
            raise

if __name__ == "__main__":
    migrate_add_openai_tracking()
