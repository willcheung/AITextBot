import json
import os
import logging
from datetime import datetime, timedelta
import requests
from flask import current_app
import sentry_sdk

logger = logging.getLogger(__name__)

# Database-stored calendar IDs replace caching for better reliability

def check_user_has_calendar_scope(user):
    """
    Check if user has granted the required Google Calendar scope.
    First refreshes the token to ensure we have the latest scope information.

    Args:
        user: User object with google_token

    Returns:
        bool: True if user has calendar scope, False otherwise
    """
    if not user.google_token:
        return False

    try:
        # First, try to refresh the token to get the latest scope information
        try:
            refresh_google_token(user)
            logger.info("Token refreshed successfully before scope check")
        except Exception as refresh_error:
            logger.warning(f"Token refresh failed, proceeding with existing token: {str(refresh_error)}")
            # Continue with existing token if refresh fails

        token_data = json.loads(user.google_token)
        access_token = token_data.get('access_token')

        if not access_token:
            return False

        # Check token info to see if it has calendar scope
        test_response = requests.get(
            f'https://www.googleapis.com/oauth2/v1/tokeninfo?access_token={access_token}',
            timeout=10
        )

        if test_response.status_code == 200:
            token_info = test_response.json()
            scope = token_info.get('scope', '')
            # Check if the token has calendar scope
            has_scope = 'calendar' in scope
            logger.info(f"Scope check result: {has_scope}, available scopes: {scope}")
            return has_scope
        else:
            logger.warning(f"Token info request failed with status {test_response.status_code}")
            return False

    except Exception as e:
        logger.error(f"Error checking calendar scope: {str(e)}")
        return False

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
        from app import db
        token_data = json.loads(user.google_token)
        access_token = token_data.get('access_token')
        refresh_token = token_data.get('refresh_token')

        logger.info(f"Token data keys: {list(token_data.keys())}")
        logger.info(f"Has refresh token: {bool(refresh_token)}")

        if not access_token:
            raise Exception("Invalid Google authentication. Please sign in again")

        # Test the current token by checking if we can access the calendar service
        # Using the tokeninfo endpoint to validate the token without requiring calendar permissions
        test_response = requests.get(
            f'https://www.googleapis.com/oauth2/v1/tokeninfo?access_token={access_token}',
            timeout=10
        )

        if test_response.status_code == 200:
            # Token is still valid, check if it has the right scope
            token_info = test_response.json()
            if 'scope' in token_info and 'calendar' in token_info['scope']:
                return access_token
            else:
                logger.warning("Token doesn't have required calendar scope, attempting refresh")

        # If token is invalid or doesn't have proper scope, attempt refresh
        if refresh_token:
            # Token expired, try to refresh it
            logger.info("Access token expired, attempting to refresh")
            logger.info(f"Using refresh token: {refresh_token[:10]}...")

            refresh_data = {
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
                'client_id': os.environ.get('GOOGLE_OAUTH_CLIENT_ID'),
                'client_secret': os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET'),
            }

            refresh_response = requests.post(
                'https://oauth2.googleapis.com/token',
                data=refresh_data,
                timeout=10
            )

            logger.info(f"Refresh response status: {refresh_response.status_code}")
            if refresh_response.status_code != 200:
                logger.error(f"Refresh response error: {refresh_response.text}")

            if refresh_response.status_code == 200:
                new_token_data = refresh_response.json()

                # Update token data while preserving refresh token
                token_data['access_token'] = new_token_data['access_token']
                if 'refresh_token' in new_token_data:
                    token_data['refresh_token'] = new_token_data['refresh_token']

                # Save updated token to database
                user.google_token = json.dumps(token_data)
                db.session.commit()

                logger.info("Successfully refreshed Google access token")
                return new_token_data['access_token']
            else:
                logger.error(f"Failed to refresh token: {refresh_response.status_code}")
                raise Exception("Google authentication has expired. Please sign in again")
        else:
            # No refresh token or other error
            if test_response.status_code == 401:
                if not refresh_token:
                    logger.warning("No refresh token available, user needs to re-authenticate")
                    raise Exception("Google authentication has expired. Please sign in again with 'Refresh Google Access' button")
                else:
                    logger.error("Token refresh failed or other auth issue")
                    raise Exception("Google authentication has expired. Please sign in again")
            else:
                logger.error(f"Calendar API error: {test_response.status_code} - {test_response.text}")
                raise Exception("Unable to access Google Calendar. Please check your permissions")

    except json.JSONDecodeError:
        raise Exception("Invalid Google authentication data. Please sign in again")
    except requests.exceptions.Timeout:
        raise Exception("Connection timeout. Please try again")
    except Exception as e:
        if "sign in" in str(e).lower() or "expired" in str(e).lower():
            raise e
        else:
            logger.error(f"Unexpected error in token refresh: {str(e)}")
            raise Exception("Google authentication issue. Please sign in again")

def get_or_create_textbot_calendar(user, access_token):
    """
    Get stored Textbot calendar ID or create a new one if needed.
    Validates existing calendar ID and creates new one if invalid.

    Args:
        user: User object with textbot_calendar_id
        access_token: Valid Google access token

    Returns:
        str: Calendar ID for the Calendar Autobot calendar
    """
    from app import db

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }

    # Check if user already has a stored calendar ID and validate it
    if user.textbot_calendar_id:
        logger.info(f"Validating stored Calendar Autobot calendar ID: {user.textbot_calendar_id}")

        try:
            # Test if the stored calendar ID is still valid
            test_response = requests.get(
                f'https://www.googleapis.com/calendar/v3/calendars/{user.textbot_calendar_id}',
                headers=headers,
                timeout=10
            )

            if test_response.status_code == 200:
                logger.info("Stored calendar ID is valid, using it")
                return user.textbot_calendar_id
            else:
                logger.warning(f"Stored calendar ID is invalid (status {test_response.status_code}), will create new calendar")
                user.textbot_calendar_id = None  # Clear invalid ID

        except Exception as e:
            logger.warning(f"Error validating stored calendar ID: {str(e)}, will create new calendar")
            user.textbot_calendar_id = None  # Clear invalid ID

    try:
        # Create new Calendar Autobot calendar since user doesn't have one stored or it's invalid
        logger.info("Creating new Calendar Autobot calendar for user")
        calendar_data = {
            'summary': 'Calendar Autobot',
            'description': 'AI-generated calendar events from email extraction',
            'timeZone': user.timezone
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

            # Store the calendar ID in the user's record
            user.textbot_calendar_id = calendar_id
            db.session.commit()

            logger.info(f"Successfully created and stored Calendar Autobot calendar with ID: {calendar_id}")
            return calendar_id
        else:
            logger.error(f"Failed to create Calendar Autobot calendar: {response.status_code} - {response.text}")
            raise Exception("Failed to create Calendar Autobot calendar")

    except requests.exceptions.Timeout:
        logger.error("Timeout while creating Calendar Autobot calendar")
        raise Exception("Calendar operation timed out. Please try again.")
    except Exception as e:
        logger.error(f"Error creating Calendar Autobot calendar: {str(e)}")
        raise Exception("Failed to create calendar. Please try again.")

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

        # Get or create the Calendar Autobot calendar
        calendar_id = get_or_create_textbot_calendar(user, access_token)

        # Check for duplicate events in the calendar to prevent duplicates
        event_name = event_data.get('event_name', '')
        start_datetime = None
        
        if event_data.get('start_datetime'):
            start_datetime = event_data['start_datetime']
        elif event_data.get('start_date'):
            start_date = event_data['start_date']
            start_time = event_data.get('start_time', '09:00')
            start_datetime = f"{start_date}T{start_time}:00"
        
        if start_datetime and event_name:
            # Parse the datetime to get the date for searching
            try:
                from datetime import datetime as dt
                if 'T' in start_datetime:
                    search_date = start_datetime.split('T')[0]
                else:
                    search_date = start_datetime
                
                # Search for existing events on the same date with similar title
                headers = {'Authorization': f'Bearer {access_token}'}
                search_url = f'https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events'
                search_params = {
                    'timeMin': f'{search_date}T00:00:00Z',
                    'timeMax': f'{search_date}T23:59:59Z',
                    'q': event_name[:50],  # Use first 50 chars for search
                    'singleEvents': True
                }
                
                search_response = requests.get(search_url, headers=headers, params=search_params, timeout=10)
                if search_response.status_code == 200:
                    existing_events = search_response.json().get('items', [])
                    for existing_event in existing_events:
                        existing_title = existing_event.get('summary', '')
                        # Check if titles are very similar (allowing for small differences)
                        if existing_title.lower().strip() == event_name.lower().strip():
                            logger.warning(f"Duplicate event detected: '{event_name}' already exists on {search_date}")
                            return existing_event.get('id')  # Return existing event ID instead of creating duplicate
                            
            except Exception as search_error:
                logger.warning(f"Duplicate check failed, proceeding with creation: {str(search_error)}")

        # Use combined datetime fields if available, otherwise fall back to separate date/time
        if event_data.get('start_datetime') and event_data.get('end_datetime'):
            start_datetime = event_data['start_datetime']
            end_datetime = event_data['end_datetime']
            logger.info(f"Using combined datetime fields: start={start_datetime}, end={end_datetime}")
        else:
            # Fallback to separate date/time fields for backward compatibility
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
            logger.info(f"Using separate date/time fields: start={start_datetime}, end={end_datetime}")

        # Create the calendar event
        calendar_event = {
            "summary": event_data['event_name'],
            "description": event_data.get('event_description', ''),
            "start": {
                "dateTime": start_datetime,
                "timeZone": user.timezone
            },
            "end": {
                "dateTime": end_datetime,
                "timeZone": user.timezone
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

        # Get the Calendar Autobot calendar ID
        calendar_id = get_or_create_textbot_calendar(user, access_token)

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
                "timeZone": user.timezone
            },
            "end": {
                "dateTime": end_datetime,
                "timeZone": user.timezone
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
            data=json.dumps(calendar_event),
            timeout=30
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

        # Get the Calendar Autobot calendar ID
        calendar_id = get_or_create_textbot_calendar(user, access_token)

        headers = {
            'Authorization': f'Bearer {access_token}'
        }

        response = requests.delete(
            f'https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{google_event_id}',
            headers=headers,
            timeout=30
        )

        return response.status_code == 204

    except Exception as e:
        current_app.logger.error(f"Error deleting calendar event: {str(e)}")
        return False
def remove_duplicate_events(user):
    """
    Remove duplicate events from the Calendar Autobot calendar.
    
    Args:
        user: User object with Google token
        
    Returns:
        int: Number of duplicates removed
    """
    try:
        access_token = refresh_google_token(user)
        calendar_id = get_or_create_textbot_calendar(user, access_token)
        
        headers = {'Authorization': f'Bearer {access_token}'}
        
        # Get all events from the calendar
        events_url = f'https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events'
        response = requests.get(events_url, headers=headers, timeout=30)
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch events for duplicate cleanup: {response.status_code}")
            return 0
            
        events = response.json().get('items', [])
        
        # Group events by title and start time
        event_groups = {}
        for event in events:
            title = event.get('summary', '').strip().lower()
            start_time = event.get('start', {}).get('dateTime', '')
            if start_time:
                key = f"{title}|{start_time[:10]}"  # Title + date
                if key not in event_groups:
                    event_groups[key] = []
                event_groups[key].append(event)
        
        # Remove duplicates (keep the first one, delete the rest)
        removed_count = 0
        for key, group in event_groups.items():
            if len(group) > 1:
                # Keep the first event, delete the rest
                for duplicate_event in group[1:]:
                    event_id = duplicate_event.get('id')
                    delete_url = f'https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}'
                    delete_response = requests.delete(delete_url, headers=headers, timeout=10)
                    if delete_response.status_code == 204:
                        removed_count += 1
                        logger.info(f"Removed duplicate event: {duplicate_event.get('summary', 'Unnamed')}")
                    else:
                        logger.warning(f"Failed to remove duplicate event: {delete_response.status_code}")
        
        return removed_count
        
    except Exception as e:
        logger.error(f"Error removing duplicate events: {str(e)}")
        return 0
