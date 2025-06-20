import logging
import json
import hmac
import hashlib
import os
from datetime import datetime
from flask import Blueprint, request, jsonify
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from models import User, Event
from helpers.event_processing import process_text_to_events
from helpers.event_utils import format_event_for_api
from app import db
import sentry_sdk

logger = logging.getLogger(__name__)

mailgun_webhook = Blueprint("mailgun_webhook", __name__)

MAILGUN_API_KEY = os.environ.get("MAILGUN_API_KEY", "your-mailgun-api-key")
MAILGUN_DOMAIN = os.environ.get("MAILGUN_DOMAIN", "your-domain.com")
MAILGUN_WEBHOOK_SIGNING_KEY = os.environ.get("MAILGUN_WEBHOOK_SIGNING_KEY", "your-webhook-signing-key")
MAILGUN_API_URL = f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}"

def verify_webhook_signature(token, timestamp, signature):
    """Verify that the webhook request is from Mailgun"""
    if not MAILGUN_WEBHOOK_SIGNING_KEY or MAILGUN_WEBHOOK_SIGNING_KEY == "your-webhook-signing-key":
        logger.warning("Mailgun webhook signing key not configured - skipping verification")
        return True

    value = bytes(timestamp + token, 'utf-8')
    expected_signature = hmac.new(
        key=bytes(MAILGUN_WEBHOOK_SIGNING_KEY, 'utf-8'),
        msg=value,
        digestmod=hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected_signature)

def send_signup_email_with_events(recipient_email, events_data, original_subject=""):
    """Send email to new user with extracted events and signup link"""
    try:
        # Create HTML email with events
        html_content = generate_signup_email_html(events_data, recipient_email, original_subject)

        response = requests.post(
            f"{MAILGUN_API_URL}/messages",
            auth=("api", MAILGUN_API_KEY),
            data={
                "from": f"Calendar Autobot <noreply@{MAILGUN_DOMAIN}>",
                "to": recipient_email,
                "subject": f"Your Calendar Events Extracted - Sign Up to Sync",
                "html": html_content,
                "o:tag": ["signup_invitation", "event_extraction"]
            }
        )

        if response.status_code == 200:
            logger.info(f"Signup email sent successfully to {recipient_email}")
            return True
        else:
            logger.error(f"Failed to send signup email: {response.status_code} {response.text}")
            return False

    except Exception as e:
        logger.error(f"Error sending signup email to {recipient_email}: {str(e)}")
        sentry_sdk.capture_exception(e)
        return False

def generate_signup_email_html(events_data, recipient_email, original_subject):
    """Generate HTML email content with extracted events"""
    base_url = os.environ.get("REPLIT_DEV_DOMAIN", "localhost:5000")
    if base_url and not base_url.startswith("http"):
        base_url = f"https://{base_url}"

    signup_url = f"{base_url}/google_login?email={recipient_email}"

    events_html = ""
    if events_data:
        events_html = "<h3>🗓️ Events I Found:</h3><ul style='list-style: none; padding: 0;'>"
        for event in events_data:
            events_html += f"""
            <li style='background: #f8f9fa; margin: 10px 0; padding: 15px; border-radius: 8px; border-left: 4px solid #007bff;'>
                <strong>{event.get('event_name', 'Untitled Event')}</strong><br>
                <span style='color: #666;'>📅 {event.get('start_date', '')} {event.get('start_time', '')}</span><br>
                {f"📍 {event.get('location', '')}<br>" if event.get('location') else ""}
                {f"<em>{event.get('event_description', '')}</em>" if event.get('event_description') else ""}
            </li>
            """
        events_html += "</ul>"
    else:
        events_html = "<p>I couldn't extract any clear calendar events from your email, but you can still sign up to try our service!</p>"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Your Calendar Events - Calendar Autobot</title>
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">

        <div style="text-align: center; margin-bottom: 30px;">
            <h1 style="color: #007bff; margin-bottom: 10px;">🤖 Calendar Autobot</h1>
            <p style="color: #666; margin: 0;">Transform text into calendar events</p>
        </div>

        <div style="background: white; border-radius: 12px; padding: 30px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">
            <h2 style="margin-top: 0; color: #333;">Hi there! 👋</h2>

            <p>I received your email{f' "{original_subject}"' if original_subject else ''} and used AI to extract calendar events from it.</p>

            {events_html}

            <div style="background: #e7f3ff; padding: 20px; border-radius: 8px; margin: 25px 0;">
                <h3 style="margin-top: 0; color: #0066cc;">🚀 Want these events in your Google Calendar?</h3>
                <p style="margin-bottom: 15px;">Sign up for Calendar Autobot and I'll automatically sync these events to your Google Calendar! We've already saved these events for you - just sign up to claim them.</p>
                <div style="text-align: center;">
                    <a href="{signup_url}" style="display: inline-block; background: #007bff; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: 500;">
                        🔗 Sign Up & Sync Your Events
                    </a>
                </div>
            </div>

            <h3>✨ What you'll get:</h3>
            <ul style="padding-left: 20px;">
                <li>📧 Email any text and get events automatically extracted</li>
                <li>🗓️ Events sync directly to your Google Calendar</li>
                <li>✈️ Smart handling of flight itineraries and travel plans</li>
                <li>🎯 AI-powered event detection from any text format</li>
            </ul>

            <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee; font-size: 14px; color: #666;">
                <p>This email was sent because you emailed our service. If you didn't mean to do this, you can safely ignore this email.</p>
                <p>Questions? Just reply to this email!</p>
            </div>
        </div>

        <div style="text-align: center; margin-top: 20px; font-size: 12px; color: #888;">
            <p>Powered by Calendar Autobot • <a href="{base_url}/privacy" style="color: #888;">Privacy Policy</a> • <a href="{base_url}/terms" style="color: #888;">Terms</a></p>
        </div>

    </body>
    </html>
    """

    return html_content

def send_confirmation_email(recipient_email, events_count, synced_count):
    """Send confirmation email to existing user after processing"""
    try:
        base_url = os.environ.get("REPLIT_DEV_DOMAIN", "localhost:5000")
        if base_url and not base_url.startswith("http"):
            base_url = f"https://{base_url}"

        dashboard_url = f"{base_url}/dashboard"

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Events Processed - Calendar Autobot</title>
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">

            <div style="text-align: center; margin-bottom: 30px;">
                <h1 style="color: #007bff; margin-bottom: 10px;">🤖 Calendar Autobot</h1>
            </div>

            <div style="background: white; border-radius: 12px; padding: 30px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">
                <h2 style="margin-top: 0; color: #333;">✅ Email Processed Successfully!</h2>

                <p>I've processed your email and extracted <strong>{events_count} event(s)</strong>.</p>

                {f"<p>✅ <strong>{synced_count} event(s)</strong> have been automatically synced to your Google Calendar!</p>" if synced_count > 0 else ""}

                <div style="text-align: center; margin: 25px 0;">
                    <a href="{dashboard_url}" style="display: inline-block; background: #007bff; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: 500;">
                        📱 View in Dashboard
                    </a>
                </div>

                <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee; font-size: 14px; color: #666;">
                    <p>Keep sending emails to this address for automatic event extraction!</p>
                </div>
            </div>

        </body>
        </html>
        """

        response = requests.post(
            f"{MAILGUN_API_URL}/messages",
            auth=("api", MAILGUN_API_KEY),
            data={
                "from": f"Calendar Autobot <noreply@{MAILGUN_DOMAIN}>",
                "to": recipient_email,
                "subject": f"✅ {events_count} Event(s) Processed Successfully",
                "html": html_content,
                "o:tag": ["confirmation", "existing_user"]
            }
        )

        if response.status_code == 200:
            logger.info(f"Confirmation email sent successfully to {recipient_email}")
            return True
        else:
            logger.error(f"Failed to send confirmation email: {response.status_code} {response.text}")
            return False

    except Exception as e:
        logger.error(f"Error sending confirmation email to {recipient_email}: {str(e)}")
        sentry_sdk.capture_exception(e)
        return False

@mailgun_webhook.route("/webhook/mailgun", methods=["POST"])
def handle_mailgun_webhook():
    """Handle incoming emails from Mailgun"""
    try:
        # Verify webhook signature
        token = request.form.get('token', '')
        timestamp = request.form.get('timestamp', '')
        signature = request.form.get('signature', '')

        if not verify_webhook_signature(token, timestamp, signature):
            logger.warning("Invalid webhook signature")
            return jsonify({"error": "Invalid signature"}), 401

        # Extract email data
        sender_email = request.form.get('sender', '').lower().strip()
        recipient = request.form.get('recipient', '')
        subject = request.form.get('subject', '')
        body_plain = request.form.get('body-plain', '')
        body_html = request.form.get('body-html', '')

        # Use plain text, fallback to HTML if available
        email_text = body_plain or body_html or ""

        if not sender_email or not email_text.strip():
            logger.warning(f"Missing sender or email content: sender={sender_email}")
            return jsonify({"error": "Missing required email data"}), 400

        logger.info(f"Processing email from {sender_email}, subject: {subject}")

        # Process text to events using helper function
        formatted_text = f"From: {sender_email}\nSubject: {subject}\n\n{email_text}"

        # Check if sender is an existing user
        user = User.query.filter_by(email=sender_email).first()

        if user:
            # Process email for existing user
            try:
                result = process_text_to_events(
                    formatted_text, 
                    user, 
                    source_type="email", 
                    auto_sync=True
                )

                events_count = len(result['events'])
                synced_count = result['synced_count']

                logger.info(f"Processed {events_count} events for existing user {user.id}, synced {synced_count}")

                # Send confirmation email
                send_confirmation_email(sender_email, events_count, synced_count)

                return jsonify({
                    "status": "success",
                    "user_id": user.id,
                    "events_extracted": events_count,
                    "events_synced": synced_count
                }), 200

            except Exception as e:
                logger.error(f"Error processing email for existing user {sender_email}: {str(e)}")
                sentry_sdk.capture_exception(e)
                return jsonify({"error": "Failed to process email"}), 500

        else:
            # Handle new user - extract events, save to database, and send signup email
            try:
                # Create a temporary user record for data storage
                temp_user = User()
                temp_user.email = sender_email
                temp_user.username = f"temp_{sender_email.replace('@', '_').replace('.', '_')}"
                temp_user.timezone = "UTC"
                temp_user.google_id = None
                temp_user.google_token = None

                # Add temp user to session but don't commit yet
                db.session.add(temp_user)
                db.session.flush()  # Get the user ID without committing

                result = process_text_to_events(
                    formatted_text, 
                    temp_user, 
                    source_type="email", 
                    auto_sync=False  # Don't auto-sync for temp users
                )

                events_count = len(result['events'])

                # Commit the database changes
                db.session.commit()

                logger.info(f"Extracted and saved {events_count} events for new user {sender_email}")

                # Format events data for email
                events_data = []
                for event in result['events']:
                    events_data.append({
                        'event_name': event.event_name,
                        'event_description': event.event_description,
                        'start_date': event.start_date.strftime('%Y-%m-%d') if event.start_date else None,
                        'start_time': event.start_time.strftime('%H:%M') if event.start_time else None,
                        'end_date': event.end_date.strftime('%Y-%m-%d') if event.end_date else None,
                        'end_time': event.end_time.strftime('%H:%M') if event.end_time else None,
                        'location': event.location
                    })

                # Send signup email with extracted events
                send_signup_email_with_events(sender_email, events_data, subject)

                return jsonify({
                    "status": "signup_sent",
                    "email": sender_email,
                    "temp_user_id": temp_user.id,
                    "text_input_id": result['text_input'].id,
                    "events_extracted": events_count,
                    "events_saved": events_count
                }), 200

            except Exception as e:
                logger.error(f"Error processing email for new user {sender_email}: {str(e)}")
                sentry_sdk.capture_exception(e)
                db.session.rollback()

                # Send basic signup email even if extraction fails
                send_signup_email_with_events(sender_email, [], subject)
                return jsonify({"status": "signup_sent_with_error"}), 200

    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}")
        sentry_sdk.capture_exception(e)
        return jsonify({"error": "Internal server error"}), 500

@mailgun_webhook.route("/webhook/mailgun/test", methods=["GET", "POST"])
def test_mailgun_webhook():
    """Test endpoint for Mailgun webhook configuration"""
    return jsonify({
        "status": "ok",
        "message": "Mailgun webhook endpoint is working",
        "timestamp": datetime.utcnow().isoformat()
    }), 200