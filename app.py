import os
import logging
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from sqlalchemy.orm import DeclarativeBase
from werkzeug.middleware.proxy_fix import ProxyFix

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Sentry for error tracking (only if DSN is provided)
sentry_dsn = os.environ.get("SENTRY_DSN")
if sentry_dsn:
    sentry_sdk.init(
        dsn=sentry_dsn,
        integrations=[
            FlaskIntegration(),
            SqlalchemyIntegration(),
        ],
        traces_sample_rate=0.1,
        environment=os.environ.get("FLASK_ENV", "production"),
    )
    logger.info("Sentry error tracking initialized")
else:
    logger.info("No Sentry DSN provided, using local logging only")

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

# create the app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key-change-in-production")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1) # needed for url_for to generate with https

# configure the database, relative to the app instance folder
database_url = os.environ.get("DATABASE_URL")
if not database_url:
    # Fallback for development
    database_url = "sqlite:///calendar_ai.db"
    logger.warning("No DATABASE_URL found, using SQLite fallback")

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 280,
    "pool_pre_ping": True,
    "pool_timeout": 30,
    "pool_size": 5,
    "max_overflow": 10,
    "connect_args": {
        "connect_timeout": 30,
        "sslmode": "require",
        "application_name": "calendar_ai",
        "keepalives_idle": "600",
        "keepalives_interval": "30",
        "keepalives_count": "3",
        "options": "-c statement_timeout=30000"  # 30 second statement timeout
    }
}

# initialize the app with the extension, flask-sqlalchemy >= 3.0.x
db.init_app(app)

# Database health check function
def check_db_connection():
    """Check if database connection is healthy and attempt to reconnect if needed"""
    try:
        db.session.execute(db.text('SELECT 1'))
        return True
    except Exception as e:
        logger.warning(f"Database connection check failed: {str(e)}")
        try:
            db.session.rollback()
            db.session.close()
        except Exception:
            pass
        return False

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'google_auth.login'

@login_manager.user_loader
def load_user(user_id):
    from models import User
    try:
        return User.query.get(int(user_id))
    except Exception as e:
        logger.error(f"Error loading user {user_id}: {str(e)}")
        sentry_sdk.capture_exception(e)
        return None

# Global error handlers
@app.errorhandler(404)
def not_found_error(error):
    logger.warning(f"404 error: {request.url}")
    return render_template('error.html', 
                         error_code=404, 
                         error_message="Page not found"), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"500 error: {str(error)}")
    sentry_sdk.capture_exception(error)
    db.session.rollback()
    return render_template('error.html', 
                         error_code=500, 
                         error_message="Internal server error"), 500

@app.errorhandler(Exception)
def handle_exception(e):
    logger.error(f"Unhandled exception: {str(e)}", exc_info=True)
    sentry_sdk.capture_exception(e)
    db.session.rollback()

    # Return JSON error for AJAX requests
    if request.is_json:
        return jsonify({
            'error': 'An unexpected error occurred',
            'message': str(e) if app.debug else 'Please try again later'
        }), 500

    # Return HTML error page for regular requests
    return render_template('error.html', 
                         error_code=500, 
                         error_message="An unexpected error occurred"), 500

with app.app_context():
    # Make sure to import the models here or their tables won't be created
    import models  # noqa: F401

    # Import and register blueprints
    from routes import main_routes
    from google_auth import google_auth
    from mailgun_webhook import mailgun_webhook

    # Register blueprints
    app.register_blueprint(main_routes)
    app.register_blueprint(google_auth)
    app.register_blueprint(mailgun_webhook)

    db.create_all()