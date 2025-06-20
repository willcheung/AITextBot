
import os
import logging

logger = logging.getLogger(__name__)

def get_base_domain():
    """
    Get the appropriate base domain for the current environment.
    Returns the full domain without protocol.
    """
    # Check for production domain first if we're in a deployment
    prod_domain = os.environ.get("PRODUCTION_DOMAIN")
    if prod_domain:
        logger.info(f"Using production domain: {prod_domain}")
        return prod_domain
    
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
    """
    return os.environ.get("PRODUCTION_DOMAIN") is not None

def is_development():
    """
    Check if we're running in development environment.
    """
    return os.environ.get("REPLIT_DEV_DOMAIN") is not None or "localhost" in get_base_domain()
