import json
import os
import logging
from datetime import datetime
import re

# the newest OpenAI model is "gpt-4.1".
# do not change this unless explicitly requested by the user
from openai import OpenAI
import sentry_sdk

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "your-openai-api-key")
openai = OpenAI(api_key=OPENAI_API_KEY)

# Centralized prompt template - single place to edit the extraction prompt
EVENT_EXTRACTION_SYS_PROMPT = """You are an expert at extracting calendar events from text. Always respond with valid JSON format. If text is non-English, retain original language as much as possible.

Sometimes the text is content of an email or forwarded email. If it is, use the body of the email for event extraction."""

EVENT_EXTRACTION_PROMPT = """Given the following text, extract all event information. 

If text is a flight itinerary, extract each event and carefully convert timezones:
- Traveler's timezone is {user_timezone}.
- The event name. Add traveler's name(s) from the text into the event name. Also generate one relevant emoji for the event name, given the context of the event.
- The event description that gives context to this calendar event. Include flight duration and other critical travel details like travel agent contact, confirmation number, booking details. If there are multiple travelers, list all of them. Make description easily human readable with new lines and bullet points.
- Identify the departure and arrival airport codes or cities.
- Use the known IANA time zones for these airports to determine their timezone offsets for the specified dates. (Example: San Francisco International Airport (SFO) = America/Los_Angeles (UTC-7 during DST, UTC-8 otherwise)
Taiwan Taoyuan International Airport (TPE) = Asia/Taipei (UTC+8 year-round))
- The start (departure) datetime as a combined date-time value (formatted according to IETF Datatracker RFC3339) converted to traveler's timezone. Consider Daylight Saving Time vs Standard Time where applicable.
- The end (arrival) datetime as a combined date-time value (formatted according to IETF Datatracker RFC3339) converted to traveler's timezone. Consider Daylight Saving Time vs Standard Time where applicable.
- The event location is departure airport.
- IMPORTANT: Always ensure end datetime is after start datetime, especially for international flights.

If text is not a flight itinerary, extract event and identify:
- The event name. Also generate one relevant emoji for the event name, given the context of the event.
- The event description that summarizes this calendar event. Include details like booking codes, confirmation numbers, and other important details for the event. Make description easily human readable with new lines and bullet points.
- The start datetime as a combined date-time value (formatted according to IETF Datatracker RFC3339)
- The end datetime as a combined date-time value (formatted according to IETF Datatracker RFC3339)
- The location (if specified).

If events are recurring, extract each instance of the event.

If same events are repeated in the email or text, extract only one instance of the event and don't include the duplicates in the output.

If a date is relative (e.g., "next Monday," "tomorrow"), first check the email sent date to resolve it. If there's no email sent date, then assume the current date is {current_date} for resolving it.

Provide the output as a JSON object with a "events" key containing a list, where each object in the list represents an event with keys: "event_name", "event_description", "start_date", "start_time", "start_datetime", "end_date", "end_time", "end_datetime", "location", "emoji". If a piece of information is not found, use null for its value.

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
        # Display the prompt for debugging purposes
        #logger.info(sys_prompt)
        #logger.info(prompt)

        # Make synchronous OpenAI API call
        response = openai.chat.completions.create(
            model="gpt-4.1",
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

        # If text is from email, append from email to event description
        if from_email:
            for event in events:
                if event.get("event_description"):
                    event["event_description"] = f"{event['event_description']} \n\n(from {from_email})"

        # Add emojis to event names using OpenAI-generated emoji
        for event in events:
            if event.get("event_name"):
                event["event_name"] = add_emoji_to_event_name(
                    event["event_name"],
                    event.get("emoji")
                )

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


def add_emoji_to_event_name(event_name, emoji=None):
    """
    Add emoji to event name if provided by OpenAI and not already present.

    Args:
        event_name (str): Original event name
        emoji (str): Emoji generated by OpenAI

    Returns:
        str: Event name with emoji prefix if applicable
    """
    if not event_name:
        return event_name

    # If no emoji provided, use default calendar emoji
    if not emoji:
        emoji = "📅"

    # Check if event name already contains an emoji
    # Simple check for common emoji ranges
    import re
    emoji_pattern = re.compile(
        r'[\U0001F600-\U0001F64F]|[\U0001F300-\U0001F5FF]|[\U0001F680-\U0001F6FF]|[\U0001F1E0-\U0001F1FF]|[\U00002600-\U000027BF]|[\U0001F900-\U0001F9FF]'
    )

    if emoji_pattern.search(event_name):
        return event_name

    # Add emoji prefix
    return f"{emoji} {event_name}"