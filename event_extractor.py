import json
import os
from datetime import datetime, timedelta
import re

# the newest OpenAI model is "gpt-4o" which was released May 13, 2024.
# do not change this unless explicitly requested by the user
from openai import OpenAI

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "your-openai-api-key")
openai = OpenAI(api_key=OPENAI_API_KEY)

def extract_events_from_text(text, current_date=None):
    """
    Extract events from text using OpenAI API following the specified prompt format.
    
    Args:
        text (str): The input text containing event information
        current_date (str): Current date in YYYY-MM-DD format for resolving relative dates
    
    Returns:
        list: List of extracted events as dictionaries
    """
    if current_date is None:
        current_date = datetime.now().strftime("%Y-%m-%d")
    
    # Check if text appears to be an email and extract from address
    from_email = None
    email_match = re.search(r'From:\s*([^\s<]+@[^\s>]+)', text, re.IGNORECASE)
    if email_match:
        from_email = email_match.group(1)
    
    prompt = f"""Given the following text, extract all event information. For each event, identify:
- The event name.
- The event description that gives context to this calendar event.
- The start date (in YYYY-MM-DD format).
- The start time (in HH:MM 24-hour format, if specified).
- The end date (in YYYY-MM-DD format, if specified or different from start date).
- The end time (in HH:MM 24-hour format, if specified).
- The location (if specified).

If a date is relative (e.g., "next Monday," "tomorrow"), assume the current date is {current_date} for resolving it.

Provide the output as a JSON object with a "events" key containing a list, where each object in the list represents an event with keys: "event_name", "event_description", "start_date", "start_time", "end_date", "end_time", "location". If a piece of information is not found, use null for its value.

Text: '''{text}'''"""

    try:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert at extracting calendar events from text. Always respond with valid JSON format."
                },
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        
        result = json.loads(response.choices[0].message.content)
        events = result.get("events", [])
        
        # If text is from email, append from email to event names
        if from_email:
            for event in events:
                if event.get("event_name"):
                    event["event_name"] = f"{event['event_name']} (from {from_email})"
        
        return events, from_email
        
    except Exception as e:
        raise Exception(f"Failed to extract events: {str(e)}")

def validate_and_clean_event(event_data):
    """
    Validate and clean extracted event data.
    
    Args:
        event_data (dict): Raw event data from extraction
    
    Returns:
        dict: Cleaned and validated event data
    """
    cleaned = {
        'event_name': event_data.get('event_name', '').strip() or 'Untitled Event',
        'event_description': event_data.get('event_description', '').strip() or '',
        'start_date': event_data.get('start_date'),
        'start_time': event_data.get('start_time'),
        'end_date': event_data.get('end_date'),
        'end_time': event_data.get('end_time'),
        'location': event_data.get('location', '').strip() or ''
    }
    
    # Validate dates
    try:
        if cleaned['start_date']:
            datetime.strptime(cleaned['start_date'], '%Y-%m-%d')
        if cleaned['end_date']:
            datetime.strptime(cleaned['end_date'], '%Y-%m-%d')
    except ValueError:
        raise ValueError("Invalid date format")
    
    # Validate times
    try:
        if cleaned['start_time']:
            datetime.strptime(cleaned['start_time'], '%H:%M')
        if cleaned['end_time']:
            datetime.strptime(cleaned['end_time'], '%H:%M')
    except ValueError:
        raise ValueError("Invalid time format")
    
    # If end_date is not specified, use start_date
    if cleaned['start_date'] and not cleaned['end_date']:
        cleaned['end_date'] = cleaned['start_date']
    
    return cleaned
