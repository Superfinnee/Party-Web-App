import sqlite3
from flask import current_app, url_for
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer

mail = Mail()

def initDB():
    conn = sqlite3.connect(current_app.config['DATABASE'])
    cursor = conn.cursor()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT
                    fName TEXT NOT NULL
                    lName TEXT NOT NULL
                    email TEXT NOT NULL
                    password TEXT NOT NULL
                    status TEXT NOT NULL DEFAULT 'user'
                    ''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS guests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT
                    fName TEXT NOT NULL
                    lName TEXT NOT NULL
                    attending BOOLEAN NOT NULL
                    diataries TEXT
                    interest TEXT
                    FOREIGN KEY (groupID) REFERENCES group(id)
                )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT
                    familyName TEXT NOT NULL
                    password TEXT NOT NULL
                    groupSize INTEGER NOT NULL
                    email TEXT NOT NULL
                    code INTEGER NOT NULL UNIQUE
                    )''')
    
    conn.commit()
    conn.close()
    

def sendConfirmationEmail(userEmail):
    confirm_url = url_for('confirm_email', _external=True)
    msg = Message('Piccoli Party – Confirm Your Email', recipients=[userEmail])
    msg.body = f"Get ready to Party! To start, confirm your email: "
        