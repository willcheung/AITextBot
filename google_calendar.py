import json
import os
import logging
from datetime import datetime, timedelta
import requests
from flask import current_app
import sentry_sdk

logger = logging.getLogger(__name__)

# Database-stored calendar IDs replace caching for better reliability

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
    
    Args:
        user: User object with textbot_calendar_id
        access_token: Valid Google access token
    
    Returns:
        str: Calendar ID for the Textbot calendar
    """
    from app import db
    
    # Check if user already has a stored calendar ID
    if user.textbot_calendar_id:
        logger.info(f"Using stored Textbot calendar ID: {user.textbot_calendar_id}")
        return user.textbot_calendar_id
    
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    
    try:
        # Create new Textbot calendar since user doesn't have one stored
        logger.info("Creating new Textbot calendar for user")
        calendar_data = {
            'summary': 'Textbot',
            'description': 'AI-generated calendar events from text extraction',
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
            
            logger.info(f"Successfully created and stored Textbot calendar with ID: {calendar_id}")
            return calendar_id
        else:
            logger.error(f"Failed to create Textbot calendar: {response.status_code} - {response.text}")
            raise Exception("Failed to create Textbot calendar")
            
    except requests.exceptions.Timeout:
        logger.error("Timeout while creating Textbot calendar")
        raise Exception("Calendar operation timed out. Please try again.")
    except Exception as e:
        logger.error(f"Error creating Textbot calendar: {str(e)}")
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
        
        # Get or create the Textbot calendar
        calendar_id = get_or_create_textbot_calendar(user, access_token)
        
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
        
        # Get the Textbot calendar ID
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
        calendar_id = get_or_create_textbot_calendar(user, access_token)
        
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
