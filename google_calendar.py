import json
import os
import logging
from datetime import datetime, timedelta
import requests
from flask import current_app
import sentry_sdk

logger = logging.getLogger(__name__)

# Simple cache for Textbot calendar ID to avoid repeated creation
_textbot_calendar_cache = {}

def refresh_google_token(user):
    """
    Refresh Google OAuth token if needed.
    
    Args:
        user: User object with google_token
    
    Returns:
        str: Valid access token
    """
    if not user.google_token:
        raise Exception("Please sign in with Google to sync events to your calendar")
    
    try:
        token_data = json.loads(user.google_token)
        access_token = token_data.get('access_token')
        
        if not access_token:
            raise Exception("Invalid Google authentication. Please sign in again")
        
        # Test the token by making a simple API call
        test_headers = {'Authorization': f'Bearer {access_token}'}
        test_response = requests.get(
            'https://www.googleapis.com/calendar/v3/users/me/calendarList',
            headers=test_headers,
            timeout=10
        )
        
        if test_response.status_code == 401:
            raise Exception("Google authentication has expired. Please sign in again")
        elif test_response.status_code != 200:
            raise Exception("Unable to access Google Calendar. Please check your permissions")
        
        return access_token
        
    except json.JSONDecodeError:
        raise Exception("Invalid Google authentication data. Please sign in again")
    except requests.exceptions.Timeout:
        raise Exception("Connection timeout. Please try again")
    except Exception as e:
        if "sign in" in str(e).lower():
            raise e
        else:
            raise Exception("Google authentication issue. Please sign in again")

def get_or_create_textbot_calendar(access_token):
    """
    Find existing Textbot calendar or create a new one.
    
    Args:
        access_token: Valid Google access token
    
    Returns:
        str: Calendar ID for the Textbot calendar
    """
    # Check cache first
    cache_key = access_token[:20]  # Use first 20 chars as cache key
    if cache_key in _textbot_calendar_cache:
        logger.info(f"Using cached Textbot calendar ID: {_textbot_calendar_cache[cache_key]}")
        return _textbot_calendar_cache[cache_key]
    
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    
    try:
        # First, list all calendars to find existing Textbot calendar
        logger.info("Searching for existing Textbot calendar")
        response = requests.get(
            'https://www.googleapis.com/calendar/v3/users/me/calendarList',
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            calendars = response.json().get('items', [])
            for calendar in calendars:
                if calendar.get('summary') == 'Textbot':
                    calendar_id = calendar.get('id')
                    logger.info(f"Found existing Textbot calendar with ID: {calendar_id}")
                    _textbot_calendar_cache[cache_key] = calendar_id
                    return calendar_id
        
        # If Textbot calendar doesn't exist, create it
        logger.info("Creating new Textbot calendar")
        calendar_data = {
            'summary': 'Textbot',
            'description': 'AI-generated calendar events from text extraction',
            'timeZone': 'UTC'
        }
        
        response = requests.post(
            'https://www.googleapis.com/calendar/v3/calendars',
            headers=headers,
            data=json.dumps(calendar_data),
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            calendar_id = result.get('id')
            logger.info(f"Successfully created Textbot calendar with ID: {calendar_id}")
            _textbot_calendar_cache[cache_key] = calendar_id
            return calendar_id
        else:
            logger.error(f"Failed to create Textbot calendar: {response.status_code} - {response.text}")
            raise Exception("Failed to create Textbot calendar")
            
    except requests.exceptions.Timeout:
        logger.error("Timeout while managing Textbot calendar")
        raise Exception("Calendar operation timed out. Please try again.")
    except Exception as e:
        logger.error(f"Error managing Textbot calendar: {str(e)}")
        raise Exception("Failed to access calendar. Please try again.")

def create_calendar_event(user, event_data):
    """
    Create an event in Google Calendar.
    
    Args:
        user: User object with Google token
        event_data: Dictionary with event details
    
    Returns:
        str: Google event ID if successful
    """
    try:
        logger.info(f"Creating calendar event: {event_data.get('event_name', 'Unnamed Event')}")
        access_token = refresh_google_token(user)
        
        # Get or create the Textbot calendar
        calendar_id = get_or_create_textbot_calendar(access_token)
        
        # Prepare event data for Google Calendar API
        start_datetime = event_data['start_date']
        end_datetime = event_data.get('end_date', event_data['start_date'])
        
        # Add time if specified
        if event_data.get('start_time'):
            start_datetime += f"T{event_data['start_time']}:00"
        else:
            start_datetime += "T09:00:00"  # Default to 9 AM
        
        if event_data.get('end_time'):
            end_datetime += f"T{event_data['end_time']}:00"
        else:
            # Default to 1 hour duration if no end time specified
            if event_data.get('start_time'):
                start_time = datetime.strptime(event_data['start_time'], '%H:%M')
                end_time = start_time + timedelta(hours=1)
                end_datetime += f"T{end_time.strftime('%H:%M')}:00"
            else:
                end_datetime += "T10:00:00"  # Default to 10 AM
        
        # Create the calendar event
        calendar_event = {
            "summary": event_data['event_name'],
            "description": event_data.get('event_description', ''),
            "start": {
                "dateTime": start_datetime,
                "timeZone": "UTC"
            },
            "end": {
                "dateTime": end_datetime,
                "timeZone": "UTC"
            }
        }
        
        # Add location if specified
        if event_data.get('location'):
            calendar_event["location"] = event_data['location']
        
        # Make API request to Google Calendar
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        logger.info("Making request to Google Calendar API")
        response = requests.post(
            f'https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events',
            headers=headers,
            data=json.dumps(calendar_event),
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            event_id = result.get('id')
            logger.info(f"Successfully created calendar event with ID: {event_id}")
            return event_id
        elif response.status_code == 401:
            logger.error("Google Calendar authentication failed")
            sentry_sdk.capture_message("Google Calendar authentication failed", level="error")
            raise Exception("Google Calendar authentication failed. Please sign in again.")
        elif response.status_code == 403:
            logger.error("Google Calendar permission denied")
            sentry_sdk.capture_message("Google Calendar permission denied", level="error")
            raise Exception("Permission denied. Please ensure Google Calendar access is granted.")
        elif response.status_code == 429:
            logger.warning("Google Calendar rate limit exceeded")
            sentry_sdk.capture_message("Google Calendar rate limit exceeded", level="warning")
            raise Exception("Google Calendar is temporarily busy. Please try again in a moment.")
        else:
            logger.error(f"Google Calendar API error {response.status_code}: {response.text}")
            sentry_sdk.capture_message(f"Google Calendar API error: {response.status_code}", level="error")
            raise Exception(f"Failed to create calendar event. Please try again.")
    
    except requests.exceptions.Timeout:
        logger.error("Google Calendar API timeout")
        sentry_sdk.capture_message("Google Calendar API timeout", level="error")
        raise Exception("Google Calendar request timed out. Please try again.")
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error with Google Calendar API: {str(e)}")
        sentry_sdk.capture_exception(e)
        raise Exception("Network error connecting to Google Calendar. Please check your connection.")
    except Exception as e:
        logger.error(f"Unexpected error creating calendar event: {str(e)}")
        sentry_sdk.capture_exception(e)
        raise Exception(f"Failed to create calendar event: {str(e)}")

def update_calendar_event(user, google_event_id, event_data):
    """
    Update an existing event in Google Calendar.
    
    Args:
        user: User object with Google token
        google_event_id: Google Calendar event ID
        event_data: Dictionary with updated event details
    
    Returns:
        bool: True if successful
    """
    try:
        access_token = refresh_google_token(user)
        
        # Get the Textbot calendar ID
        calendar_id = get_or_create_textbot_calendar(access_token)
        
        # Similar logic as create_calendar_event but for updating
        start_datetime = event_data['start_date']
        end_datetime = event_data.get('end_date', event_data['start_date'])
        
        if event_data.get('start_time'):
            start_datetime += f"T{event_data['start_time']}:00"
        else:
            start_datetime += "T09:00:00"
        
        if event_data.get('end_time'):
            end_datetime += f"T{event_data['end_time']}:00"
        else:
            if event_data.get('start_time'):
                start_time = datetime.strptime(event_data['start_time'], '%H:%M')
                end_time = start_time + timedelta(hours=1)
                end_datetime += f"T{end_time.strftime('%H:%M')}:00"
            else:
                end_datetime += "T10:00:00"
        
        calendar_event = {
            "summary": event_data['event_name'],
            "description": event_data.get('event_description', ''),
            "start": {
                "dateTime": start_datetime,
                "timeZone": "UTC"
            },
            "end": {
                "dateTime": end_datetime,
                "timeZone": "UTC"
            }
        }
        
        if event_data.get('location'):
            calendar_event["location"] = event_data['location']
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        response = requests.put(
            f'https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{google_event_id}',
            headers=headers,
            data=json.dumps(calendar_event)
        )
        
        return response.status_code == 200
    
    except Exception as e:
        current_app.logger.error(f"Error updating calendar event: {str(e)}")
        return False

def delete_calendar_event(user, google_event_id):
    """
    Delete an event from Google Calendar.
    
    Args:
        user: User object with Google token
        google_event_id: Google Calendar event ID
    
    Returns:
        bool: True if successful
    """
    try:
        access_token = refresh_google_token(user)
        
        # Get the Textbot calendar ID
        calendar_id = get_or_create_textbot_calendar(access_token)
        
        headers = {
            'Authorization': f'Bearer {access_token}'
        }
        
        response = requests.delete(
            f'https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{google_event_id}',
            headers=headers
        )
        
        return response.status_code == 204
    
    except Exception as e:
        current_app.logger.error(f"Error deleting calendar event: {str(e)}")
        return False
