import logging
import os
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from app import db
from models import User, Event, TextInput
from google_calendar import create_calendar_event, update_calendar_event, delete_calendar_event, check_user_has_calendar_scope
from datetime import datetime
import sentry_sdk

# Import helper modules
from helpers.event_processing import process_text_to_events
from helpers.event_utils import prepare_event_data_for_calendar, update_event_from_form, format_event_for_api

logger = logging.getLogger(__name__)

main_routes = Blueprint("main_routes", __name__)

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

@main_routes.route("/extract_events", methods=["POST"])
@login_required
def extract_events():
    try:
        text = request.form.get("text", "").strip()

        if not text:
            logger.warning(f"User {current_user.id} submitted empty text")
            flash("Please enter some text to extract events from.", "error")
            return redirect(url_for("main_routes.dashboard"))

        # Process text to events using helper function
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
        # Update event using helper function
        update_event_from_form(event, request.form)

        # Update in Google Calendar if already synced
        if event.is_synced and event.google_event_id:
            try:
                event_data = prepare_event_data_for_calendar(event)

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
        event_data = prepare_event_data_for_calendar(event)
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

@main_routes.route('/email-instructions')
def email_instructions():
    """Display email integration instructions"""
    mailgun_domain = os.environ.get("MAILGUN_DOMAIN", "your-domain.com")
    return render_template('email_instructions.html', mailgun_domain=mailgun_domain)

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

        # Process text to events using helper function
        result = process_text_to_events(text, current_user, source_type=source_type, auto_sync=auto_sync)

        # Format response using helper function
        events_data = [format_event_for_api(event) for event in result['events']]

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