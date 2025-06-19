
import logging
import time
from datetime import datetime
from app import db
from models import User, Event, TextInput
from event_extractor import extract_events_from_text, validate_and_clean_event
from google_calendar import create_calendar_event
from helpers.text_processing import sanitize_text_for_db
import sentry_sdk

logger = logging.getLogger(__name__)

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
                # Skip if already synced (in case of duplicate processing)
                if event.is_synced and event.google_event_id:
                    logger.info(f"Event '{event.event_name}' already synced, skipping")
                    synced_count += 1
                    continue

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

                # Create event in Google Calendar (includes duplicate prevention)
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
