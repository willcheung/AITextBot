# Use this Flask blueprint for Google authentication. Do not use flask-dance.

import json
import os

import requests
from app import db
from flask import Blueprint, redirect, request, url_for, session
from flask_login import login_required, login_user, logout_user
from models import User, Event
from oauthlib.oauth2 import WebApplicationClient

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID",
                                  "your-google-client-id")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET",
                                      "your-google-client-secret")
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

# Use relative redirect URL - Flask will handle the domain automatically
REDIRECT_URL = "/google_login/callback"

client = WebApplicationClient(GOOGLE_CLIENT_ID)

google_auth = Blueprint("google_auth", __name__)


@google_auth.route("/google_login")
def login():
    google_provider_cfg = requests.get(GOOGLE_DISCOVERY_URL).json()
    authorization_endpoint = google_provider_cfg["authorization_endpoint"]

    # Store timezone in session for later use during user creation
    timezone = request.args.get('timezone', 'UTC')
    session['user_timezone'] = timezone

    # Store email parameter for new user signup flow
    email = request.args.get('email')
    if email:
        session['signup_email'] = email

    request_uri = client.prepare_request_uri(
        authorization_endpoint,
        redirect_uri=request.url_root.rstrip('/') + REDIRECT_URL,
        scope=[
            "openid", "email", "profile",
            "https://www.googleapis.com/auth/calendar.app.created"
        ],
        access_type="offline",  # Request offline access to get refresh token
        prompt="consent"  # Only prompt for account selection, not consent for returning users
    )
    return redirect(request_uri)


@google_auth.route("/google_login/callback")
def callback():
    code = request.args.get("code")
    google_provider_cfg = requests.get(GOOGLE_DISCOVERY_URL).json()
    token_endpoint = google_provider_cfg["token_endpoint"]

    token_url, headers, body = client.prepare_token_request(
        token_endpoint,
        authorization_response=request.url,
        redirect_url=request.url_root.rstrip('/') + REDIRECT_URL,
        code=code,
    )
    token_response = requests.post(
        token_url,
        headers=headers,
        data=body,
        auth=(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET),
    )

    # Store the token for Google Calendar API access
    token_data = token_response.json()

    client.parse_request_body_response(json.dumps(token_data))

    userinfo_endpoint = google_provider_cfg["userinfo_endpoint"]
    uri, headers, body = client.add_token(userinfo_endpoint)
    userinfo_response = requests.get(uri, headers=headers, data=body)

    userinfo = userinfo_response.json()
    if userinfo.get("email_verified"):
        users_email = userinfo["email"]
        users_name = userinfo["given_name"]
        google_id = userinfo["sub"]
    else:
        return "User email not available or not verified by Google.", 400

    # Get timezone from session
    user_timezone = session.get('user_timezone', 'UTC')

    user = User.query.filter_by(email=users_email).first()
    if not user:
        user = User()
        user.username = users_name
        user.email = users_email
        user.google_id = google_id
        user.timezone = user_timezone
        db.session.add(user)
    else:
        # Check if this is a temp user (no google_id) converting to real user
        is_temp_user_signup = (user.google_id is None)
        
        # Update existing user's fields
        user.timezone = user_timezone
        
        if is_temp_user_signup:
            # Update temp user to real user
            user.google_id = google_id
            user.username = users_name  # Replace temp username with real name

    # Update the Google token for Calendar API access
    user.google_token = json.dumps(token_data)

    # Save refresh token separately for better management
    if token_data.get('refresh_token'):
        user.google_refresh_token = token_data.get('refresh_token')
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Stored refresh token for user {users_email}")

    db.session.commit()

    login_user(user)

    # Check if this is a new signup from email invitation
    signup_email = session.get('signup_email')
    if signup_email and signup_email == users_email:
        # Clear the signup email from session
        session.pop('signup_email', None)

        # Check for any pending events that were extracted from their email
        # This could be implemented by storing temporary events in a separate table
        # or by re-processing their recent emails
        logger.info(f"New user {users_email} signed up after email invitation")

    # Auto-sync events for temp users who just signed up
    if 'is_temp_user_signup' in locals() and is_temp_user_signup:
        try:
            from google_calendar import get_or_create_textbot_calendar, create_calendar_event
            from helpers.event_utils import prepare_event_data_for_calendar
            import logging
            logger = logging.getLogger(__name__)
            
            logger.info(f"Temp user {users_email} signed up, auto-syncing existing events")
            
            # Get user's unsynced events
            unsynced_events = user.events.filter_by(is_synced=False).all()
            
            if unsynced_events:
                # Get access token and create calendar
                access_token = json.loads(user.google_token)['access_token']
                calendar_id = get_or_create_textbot_calendar(user, access_token)
                
                synced_count = 0
                for event in unsynced_events:
                    try:
                        event_data = prepare_event_data_for_calendar(event)
                        google_event_id = create_calendar_event(user, event_data)
                        
                        if google_event_id:
                            event.google_event_id = google_event_id
                            event.is_synced = True
                            synced_count += 1
                            logger.info(f"Auto-synced event '{event.event_name}' for new user")
                    
                    except Exception as sync_error:
                        logger.warning(f"Failed to sync event '{event.event_name}': {str(sync_error)}")
                
                # Commit the sync updates
                db.session.commit()
                logger.info(f"Auto-synced {synced_count}/{len(unsynced_events)} events for new user {users_email}")
        
        except Exception as e:
            logger.error(f"Error auto-syncing events for new user {users_email}: {str(e)}")
            # Don't fail the signup process if sync fails

    return redirect(url_for("main_routes.dashboard"))




@google_auth.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("main_routes.index"))