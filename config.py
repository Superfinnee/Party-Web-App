import os
from dotenv import load_dotenv

# Load .env before the Config class is built, otherwise every os.environ.get
# below runs before the variables exist and falls back to its default.
load_dotenv()


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
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER')
