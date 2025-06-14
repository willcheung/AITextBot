import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from app import db
from models import User, Event, TextInput
from event_extractor import extract_events_from_text, validate_and_clean_event
from google_calendar import create_calendar_event, update_calendar_event, delete_calendar_event
from datetime import datetime
import sentry_sdk

logger = logging.getLogger(__name__)

main_routes = Blueprint("main_routes", __name__)

@main_routes.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main_routes.dashboard"))
    return render_template("index.html")

@main_routes.route("/dashboard")
@login_required
def dashboard():
    # Get user's events ordered by date
    events = Event.query.filter_by(user_id=current_user.id).order_by(Event.start_date.desc(), Event.start_time.desc()).all()
    text_inputs = TextInput.query.filter_by(user_id=current_user.id).order_by(TextInput.created_at.desc()).limit(10).all()
    
    return render_template("dashboard.html", events=events, text_inputs=text_inputs)

@main_routes.route("/extract_events", methods=["POST"])
@login_required
def extract_events():
    try:
        text = request.form.get("text", "").strip()
        if not text:
            flash("Please enter some text to extract events from.", "error")
            return redirect(url_for("main_routes.dashboard"))
        
        # Save the text input
        text_input = TextInput(
            user_id=current_user.id,
            original_text=text,
            source_type="manual"
        )
        db.session.add(text_input)
        db.session.commit()
        
        try:
            # Extract events using AI
            extracted_events, from_email = extract_events_from_text(text)
            
            if from_email:
                text_input.from_email = from_email
            
            text_input.extracted_events = extracted_events
            text_input.processing_status = "completed"
            
            # Create Event records for each extracted event
            created_events = []
            for event_data in extracted_events:
                try:
                    cleaned_event = validate_and_clean_event(event_data)
                    
                    event = Event(
                        user_id=current_user.id,
                        text_input_id=text_input.id,
                        event_name=cleaned_event['event_name'],
                        event_description=cleaned_event['event_description'],
                        start_date=datetime.strptime(cleaned_event['start_date'], '%Y-%m-%d').date() if cleaned_event['start_date'] else None,
                        start_time=datetime.strptime(cleaned_event['start_time'], '%H:%M').time() if cleaned_event['start_time'] else None,
                        end_date=datetime.strptime(cleaned_event['end_date'], '%Y-%m-%d').date() if cleaned_event['end_date'] else None,
                        end_time=datetime.strptime(cleaned_event['end_time'], '%H:%M').time() if cleaned_event['end_time'] else None,
                        location=cleaned_event['location']
                    )
                    
                    db.session.add(event)
                    created_events.append(event)
                    
                except Exception as e:
                    flash(f"Error processing event: {str(e)}", "error")
                    continue
            
            db.session.commit()
            
            if created_events:
                flash(f"Successfully extracted {len(created_events)} event(s)!", "success")
            else:
                flash("No valid events could be extracted from the text.", "warning")
        
        except Exception as e:
            text_input.processing_status = "failed"
            text_input.error_message = str(e)
            db.session.commit()
            flash(f"Error extracting events: {str(e)}", "error")
    
    except Exception as e:
        flash(f"Error processing request: {str(e)}", "error")
    
    return redirect(url_for("main_routes.dashboard"))

@main_routes.route("/edit_event/<int:event_id>")
@login_required
def edit_event(event_id):
    event = Event.query.filter_by(id=event_id, user_id=current_user.id).first_or_404()
    return render_template("event_form.html", event=event)

@main_routes.route("/update_event/<int:event_id>", methods=["POST"])
@login_required
def update_event(event_id):
    event = Event.query.filter_by(id=event_id, user_id=current_user.id).first_or_404()
    
    try:
        # Update event fields
        event.event_name = request.form.get("event_name", "").strip() or "Untitled Event"
        event.event_description = request.form.get("event_description", "").strip()
        event.location = request.form.get("location", "").strip()
        
        # Parse dates and times
        start_date_str = request.form.get("start_date")
        if start_date_str:
            event.start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        
        start_time_str = request.form.get("start_time")
        if start_time_str:
            event.start_time = datetime.strptime(start_time_str, '%H:%M').time()
        else:
            event.start_time = None
        
        end_date_str = request.form.get("end_date")
        if end_date_str:
            event.end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        else:
            event.end_date = event.start_date
        
        end_time_str = request.form.get("end_time")
        if end_time_str:
            event.end_time = datetime.strptime(end_time_str, '%H:%M').time()
        else:
            event.end_time = None
        
        event.updated_at = datetime.utcnow()
        
        # Update in Google Calendar if already synced
        if event.is_synced and event.google_event_id:
            try:
                event_data = {
                    'event_name': event.event_name,
                    'event_description': event.event_description,
                    'start_date': event.start_date.strftime('%Y-%m-%d'),
                    'start_time': event.start_time.strftime('%H:%M') if event.start_time else None,
                    'end_date': event.end_date.strftime('%Y-%m-%d') if event.end_date else None,
                    'end_time': event.end_time.strftime('%H:%M') if event.end_time else None,
                    'location': event.location
                }
                
                if update_calendar_event(current_user, event.google_event_id, event_data):
                    flash("Event updated successfully in both database and Google Calendar!", "success")
                else:
                    flash("Event updated in database, but failed to update in Google Calendar.", "warning")
            except Exception as e:
                flash(f"Event updated in database, but Google Calendar update failed: {str(e)}", "warning")
        else:
            flash("Event updated successfully!", "success")
        
        db.session.commit()
    
    except Exception as e:
        db.session.rollback()
        flash(f"Error updating event: {str(e)}", "error")
    
    return redirect(url_for("main_routes.dashboard"))

@main_routes.route("/sync_to_calendar/<int:event_id>", methods=["POST"])
@login_required
def sync_to_calendar(event_id):
    event = Event.query.filter_by(id=event_id, user_id=current_user.id).first_or_404()
    
    if event.is_synced:
        flash("Event is already synced to Google Calendar.", "info")
        return redirect(url_for("main_routes.dashboard"))
    
    try:
        event_data = {
            'event_name': event.event_name,
            'event_description': event.event_description,
            'start_date': event.start_date.strftime('%Y-%m-%d'),
            'start_time': event.start_time.strftime('%H:%M') if event.start_time else None,
            'end_date': event.end_date.strftime('%Y-%m-%d') if event.end_date else None,
            'end_time': event.end_time.strftime('%H:%M') if event.end_time else None,
            'location': event.location
        }
        
        google_event_id = create_calendar_event(current_user, event_data)
        
        event.google_event_id = google_event_id
        event.is_synced = True
        db.session.commit()
        
        flash("Event successfully added to Google Calendar!", "success")
    
    except Exception as e:
        flash(f"Error syncing to Google Calendar: {str(e)}", "error")
    
    return redirect(url_for("main_routes.dashboard"))

@main_routes.route("/delete_event/<int:event_id>", methods=["POST"])
@login_required
def delete_event(event_id):
    event = Event.query.filter_by(id=event_id, user_id=current_user.id).first_or_404()
    
    try:
        # Delete from Google Calendar if synced
        if event.is_synced and event.google_event_id:
            try:
                delete_calendar_event(current_user, event.google_event_id)
            except Exception as e:
                flash(f"Warning: Failed to delete from Google Calendar: {str(e)}", "warning")
        
        # Delete from database
        db.session.delete(event)
        db.session.commit()
        
        flash("Event deleted successfully!", "success")
    
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting event: {str(e)}", "error")
    
    return redirect(url_for("main_routes.dashboard"))
