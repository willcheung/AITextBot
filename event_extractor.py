import json
import os
import time
import logging
from datetime import datetime, timedelta
import re

# the newest OpenAI model is "gpt-4.1-mini" which was released May 13, 2024.
# do not change this unless explicitly requested by the user
from openai import OpenAI
import sentry_sdk

logger = logging.getLogger(__name__)

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
        logger.info(f"Extracting events from text of length {len(text)}")
        
        response = openai.chat.completions.create(
            model="gpt-4.1-mini",
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
        
        content = response.choices[0].message.content
        if not content:
            raise Exception("Empty response from AI service")
        
        result = json.loads(content)
        events = result.get("events", [])
        
        # If text is from email, append from email to event names
        if from_email:
            for event in events:
                if event.get("event_name"):
                    event["event_name"] = f"{event['event_name']} (from {from_email})"
        
        logger.info(f"Successfully extracted {len(events)} events")
        return events, from_email
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"OpenAI API error: {error_msg}")
        
        # Handle specific error types
        if "429" in error_msg or "rate_limit" in error_msg.lower():
            logger.warning("Rate limit hit, implementing exponential backoff")
            sentry_sdk.capture_message("OpenAI rate limit exceeded", level="warning")
            
            # Try with exponential backoff (3 attempts)
            for attempt in range(3):
                wait_time = (2 ** attempt) * 5  # 5, 10, 20 seconds
                logger.info(f"Retry attempt {attempt + 1} after {wait_time} seconds")
                time.sleep(wait_time)
                
                try:
                    response = openai.chat.completions.create(
                        model="gpt-4.1-mini",
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
                    
                    retry_content = response.choices[0].message.content
                    if not retry_content:
                        raise Exception("Empty response from AI service on retry")
                    
                    result = json.loads(retry_content)
                    events = result.get("events", [])
                    
                    if from_email:
                        for event in events:
                            if event.get("event_name"):
                                event["event_name"] = f"{event['event_name']} (from {from_email})"
                    
                    logger.info(f"Successfully extracted {len(events)} events on retry {attempt + 1}")
                    return events, from_email
                    
                except Exception as retry_e:
                    logger.error(f"Retry {attempt + 1} failed: {str(retry_e)}")
                    if attempt == 2:  # Last attempt
                        sentry_sdk.capture_exception(retry_e)
                        raise Exception(f"OpenAI API failed after 3 attempts due to rate limiting. Please try again in a few minutes.")
            
        elif "401" in error_msg or "authentication" in error_msg.lower():
            logger.error("OpenAI authentication failed")
            sentry_sdk.capture_exception(e)
            raise Exception("OpenAI API authentication failed. Please check your API key.")
            
        elif "400" in error_msg or "invalid" in error_msg.lower():
            logger.error("Invalid request to OpenAI API")
            sentry_sdk.capture_exception(e)
            raise Exception("Invalid request format. Please try with different text.")
            
        else:
            logger.error(f"Unexpected OpenAI API error: {error_msg}")
            sentry_sdk.capture_exception(e)
            raise Exception(f"AI service temporarily unavailable: {error_msg}")

def validate_and_clean_event(event_data):
    """
    Validate and clean extracted event data.
    
    Args:
        event_data (dict): Raw event data from extraction
    
    Returns:
        dict: Cleaned and validated event data
    """
    # Helper function to safely strip strings
    def safe_strip(value, default=''):
        if value is None:
            return default
        if isinstance(value, str):
            return value.strip() or default
        return str(value).strip() or default
    
    cleaned = {
        'event_name': safe_strip(event_data.get('event_name'), 'Untitled Event'),
        'event_description': safe_strip(event_data.get('event_description'), ''),
        'start_date': event_data.get('start_date'),
        'start_time': event_data.get('start_time'),
        'end_date': event_data.get('end_date'),
        'end_time': event_data.get('end_time'),
        'location': safe_strip(event_data.get('location'), '')
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
