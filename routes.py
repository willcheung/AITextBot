import logging
import time
import html
import re
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from app import db
from models import User, Event, TextInput
from event_extractor import extract_events_from_text, validate_and_clean_event
from google_calendar import create_calendar_event, update_calendar_event, delete_calendar_event, check_user_has_calendar_scope
from datetime import datetime
import sentry_sdk

logger = logging.getLogger(__name__)

main_routes = Blueprint("main_routes", __name__)

def sanitize_text_for_db(text):
    """
    Sanitize text input before saving to database to prevent PostgreSQL conflicts.

    Args:
        text (str): Original text input

    Returns:
        str: Sanitized text safe for database storage
    """
    if not text:
        return text

    # HTML escape to prevent script injection
    sanitized = html.escape(text)

    # Remove or escape PostgreSQL special characters that could cause issues
    # Replace null bytes which PostgreSQL doesn't allow
    sanitized = sanitized.replace('\x00', '')

    # Escape single quotes to prevent SQL injection
    sanitized = sanitized.replace("'", "''")

    # Remove or replace other potentially problematic characters
    # Remove control characters except common whitespace
    sanitized = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', sanitized)

    # Limit length to prevent excessive database storage (adjust as needed)
    max_length = 50000  # 50KB limit
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length] + "... [truncated]"

    return sanitized

@main_routes.route("/health")
def health_check():
    """Health check endpoint for deployment"""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}, 200

@main_routes.route("/health/db")
def db_health_check():
    """Database health check endpoint"""
    try:
        # Test database connection
        result = db.session.execute(db.text('SELECT 1 as test')).fetchone()

        # Get some basic stats
        user_count = db.session.execute(db.text('SELECT COUNT(*) FROM "user"')).scalar()
        event_count = db.session.execute(db.text('SELECT COUNT(*) FROM event')).scalar()

        return {
            "status": "healthy", 
            "database": "connected",
            "test_query": result[0] if result else None,
            "user_count": user_count,
            "event_count": event_count,
            "timestamp": datetime.utcnow().isoformat()
        }, 200
    except Exception as e:
        logger.error(f"Database health check failed: {str(e)}")
        return {
            "status": "unhealthy", 
            "database": "disconnected",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }, 500

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

    # Check if user has granted calendar scope
    has_calendar_scope = check_user_has_calendar_scope(current_user)

    return render_template("dashboard.html", events=events, text_inputs=text_inputs, has_calendar_scope=has_calendar_scope)

def process_text_to_events(text, user, source_type="manual", auto_sync=True):
    """
    Core function to process text and extract events.
    Can be called from web routes, API endpoints, or webhooks.

    Args:
        text (str): Text to process
        user (User): User object
        source_type (str): Source of the text (manual, api, webhook, email)
        auto_sync (bool): Whether to auto-sync to Google Calendar

    Returns:
        dict: Processing results with events, text_input, and sync status
    """
    if not text or not text.strip():
        raise ValueError("Text input is required")

    text = text.strip()
    logger.info(f"User {user.id} processing text of length {len(text)} from {source_type}")

    # Extract events using AI first - use original unsanitized text
    user_timezone = user.timezone if user.timezone else "UTC"

    # Call the extraction function synchronously
    extracted_events, from_email, is_offline, openai_status, openai_error = extract_events_from_text(text, user_timezone=user_timezone)

    # Prepare all database objects
    extraction_time = datetime.utcnow()

    # Sanitize text for database storage
    sanitized_text = sanitize_text_for_db(text)
    sanitized_from_email = sanitize_text_for_db(from_email) if from_email else None

    # Create TextInput record with sanitized data
    text_input = TextInput()
    text_input.user_id = user.id
    text_input.original_text = sanitized_text  # Save sanitized version to database
    text_input.source_type = source_type
    text_input.from_email = sanitized_from_email
    text_input.extracted_events = extracted_events
    text_input.processing_status = "completed"
    text_input.openai_status = openai_status if openai_status else ("offline" if is_offline else "success")
    text_input.openai_error_message = openai_error

    # Create Event records
    created_events = []
    for event_data in extracted_events:
        try:
            cleaned_event = validate_and_clean_event(event_data)

            event = Event()
            event.user_id = user.id
            # Sanitize event data before saving to database
            event.event_name = sanitize_text_for_db(cleaned_event['event_name'])
            event.event_description = sanitize_text_for_db(cleaned_event['event_description'])
            event.extracted_at = extraction_time

            # Parse dates safely - start_date is required by database schema
            if cleaned_event['start_date']:
                event.start_date = datetime.strptime(cleaned_event['start_date'], '%Y-%m-%d').date()
            else:
                # If no start date provided, use today as default (required by DB schema)
                event.start_date = datetime.now().date()

            if cleaned_event['start_time']:
                event.start_time = datetime.strptime(cleaned_event['start_time'], '%H:%M').time()
            if cleaned_event['end_date']:
                event.end_date = datetime.strptime(cleaned_event['end_date'], '%Y-%m-%d').date()
            else:
                # If no end date, use start date
                event.end_date = event.start_date

            if cleaned_event['end_time']:
                event.end_time = datetime.strptime(cleaned_event['end_time'], '%H:%M').time()

            # Store RFC3339 datetime strings for Google Calendar
            event.start_datetime = cleaned_event.get('start_datetime')
            event.end_datetime = cleaned_event.get('end_datetime')
            event.location = sanitize_text_for_db(cleaned_event['location'])

            created_events.append(event)

        except Exception as e:
            logger.error(f"Error processing individual event: {str(e)}")
            logger.error(f"Raw event data: {event_data}")
            sentry_sdk.capture_exception(e)
            continue

    # Save everything to database atomically
    for attempt in range(3):
        try:
            db.session.add(text_input)
            db.session.flush()  # Get the text_input.id

            # Link events to text_input
            for event in created_events:
                event.text_input_id = text_input.id
                db.session.add(event)

            db.session.commit()
            break

        except Exception as commit_error:
            logger.warning(f"Database commit error on attempt {attempt + 1}: {str(commit_error)}")
            try:
                db.session.rollback()
            except Exception as rollback_error:
                logger.error(f"Rollback failed: {str(rollback_error)}")
                try:
                    db.session.close()
                except Exception:
                    pass

            if attempt == 2:
                sentry_sdk.capture_exception(commit_error)
                raise Exception("Failed to save events to database after 3 attempts")
            time.sleep(2 ** attempt)  # Exponential backoff

    logger.info(f"Successfully saved {len(created_events)} events")

    # Auto-sync to Google Calendar if requested
    synced_count = 0
    if auto_sync and created_events:
        for event in created_events:
            try:
                # Prepare event data for Google Calendar
                event_data = {
                    'event_name': event.event_name,
                    'event_description': event.event_description,
                    'location': event.location
                }

                # Use datetime fields if available, otherwise fall back to separate date/time
                if event.start_datetime and event.end_datetime:
                    event_data['start_datetime'] = event.start_datetime
                    event_data['end_datetime'] = event.end_datetime
                else:
                    # Fallback to separate date/time fields
                    if event.start_date:
                        event_data['start_date'] = event.start_date.strftime('%Y-%m-%d')
                    if event.start_time:
                        event_data['start_time'] = event.start_time.strftime('%H:%M')
                    if event.end_date:
                        event_data['end_date'] = event.end_date.strftime('%Y-%m-%d')
                    if event.end_time:
                        event_data['end_time'] = event.end_time.strftime('%H:%M')

                # Create event in Google Calendar
                google_event_id = create_calendar_event(user, event_data)
                if google_event_id:
                    event.google_event_id = google_event_id
                    event.is_synced = True
                    synced_count += 1
                    logger.info(f"Auto-synced event '{event.event_name}' to Google Calendar")

            except Exception as sync_error:
                error_msg = str(sync_error)
                logger.warning(f"Failed to auto-sync event '{event.event_name}': {error_msg}")

                # If it's an authentication error, stop trying other events
                if "sign in" in error_msg.lower() or "authentication" in error_msg.lower():
                    break
                continue

        # Commit sync status updates
        try:
            db.session.commit()
        except Exception as commit_error:
            logger.error(f"Failed to update sync status: {str(commit_error)}")
            db.session.rollback()

    result_dict = {
        'text_input': text_input,
        'events': created_events,
        'synced_count': synced_count,
        'from_email': from_email
    }

    if is_offline:
        result_dict['offline_extraction'] = True

    return result_dict


@main_routes.route("/extract_events", methods=["POST"])
@login_required
def extract_events():
    try:
        text = request.form.get("text", "").strip()

        if not text:
            logger.warning(f"User {current_user.id} submitted empty text")
            flash("Please enter some text to extract events from.", "error")
            return redirect(url_for("main_routes.dashboard"))

        # Process text to events
        result = process_text_to_events(text, current_user, source_type="manual", auto_sync=True)

        events_count = len(result['events'])
        synced_count = result['synced_count']

        if 'offline_extraction' in result and result['offline_extraction']:
            flash("AI service timed out. Extracting events offline. Results may be less accurate.", "warning")


        if events_count > 0:
            if synced_count > 0:
                flash(f"Successfully extracted {events_count} event(s) and synced {synced_count} to your Textbot calendar!", "success")
            else:
                flash(f"Successfully extracted {events_count} event(s)! Events are ready for manual sync.", "success")
        else:
            flash("No valid events could be extracted from the text.", "warning")

    except ValueError as ve:
        logger.warning(f"Validation error for user {current_user.id}: {str(ve)}")
        flash(str(ve), "error")

    except Exception as e:
        logger.error(f"Event extraction failed for user {current_user.id}: {str(e)}")
        sentry_sdk.capture_exception(e)

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
        # Update event fields with sanitization
        event_name = request.form.get("event_name", "").strip() or "Untitled Event"
        event_description = request.form.get("event_description", "").strip()
        location = request.form.get("location", "").strip()

        event.event_name = sanitize_text_for_db(event_name)
        event.event_description = sanitize_text_for_db(event_description)
        event.location = sanitize_text_for_db(location)

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
                    'location': event.location
                }

                # Use datetime fields if available, otherwise fall back to separate date/time
                if event.start_datetime and event.end_datetime:
                    event_data['start_datetime'] = event.start_datetime
                    event_data['end_datetime'] = event.end_datetime
                else:
                    # Fallback to separate date/time fields
                    event_data['start_date'] = event.start_date.strftime('%Y-%m-%d')
                    if event.start_time:
                        event_data['start_time'] = event.start_time.strftime('%H:%M')
                    if event.end_date:
                        event_data['end_date'] = event.end_date.strftime('%Y-%m-%d')
                    if event.end_time:
                        event_data['end_time'] = event.end_time.strftime('%H:%M')

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
            'location': event.location
        }

        # Use datetime fields if available, otherwise fall back to separate date/time
        if event.start_datetime and event.end_datetime:
            event_data['start_datetime'] = event.start_datetime
            event_data['end_datetime'] = event.end_datetime
        else:
            # Fallback to separate date/time fields
            event_data['start_date'] = event.start_date.strftime('%Y-%m-%d')
            if event.start_time:
                event_data['start_time'] = event.start_time.strftime('%H:%M')
            if event.end_date:
                event_data['end_date'] = event.end_date.strftime('%Y-%m-%d')
            if event.end_time:
                event_data['end_time'] = event.end_time.strftime('%H:%M')

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


@main_routes.route('/terms')
def terms():
    """Display Terms of Service page"""
    return render_template('terms.html')


@main_routes.route('/privacy')
def privacy():
    """Display Privacy Policy page"""
    return render_template('privacy.html')


@main_routes.route("/api/extract_events", methods=["POST"])
@login_required
def api_extract_events():
    """
    API endpoint for extracting events from text.
    Can be used by external services, webhooks, or programmatic access.
    """
    try:
        # Get JSON data from request
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        text = data.get("text", "").strip()
        if not text:
            return jsonify({"error": "Text field is required"}), 400

        source_type = data.get("source_type", "api")
        auto_sync = data.get("auto_sync", True)

        # Process text to events
        result = process_text_to_events(text, current_user, source_type=source_type, auto_sync=auto_sync)

        # Format response
        events_data = []
        for event in result['events']:
            event_dict = {
                'id': event.id,
                'event_name': event.event_name,
                'event_description': event.event_description,
                'start_date': event.start_date.strftime('%Y-%m-%d') if event.start_date else None,
                'start_time': event.start_time.strftime('%H:%M') if event.start_time else None,
                'end_date': event.end_date.strftime('%Y-%m-%d') if event.end_date else None,
                'end_time': event.end_time.strftime('%H:%M') if event.end_time else None,
                'start_datetime': event.start_datetime,
                'end_datetime': event.end_datetime,
                'location': event.location,
                'is_synced': event.is_synced,
                'google_event_id': event.google_event_id
            }
            events_data.append(event_dict)

        response = {
            'success': True,
            'text_input_id': result['text_input'].id,
            'events_count': len(result['events']),
            'synced_count': result['synced_count'],
            'from_email': result['from_email'],
            'events': events_data
        }

        if 'offline_extraction' in result and result['offline_extraction']:
            response['offline_extraction'] = True


        return jsonify(response), 200

    except ValueError as ve:
        logger.warning(f"API validation error for user {current_user.id}: {str(ve)}")
        return jsonify({"error": str(ve)}), 400

    except Exception as e:
        logger.error(f"API event extraction failed for user {current_user.id}: {str(e)}")
        sentry_sdk.capture_exception(e)

        # Return appropriate error response
        error_msg = str(e).lower()
        if "rate limit" in error_msg or "429" in error_msg:
            return jsonify({"error": "AI service is busy. Please try again later."}), 429
        elif "authentication" in error_msg or "401" in error_msg:
            return jsonify({"error": "AI service authentication failed."}), 503
        else:
            return jsonify({"error": "Failed to process text. Please try again."}), 500