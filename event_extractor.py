import json
import os
import time
import logging
from datetime import datetime, timedelta
import re
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

# the newest OpenAI model is "gpt-4.1-mini".
# do not change this unless explicitly requested by the user
from openai import OpenAI
import sentry_sdk

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "your-openai-api-key")
openai = OpenAI(api_key=OPENAI_API_KEY)

# Centralized prompt template - single place to edit the extraction prompt
EVENT_EXTRACTION_SYS_PROMPT = """You are an expert at extracting calendar events from text. Always respond with valid JSON format. If text is non-English, retain original language as much as possible.

Sometimes the text is content of an email or forwarded email. If it is, use the body of the email for extraction and ignore the To, From and replied threads."""

EVENT_EXTRACTION_PROMPT = """Given the following text, extract all event information. For each event, identify:
- The event name.
- The event description that summarizes this calendar event. Include details like booking codes, confirmation numbers, and other important details for the event.
- The start datetime as a combined date-time value (formatted according to IETF Datatracker RFC3339)
- The end datetime as a combined date-time value (formatted according to IETF Datatracker RFC3339)
- The location (if specified).

Important: If event is a flight itinerary, extract each event and carefully convert timezones:
- Traveler's timezone is {user_timezone}.
- The event name. Add traveler's name(s) from the text into the event name.
- The event description that gives context to this calendar event. Include flight duration and other critical travel details like travel agent contact, confirmation number, booking details.
- The start datetime as a combined date-time value (formatted according to IETF Datatracker RFC3339) converted to traveler's timezone accounting for standard time or daylight saving time.
- The end datetime as a combined date-time value (formatted according to IETF Datatracker RFC3339) converted to traveler's timezone accounting for standard time or daylight saving time.
- The location is departure airport.
- Identify the departure and arrival airport codes or cities.
- Use the known IANA time zones for these airports to determine their timezone offsets for the specified dates. (Example: San Francisco International Airport (SFO) = America/Los_Angeles (UTC-7 during DST in July-August)
Taiwan Taoyuan International Airport (TPE) = Asia/Taipei (UTC+8 year-round))
- Using the departure date and time with departure airport timezone, convert it to traveler's timezone. Consider daylight savings there is a US timezone. The converted date/time will be the event start date and time in traveler's timezone.
- Using the arrival date and time with arrival airport timezone, convert it to traveler's timezone. Consider daylight savings there is a US timezone. The converted date/time will be the event end date and time in traveler's timezone.
- Calculate the flight duration using traveler's timezone.
- If flight duration is explicitly provided in the text, compare and use that duration to confirm or adjust end date/time if needed. The flight time should be exactly the same.

If text is not a flight itinerary, extract text normally.

If a date is relative (e.g., "next Monday," "tomorrow"), first check the email sent date to resolve it. If there's no email sent date, then assume the current date is {current_date} for resolving it.

Provide the output as a JSON object with a "events" key containing a list, where each object in the list represents an event with keys: "event_name", "event_description", "start_date", "start_time", "start_datetime", "end_date", "end_time", "end_datetime", "location". If a piece of information is not found, use null for its value.

Text: '''{text}'''"""


def extract_events_offline(text, from_email=None):
    """
    Offline fallback extraction using basic text parsing.
    This provides a basic extraction when OpenAI API is unavailable.
    """
    logger.info("Using offline extraction fallback")

    events = []

    # Basic pattern matching for common event patterns
    # Look for date patterns
    date_patterns = [
        r'(\w+day),?\s+(\w+)\s+(\d{1,2}),?\s+(\d{4})',  # Monday, January 15, 2024
        r'(\d{1,2})/(\d{1,2})/(\d{4})',  # 1/15/2024
        r'(\d{4})-(\d{1,2})-(\d{1,2})',  # 2024-01-15
    ]

    # Look for time patterns
    time_patterns = [
        r'(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)',  # 2:30 PM
        r'(\d{1,2}):(\d{2})',  # 14:30
    ]

    # Look for event-like phrases
    event_patterns = [
        r'(meeting|conference|appointment|call|interview|lunch|dinner|presentation)',
        r'(flight|departure|arrival|boarding)',
        r'(event|workshop|training|seminar)',
    ]

    # Basic extraction - look for lines with both date/time and event keywords
    lines = text.split('\n')

    for line in lines:
        line = line.strip()
        if len(line) < 10:  # Skip very short lines
            continue

        # Check if line contains date and event patterns
        has_date = any(
            re.search(pattern, line, re.IGNORECASE)
            for pattern in date_patterns)
        has_event = any(
            re.search(pattern, line, re.IGNORECASE)
            for pattern in event_patterns)

        if has_date or has_event:
            # Create a basic event
            event_name = line[:50] + "..." if len(line) > 50 else line
            if from_email:
                event_name = f"{event_name} (from {from_email})"

            events.append({
                "event_name": event_name,
                "event_description": line,
                "start_date": None,
                "start_time": None,
                "start_datetime": None,
                "end_date": None,
                "end_time": None,
                "end_datetime": None,
                "location": None
            })

    # If no events found, create a generic one
    if not events:
        event_name = "Extracted Event"
        if from_email:
            event_name = f"{event_name} (from {from_email})"

        events.append({
            "event_name":
            event_name,
            "event_description":
            text[:200] + "..." if len(text) > 200 else text,
            "start_date":
            None,
            "start_time":
            None,
            "start_datetime":
            None,
            "end_date":
            None,
            "end_time":
            None,
            "end_datetime":
            None,
            "location":
            None
        })

    logger.info(f"Offline extraction created {len(events)} events")
    return events


def call_openai_api(prompt, sys_prompt):
    """
    Make the actual OpenAI API call.
    Separated for timeout handling.
    """
    response = openai.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{
            "role": "system",
            "content": sys_prompt
        }, {
            "role": "user",
            "content": prompt
        }],
        response_format={"type": "json_object"},
        temperature=0.1,
        timeout=25.0)

    content = response.choices[0].message.content
    if not content:
        raise Exception("Empty response from AI service")

    return json.loads(content)


def extract_events_from_text(text, current_date=None, user_timezone="UTC"):
    """
    Extract events from text using OpenAI API with timeout and offline fallback.

    Args:
        text (str): The input text containing event information
        current_date (str): Current date in YYYY-MM-DD format for resolving relative dates
        user_timezone (str): User's timezone for proper time handling

    Returns:
        tuple: (list of extracted events, from_email, is_offline)
    """
    if current_date is None:
        current_date = datetime.now().strftime("%Y-%m-%d")

    # Check if text appears to be an email and extract from address
    from_email = None
    email_match = re.search(r'From:\s*([^\s<]+@[^\s>]+)', text, re.IGNORECASE)
    if email_match:
        from_email = email_match.group(1)
        logger.info(f"Extracted from email: {from_email}")

    # Build the prompt using the centralized template
    sys_prompt = EVENT_EXTRACTION_SYS_PROMPT
    prompt = EVENT_EXTRACTION_PROMPT.format(user_timezone=user_timezone,
                                            current_date=current_date,
                                            text=text)

    try:
        logger.info(f"Extracting events from text of length {len(text)}")

        # Use ThreadPoolExecutor with timeout to call OpenAI API
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(call_openai_api, prompt, sys_prompt)
            try:
                # Wait for 5 seconds maximum
                result = future.result(timeout=5.0)
                events = result.get("events", [])

                # If text is from email, append from email to event names
                if from_email:
                    for event in events:
                        if event.get("event_name"):
                            event[
                                "event_name"] = f"{event['event_name']} (from {from_email})"

                logger.info(
                    f"Successfully extracted {len(events)} events via OpenAI API"
                )
                return events, from_email, False

            except FutureTimeoutError:
                logger.warning(
                    "OpenAI API call timed out after 5 seconds, switching to offline extraction"
                )
                # Cancel the future to clean up
                future.cancel()

                # Use offline extraction
                events = extract_events_offline(text, from_email)
                return events, from_email, True

    except Exception as e:
        error_msg = str(e)
        logger.error(f"OpenAI API error: {error_msg}")

        # Handle specific error types with single notification
        if "429" in error_msg or "rate_limit" in error_msg.lower():
            logger.warning("Rate limit hit - switching to offline extraction")
            sentry_sdk.capture_message("OpenAI rate limit exceeded",
                                       level="warning")
            events = extract_events_offline(text, from_email)
            return events, from_email, True

        elif "401" in error_msg or "authentication" in error_msg.lower():
            logger.error(
                "OpenAI authentication failed - switching to offline extraction"
            )
            sentry_sdk.capture_exception(e)
            events = extract_events_offline(text, from_email)
            return events, from_email, True

        elif "400" in error_msg or "invalid" in error_msg.lower():
            logger.error(
                "Invalid request to OpenAI API - switching to offline extraction"
            )
            sentry_sdk.capture_exception(e)
            events = extract_events_offline(text, from_email)
            return events, from_email, True

        elif "timeout" in error_msg.lower() or "timed out" in error_msg.lower(
        ):
            logger.error(
                "OpenAI API timeout - switching to offline extraction")
            sentry_sdk.capture_exception(e)
            events = extract_events_offline(text, from_email)
            return events, from_email, True

        else:
            logger.error(
                f"Unexpected OpenAI API error: {error_msg} - switching to offline extraction"
            )
            sentry_sdk.capture_exception(e)
            events = extract_events_offline(text, from_email)
            return events, from_email, True


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
        'event_name': safe_strip(event_data.get('event_name'),
                                 'Untitled Event'),
        'event_description': safe_strip(event_data.get('event_description'),
                                        ''),
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

    # Validate and normalize times
    def normalize_time(time_str):
        if not time_str:
            return None

        time_str = str(time_str).strip()
        if not time_str:
            return None

        # Try multiple time formats
        time_formats = [
            '%H:%M',  # 14:30
            '%I:%M %p',  # 2:30 PM
            '%I:%M%p',  # 2:30PM
            '%H:%M:%S',  # 14:30:00
            '%I:%M:%S %p'  # 2:30:00 PM
        ]

        for fmt in time_formats:
            try:
                parsed_time = datetime.strptime(time_str, fmt)
                return parsed_time.strftime(
                    '%H:%M')  # Always return in 24-hour format
            except ValueError:
                continue

        # If no format matches, log the problematic value and raise error
        logger.error(
            f"Unable to parse time format: '{time_str}' - tried formats: {time_formats}"
        )
        raise ValueError(f"Unable to parse time format: {time_str}")

    try:
        cleaned['start_time'] = normalize_time(cleaned['start_time'])
        cleaned['end_time'] = normalize_time(cleaned['end_time'])
    except ValueError as e:
        raise ValueError(f"Invalid time format: {str(e)}")

    # Validate RFC3339 datetime strings if present
    def validate_rfc3339_datetime(dt_str):
        if not dt_str:
            return None
        try:
            # Basic validation - ensure it looks like an RFC3339 datetime
            if 'T' in str(dt_str) and ('+' in str(dt_str)
                                       or '-' in str(dt_str)[-6:]):
                return str(dt_str).strip()
            return None
        except Exception:
            return None

    # Validate datetime fields
    cleaned['start_datetime'] = validate_rfc3339_datetime(
        cleaned.get('start_datetime'))
    cleaned['end_datetime'] = validate_rfc3339_datetime(
        cleaned.get('end_datetime'))

    # If end_date is not specified, use start_date
    if cleaned['start_date'] and not cleaned['end_date']:
        cleaned['end_date'] = cleaned['start_date']

    return cleaned
