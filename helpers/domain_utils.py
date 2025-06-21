import os
import logging
from flask import current_app

logger = logging.getLogger(__name__)

def get_base_domain():
    """
    Get the appropriate base domain for the current environment.
    Returns the full domain without protocol.
    """
    # Use environment detection to determine correct domain
    if is_production():
        prod_domain = os.environ.get("PRODUCTION_DOMAIN")
        if prod_domain:
            logger.info(f"Using production domain: {prod_domain}")
            return prod_domain
        else:
            logger.warning("Production environment detected but no PRODUCTION_DOMAIN set")
    
    if is_development():
        # Check if we're in Replit development environment
        replit_dev_domain = os.environ.get("REPLIT_DEV_DOMAIN")
        if replit_dev_domain:
            logger.info(f"Using Replit dev domain: {replit_dev_domain}")
            return replit_dev_domain

    # Fallback for local development
    logger.info("Using localhost fallback")
    return "localhost:5000"

def get_base_url():
    """
    Get the full base URL with protocol for the current environment.
    """
    domain = get_base_domain()

    # Use HTTPS for production and Replit domains, HTTP for localhost
    if "localhost" in domain:
        protocol = "http"
    else:
        protocol = "https"

    return f"{protocol}://{domain}"

def get_mailgun_forward_email():
    """
    Get the email address for forwarding based on current domain.
    """
    mailgun_domain = os.environ.get("MAILGUN_DOMAIN")
    if mailgun_domain:
        return f"go@{mailgun_domain}"

    # Fallback
    return "go@calAutobot.com"

def is_production():
    """
    Check if we're running in production environment.
    Uses Flask config and environment variables, not domain settings.
    """
    try:
        # Method 1: Check Flask debug mode (production should have debug=False)
        if hasattr(current_app, 'debug') and not current_app.debug:
            return True

        # Method 2: Check Flask config ENV
        if hasattr(current_app, 'config') and current_app.config.get('ENV') == 'production':
            return True

    except RuntimeError:
        # No Flask app context available, fallback to environment variables
        pass

    # Fallback: Check environment variables (not domain variables)
    return (
        os.environ.get("FLASK_ENV") == "production" or
        os.environ.get("ENVIRONMENT") == "production"
    )

def is_development():
    """
    Check if we're running in development environment.
    Uses Flask config and environment variables, not domain settings.
    """
    try:
        # Method 1: Check Flask debug mode (development should have debug=True)
        if hasattr(current_app, 'debug') and current_app.debug:
            return True

        # Method 2: Check Flask config ENV
        if hasattr(current_app, 'config') and current_app.config.get('ENV') == 'development':
            return True

    except RuntimeError:
        # No Flask app context available, fallback to environment variables
        pass

    # Fallback: Check environment variables (not domain variables)
    return (
        os.environ.get("FLASK_ENV") == "development" or
        os.environ.get("FLASK_DEBUG") == "True" or
        os.environ.get("ENVIRONMENT") == "development" or
        # If no specific environment is set, assume development
        not os.environ.get("FLASK_ENV") and not os.environ.get("ENVIRONMENT")
    )