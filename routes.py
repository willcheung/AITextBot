import logging
import time
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
    # Get user's events ordered by extraction datetime (oldest first)
    events = Event.query.filter_by(user_id=current_user.id).order_by(Event.created_at.desc()).all()
    text_inputs = TextInput.query.filter_by(user_id=current_user.id).order_by(TextInput.created_at.desc()).limit(10).all()
    
    return render_template("dashboard.html", events=events, text_inputs=text_inputs)

@main_routes.route("/extract_events", methods=["POST"])
@login_required
def extract_events():
    text_input = None
    try:
        logger.info(f"User {current_user.id} starting event extraction")
        text = request.form.get("text", "").strip()
        
        if not text:
            logger.warning(f"User {current_user.id} submitted empty text")
            flash("Please enter some text to extract events from.", "error")
            return redirect(url_for("main_routes.dashboard"))
        
        # Database operation with retry logic
        text_input_created = False
        for attempt in range(3):
            try:
                # Save the text input
                text_input = TextInput()
                text_input.user_id = current_user.id
                text_input.original_text = text
                text_input.source_type = "manual"
                
                db.session.add(text_input)
                db.session.commit()
                text_input_created = True
                break
                
            except Exception as db_error:
                logger.warning(f"Database error on attempt {attempt + 1}: {str(db_error)}")
                db.session.rollback()
                text_input = None
                if attempt == 2:
                    sentry_sdk.capture_exception(db_error)
                    flash("Database connection issue. Please try again in a moment.", "error")
                    return redirect(url_for("main_routes.dashboard"))
                time.sleep(1)  # Brief delay before retry
        
        if not text_input_created or text_input is None:
            flash("Failed to save your text. Please try again.", "error")
            return redirect(url_for("main_routes.dashboard"))
        
        try:
            logger.info("Starting AI event extraction")
            # Extract events using AI
            user_timezone = current_user.timezone if current_user.timezone else "UTC"
            extracted_events, from_email = extract_events_from_text(text, user_timezone=user_timezone)
            
            if from_email and text_input:
                text_input.from_email = from_email
            
            if text_input:
                text_input.extracted_events = extracted_events
                text_input.processing_status = "completed"
            
            # Create Event records for each extracted event
            extraction_time = datetime.utcnow()
            created_events = []
            for event_data in extracted_events:
                try:
                    cleaned_event = validate_and_clean_event(event_data)
                    
                    event = Event()
                    event.user_id = current_user.id
                    if text_input:
                        event.text_input_id = text_input.id
                    event.event_name = cleaned_event['event_name']
                    event.event_description = cleaned_event['event_description']
                    event.extracted_at = extraction_time
                    
                    # Parse dates safely
                    if cleaned_event['start_date']:
                        event.start_date = datetime.strptime(cleaned_event['start_date'], '%Y-%m-%d').date()
                    if cleaned_event['start_time']:
                        event.start_time = datetime.strptime(cleaned_event['start_time'], '%H:%M').time()
                    if cleaned_event['end_date']:
                        event.end_date = datetime.strptime(cleaned_event['end_date'], '%Y-%m-%d').date()
                    if cleaned_event['end_time']:
                        event.end_time = datetime.strptime(cleaned_event['end_time'], '%H:%M').time()
                    
                    event.location = cleaned_event['location']
                    
                    db.session.add(event)
                    created_events.append(event)
                    
                except Exception as e:
                    logger.error(f"Error processing individual event: {str(e)}")
                    flash(f"Skipped one event due to formatting issue: {str(e)}", "warning")
                    continue
            
            # Commit all events with retry logic
            for attempt in range(3):
                try:
                    db.session.commit()
                    break
                except Exception as commit_error:
                    logger.warning(f"Commit error on attempt {attempt + 1}: {str(commit_error)}")
                    db.session.rollback()
                    if attempt == 2:
                        sentry_sdk.capture_exception(commit_error)
                        flash("Failed to save events to database. Please try again.", "error")
                        return redirect(url_for("main_routes.dashboard"))
                    time.sleep(1)
            
            if created_events:
                logger.info(f"Successfully created {len(created_events)} events")
                
                # Auto-sync all extracted events to Google Calendar
                synced_count = 0
                for event in created_events:
                    try:
                        # Prepare event data for Google Calendar
                        event_data = {
                            'event_name': event.event_name,
                            'event_description': event.event_description,
                            'start_date': event.start_date.strftime('%Y-%m-%d') if event.start_date else None,
                            'start_time': event.start_time.strftime('%H:%M') if event.start_time else None,
                            'end_date': event.end_date.strftime('%Y-%m-%d') if event.end_date else None,
                            'end_time': event.end_time.strftime('%H:%M') if event.end_time else None,
                            'location': event.location
                        }
                        
                        # Create event in Google Calendar
                        google_event_id = create_calendar_event(current_user, event_data)
                        if google_event_id:
                            event.google_event_id = google_event_id
                            event.is_synced = True
                            synced_count += 1
                            logger.info(f"Auto-synced event '{event.event_name}' to Google Calendar")
                        
                    except Exception as sync_error:
                        error_msg = str(sync_error)
                        logger.warning(f"Failed to auto-sync event '{event.event_name}': {error_msg}")
                        
                        # If it's an authentication error, provide clear feedback
                        if "sign in" in error_msg.lower() or "authentication" in error_msg.lower():
                            flash(f"Google Calendar sync failed: {error_msg}", "warning")
                            break  # Stop trying other events if auth is broken
                        # Continue with other events for other types of errors
                        continue
                
                # Commit the sync status updates
                try:
                    db.session.commit()
                except Exception as commit_error:
                    logger.error(f"Failed to update sync status: {str(commit_error)}")
                    db.session.rollback()
                
                if synced_count > 0:
                    flash(f"Successfully extracted {len(created_events)} event(s) and synced {synced_count} to your Textbot calendar!", "success")
                else:
                    flash(f"Successfully extracted {len(created_events)} event(s)! Events are ready for manual sync.", "success")
            else:
                flash("No valid events could be extracted from the text.", "warning")
        
        except Exception as e:
            logger.error(f"Event extraction failed: {str(e)}")
            sentry_sdk.capture_exception(e)
            
            # Update text input status
            if text_input:
                try:
                    text_input.processing_status = "failed"
                    text_input.error_message = str(e)
                    db.session.commit()
                except:
                    db.session.rollback()
            
            # Show user-friendly error message based on error type
            error_msg = str(e).lower()
            if "rate limit" in error_msg or "429" in error_msg:
                flash("AI service is busy. Please wait a moment and try again.", "error")
            elif "authentication" in error_msg or "401" in error_msg:
                flash("AI service authentication issue. Please contact support.", "error")
            elif "network" in error_msg or "timeout" in error_msg:
                flash("Network connection issue. Please check your connection and try again.", "error")
            else:
                flash("Unable to extract events from the text. Please try rephrasing or shortening your text.", "error")
    
    except Exception as e:
        logger.error(f"Unexpected error in extract_events: {str(e)}", exc_info=True)
        sentry_sdk.capture_exception(e)
        db.session.rollback()
        flash("An unexpected error occurred. Please try again.", "error")
    
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
        logger.error(f"Error updating event {event_id} for user {current_user.id}: {str(e)}", exc_info=True)
        sentry_sdk.capture_exception(e)
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
        logger.error(f"Error syncing event {event_id} to calendar for user {current_user.id}: {str(e)}", exc_info=True)
        sentry_sdk.capture_exception(e)
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
        logger.error(f"Error deleting event {event_id} for user {current_user.id}: {str(e)}", exc_info=True)
        sentry_sdk.capture_exception(e)
        db.session.rollback()
        flash(f"Error deleting event: {str(e)}", "error")
    
    return redirect(url_for("main_routes.dashboard"))
