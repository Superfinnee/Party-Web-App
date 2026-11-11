import logging
import os
from dotenv import load_dotenv

# Load .env before the Config class is built, otherwise every os.environ.get
# below runs before the variables exist and falls back to its default.
load_dotenv()


def intFromEnv(name, default):
    """Read a whole number from .env, falling back to the default if it's missing or not a number."""
    raw = os.environ.get(name, '').strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logging.getLogger(__name__).warning("%s=%r in .env isn't a number, using %s instead",
                                            name, raw, default)
        return default


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key')
    SECURITY_PASSWORD_SALT = os.environ.get('SECURITY_PASSWORD_SALT', 'default-salt')
    DATABASE = os.environ.get('DATABASE', 'party.db')

    # The host logs in on the same page as guests, using this email and password.
    # HOST_EMAIL falls back to the Gmail address the invites are sent from.
    HOST_EMAIL = (os.environ.get('HOST_EMAIL') or os.environ.get('MAIL_USERNAME') or '').strip().lower()
    HOST_PASSWORD = os.environ.get('HOST_PASSWORD') or os.environ.get('HOST_LOGIN')

    PARTY_NAME = os.environ.get('PARTY_NAME', 'Piccoli Party')
    PARTY_ADDRESS = os.environ.get('PARTY_ADDRESS', '46b Flinders Ave, St Ives')
    PARTY_TIME = os.environ.get('PARTY_TIME', '5PM')

    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = intFromEnv('MAIL_PORT', 587)
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER')

    # Auto deploy from GitHub (the /git_pull route). Only needed on PythonAnywhere;
    # leave WEBHOOK_SECRET out of your local .env and the route stays switched off.
    WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET', '')
    DEPLOY_BRANCH = os.environ.get('DEPLOY_BRANCH', 'Development')
    PA_USERNAME = os.environ.get('PA_USERNAME', '')
    PA_API_TOKEN = os.environ.get('PA_API_TOKEN', '')
    # The web app's address, e.g. yourname.pythonanywhere.com
    PA_DOMAIN = os.environ.get('PA_DOMAIN') or f"{PA_USERNAME}.pythonanywhere.com"
    # Use eu.pythonanywhere.com if your account is on the EU servers
    PA_API_HOST = os.environ.get('PA_API_HOST', 'www.pythonanywhere.com')
