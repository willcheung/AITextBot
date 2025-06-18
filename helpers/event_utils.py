
from datetime import datetime
from helpers.text_processing import sanitize_text_for_db

def prepare_event_data_for_calendar(event):
    """
    Prepare event data for Google Calendar API.
    
    Args:
        event (Event): Event object from database
        
    Returns:
        dict: Event data formatted for Google Calendar
    """
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

    return event_data

def update_event_from_form(event, form_data):
    """
    Update event object with form data.
    
    Args:
        event (Event): Event object to update
        form_data (dict): Form data from request
    """
    # Update event fields with sanitization
    event_name = form_data.get("event_name", "").strip() or "Untitled Event"
    event_description = form_data.get("event_description", "").strip()
    location = form_data.get("location", "").strip()

    event.event_name = sanitize_text_for_db(event_name)
    event.event_description = sanitize_text_for_db(event_description)
    event.location = sanitize_text_for_db(location)

    # Parse dates and times
    start_date_str = form_data.get("start_date")
    if start_date_str:
        event.start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()

    start_time_str = form_data.get("start_time")
    if start_time_str:
        event.start_time = datetime.strptime(start_time_str, '%H:%M').time()
    else:
        event.start_time = None

    end_date_str = form_data.get("end_date")
    if end_date_str:
        event.end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    else:
        event.end_date = event.start_date

    end_time_str = form_data.get("end_time")
    if end_time_str:
        event.end_time = datetime.strptime(end_time_str, '%H:%M').time()
    else:
        event.end_time = None

    event.updated_at = datetime.utcnow()

def format_event_for_api(event):
    """
    Format event object for API response.
    
    Args:
        event (Event): Event object from database
        
    Returns:
        dict: Event data formatted for API response
    """
    return {
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
