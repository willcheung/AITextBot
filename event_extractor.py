import json
import os
import logging
from datetime import datetime
import re

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

EVENT_EXTRACTION_PROMPT = """Given the following text, extract all event information. 

If event is a flight itinerary, extract each event and carefully convert timezones:
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

If text is not a flight itinerary, extract event and identify:
- The event name.
- The event description that summarizes this calendar event. Include details like booking codes, confirmation numbers, and other important details for the event.
- The start datetime as a combined date-time value (formatted according to IETF Datatracker RFC3339)
- The end datetime as a combined date-time value (formatted according to IETF Datatracker RFC3339)
- The location (if specified).

If a date is relative (e.g., "next Monday," "tomorrow"), first check the email sent date to resolve it. If there's no email sent date, then assume the current date is {current_date} for resolving it.

Provide the output as a JSON object with a "events" key containing a list, where each object in the list represents an event with keys: "event_name", "event_description", "start_date", "start_time", "start_datetime", "end_date", "end_time", "end_datetime", "location". If a piece of information is not found, use null for its value.

Text: '''{text}'''"""


def extract_events_from_text(text, current_date=None, user_timezone="UTC"):
    """
    Extract events from text using OpenAI API synchronously.

    Args:
        text (str): The input text containing event information
        current_date (str): Current date in YYYY-MM-DD format for resolving relative dates
        user_timezone (str): User's timezone for proper time handling

    Returns:
        tuple: (list of extracted events, from_email, is_offline, openai_status, openai_error)
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
        logger.info(sys_prompt)
        logger.info(prompt)

        # Make synchronous OpenAI API call
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
            timeout=30.0)

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

        logger.info(f"Successfully extracted {len(events)} events via OpenAI API")
        return events, from_email, False, "success", None

    except Exception as e:
        error_msg = str(e)
        logger.error(f"OpenAI API error: {error_msg}")
        sentry_sdk.capture_exception(e)

        # Re-raise the exception to be handled by the calling function
        raise Exception(f"Failed to extract events: {error_msg}")


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
