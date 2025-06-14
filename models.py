from app import db
from flask_login import UserMixin
from datetime import datetime
import json

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    google_id = db.Column(db.String(100), unique=True, nullable=True)
    google_token = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationship with events
    events = db.relationship('Event', backref='user', lazy=True, cascade='all, delete-orphan')
    text_inputs = db.relationship('TextInput', backref='user', lazy=True, cascade='all, delete-orphan')

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Event details
    event_name = db.Column(db.String(200), nullable=False)
    event_description = db.Column(db.Text)
    start_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time)
    end_date = db.Column(db.Date)
    end_time = db.Column(db.Time)
    location = db.Column(db.String(500))
    
    # Google Calendar integration
    google_event_id = db.Column(db.String(100))
    is_synced = db.Column(db.Boolean, default=False)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    extracted_at = db.Column(db.DateTime, default=datetime.utcnow)  # When this event was extracted from text
    
    # Link to original text input
    text_input_id = db.Column(db.Integer, db.ForeignKey('text_input.id'))

class TextInput(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Input data
    original_text = db.Column(db.Text, nullable=False)
    source_type = db.Column(db.String(50), default='manual')  # manual, email
    from_email = db.Column(db.String(120))  # if source is email
    
    # Processing results
    extracted_events_json = db.Column(db.Text)  # JSON string of extracted events
    processing_status = db.Column(db.String(50), default='pending')  # pending, completed, failed
    error_message = db.Column(db.Text)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationship with events
    events = db.relationship('Event', backref='text_input', lazy=True)
    
    @property
    def extracted_events(self):
        if self.extracted_events_json:
            try:
                return json.loads(self.extracted_events_json)
            except json.JSONDecodeError:
                return []
        return []
    
    @extracted_events.setter
    def extracted_events(self, events_list):
        self.extracted_events_json = json.dumps(events_list)
