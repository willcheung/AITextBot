import json
import os
from datetime import datetime, timedelta
import requests
from flask import current_app

def refresh_google_token(user):
    """
    Refresh Google OAuth token if needed.
    
    Args:
        user: User object with google_token
    
    Returns:
        str: Valid access token
    """
    if not user.google_token:
        raise Exception("No Google token found for user")
    
    token_data = json.loads(user.google_token)
    
    # Check if token needs refresh (simple check - in production, implement proper token management)
    # For now, we'll assume the token is valid
    return token_data.get('access_token')

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
        access_token = refresh_google_token(user)
        
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
        
        response = requests.post(
            'https://www.googleapis.com/calendar/v3/calendars/primary/events',
            headers=headers,
            data=json.dumps(calendar_event)
        )
        
        if response.status_code == 200:
            result = response.json()
            return result.get('id')
        else:
            current_app.logger.error(f"Google Calendar API error: {response.text}")
            raise Exception(f"Failed to create calendar event: {response.text}")
    
    except Exception as e:
        current_app.logger.error(f"Error creating calendar event: {str(e)}")
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
            f'https://www.googleapis.com/calendar/v3/calendars/primary/events/{google_event_id}',
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
        
        headers = {
            'Authorization': f'Bearer {access_token}'
        }
        
        response = requests.delete(
            f'https://www.googleapis.com/calendar/v3/calendars/primary/events/{google_event_id}',
            headers=headers
        )
        
        return response.status_code == 204
    
    except Exception as e:
        current_app.logger.error(f"Error deleting calendar event: {str(e)}")
        return False
