import subprocess, hmac, hashlib, threading, requests, os, sqlite3
from functools import wraps
from flask import Flask, render_template, request, redirect, session, url_for, flash, current_app, abort
from dotenv import load_dotenv
from markupsafe import escape
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from config import Config
from extensions import mail, initDB, verifyConfirmationToken, sendConfirmationEmail
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from datetime import timedelta
from flask_wtf.csrf import CSRFProtect
load_dotenv()

app = Flask(__name__)
app.config.from_object(Config)
mail.init_app(app)

limiter = Limiter(get_remote_address, app=app, default_limits=["15 per minute"])

app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=300)
csrf = CSRFProtect(app)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE='Strict'
)

def login_required(f):
    
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "userID" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session['status'] != "admin":
            flash("You do not have permission to access this page")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapper
        
with app.app_context():
    initDB()

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/register")
def register():
    
    return render_template("register.html")

@admin_required
@app.route("/host")
def host():
    if request.method == "POST":
        return render_template("host.html")
    
    return render_template("host.html")

if __name__ == "__main__":
    app.run(debug=True)
