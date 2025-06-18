
import html
import re
import logging

logger = logging.getLogger(__name__)

def sanitize_text_for_db(text):
    """
    Sanitize text input before saving to database to prevent PostgreSQL conflicts.

    Args:
        text (str): Original text input

    Returns:
        str: Sanitized text safe for database storage
    """
    if not text:
        return text

    # HTML escape to prevent script injection
    sanitized = html.escape(text)

    # Remove or escape PostgreSQL special characters that could cause issues
    # Replace null bytes which PostgreSQL doesn't allow
    sanitized = sanitized.replace('\x00', '')

    # Escape single quotes to prevent SQL injection
    sanitized = sanitized.replace("'", "''")

    # Remove or replace other potentially problematic characters
    # Remove control characters except common whitespace
    sanitized = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', sanitized)

    # Limit length to prevent excessive database storage (adjust as needed)
    max_length = 50000  # 50KB limit
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length] + "... [truncated]"

    return sanitized
