# Calendar AI - Text to Calendar Events Service

## Overview

Calendar AI is a Flask-based web application that transforms text input (emails, documents, itineraries) into structured calendar events using AI-powered extraction. The service integrates with Google Calendar to automatically create events and provides a clean, modern interface for users to manage their extracted events.

## System Architecture

### Backend Architecture
- **Framework**: Flask with SQLAlchemy ORM
- **Database**: PostgreSQL with Flask-SQLAlchemy
- **Authentication**: Google OAuth 2.0 with Flask-Login session management
- **AI Integration**: OpenAI GPT-4o for intelligent event extraction
- **Deployment**: Gunicorn WSGI server with autoscaling deployment

### Frontend Architecture
- **Templates**: Jinja2 templating with Bootstrap 5 for responsive design
- **Styling**: Custom CSS with Inter font family and consistent color scheme
- **JavaScript**: Vanilla JS for form validation, animations, and UX enhancements
- **Icons**: Feather Icons for consistent iconography

## Key Components

### 1. User Authentication (`google_auth.py`)
- Google OAuth 2.0 integration using oauthlib
- Automatic token refresh handling
- Calendar API scope permissions
- User session management with Flask-Login

### 2. AI Event Extraction (`event_extractor.py`)
- OpenAI GPT-4o integration for text analysis
- Structured prompt engineering for consistent event extraction
- Relative date resolution based on current date
- Email parsing for sender identification

### 3. Google Calendar Integration (`google_calendar.py`)
- Google Calendar API integration
- Event creation, updating, and deletion
- Token refresh management
- Calendar synchronization tracking

### 4. Database Models (`models.py`)
- **User**: Stores user profile, Google tokens, and relationships
- **Event**: Stores extracted event details with Google Calendar sync status
- **TextInput**: Stores original text inputs for audit trail and reprocessing

### 5. Web Routes (`routes.py`)
- Dashboard for event management
- Text input processing and event extraction
- Event editing and calendar sync operations
- RESTful API endpoints for CRUD operations

## Data Flow

1. **User Authentication**: Users sign in with Google OAuth, granting calendar access
2. **Text Input**: Users paste text (emails, documents) into the web interface
3. **AI Processing**: OpenAI GPT-4o analyzes text and extracts structured event data
4. **Event Storage**: Extracted events are saved to PostgreSQL database
5. **User Review**: Events displayed in card format for user review and editing
6. **Calendar Sync**: Approved events are created in Google Calendar via API
7. **Status Tracking**: Sync status and Google event IDs are tracked in database

## External Dependencies

### Required Services
- **OpenAI API**: GPT-4o model for event extraction
- **Google OAuth 2.0**: User authentication
- **Google Calendar API**: Calendar integration
- **PostgreSQL**: Primary database storage

### Python Packages
- Flask ecosystem (Flask, Flask-SQLAlchemy, Flask-Login)
- Google client libraries (google-auth, google-api-python-client)
- OpenAI Python client
- Authentication libraries (oauthlib, requests)
- Database drivers (psycopg2-binary)

## Deployment Strategy

### Production Setup
- **WSGI Server**: Gunicorn with autoscaling deployment
- **Environment Variables**: 
  - `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`
  - `OPENAI_API_KEY`
  - `DATABASE_URL`
  - `SESSION_SECRET`
- **Database**: PostgreSQL with connection pooling
- **SSL**: HTTPS required for OAuth redirect URIs

### Development Setup
- Local development server with hot reload
- SQLite fallback for local development
- Replit integration with dev domain handling

## User Preferences

Preferred communication style: Simple, everyday language.

## Error Handling & Monitoring

### Comprehensive Error Management
- **Sentry Integration**: Production-ready error tracking with structured logging
- **Rate Limiting Protection**: OpenAI API calls with exponential backoff retry logic
- **Google Calendar Error Handling**: Specific error codes and user-friendly messages
- **Global Exception Handlers**: 404, 500, and unhandled exception catching
- **Structured Logging**: JSON-formatted logs with severity levels

### Error Recovery Features
- **OpenAI Rate Limits**: Automatic retry with 5, 10, 20 second backoff intervals
- **Google API Failures**: Graceful degradation with actionable error messages
- **Database Rollbacks**: Automatic transaction rollback on failures
- **User Feedback**: Clear error messages without exposing technical details

## Changelog

- June 15, 2025:
  - Implemented minimal OAuth scope approach using only "calendar.app.created" permission
  - Added database storage for Textbot calendar IDs to avoid duplicate calendar creation
  - Removed calendar listing permissions while maintaining full functionality
  - Enhanced user model with textbot_calendar_id field for calendar reuse
- June 14, 2025: 
  - Added comprehensive error handling with Sentry logging service integration
  - Implemented dedicated "Textbot" calendar creation and management
  - Fixed None value validation errors in multi-event processing
  - Enhanced Google Calendar integration to use separate calendar for AI-generated events
- June 13, 2025: Initial setup