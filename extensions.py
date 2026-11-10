import sqlite3
import secrets
import string
from flask import current_app, render_template, url_for
from flask_mail import Mail, Message

mail = Mail()


def getDB():
    """Open a connection to the party database. Rows behave like dicts."""
    conn = sqlite3.connect(current_app.config['DATABASE'])
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def initDB():
    conn = getDB()
    cursor = conn.cursor()

    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fName TEXT NOT NULL,
                    lName TEXT NOT NULL,
                    email TEXT NOT NULL,
                    password TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'user'
                    )''')

    # One row per invitation. password and leaderID stay NULL until the
    # first person in the group registers and becomes the leader.
    # code is TEXT so codes starting with 0 keep their leading zero.
    cursor.execute('''CREATE TABLE IF NOT EXISTS groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    familyName TEXT NOT NULL,
                    groupSize INTEGER NOT NULL,
                    email TEXT NOT NULL,
                    code TEXT NOT NULL UNIQUE,
                    password TEXT,
                    leaderID INTEGER,
                    createdAt TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
                    invitedAt TEXT,
                    registeredAt TEXT
                    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS guests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    groupID INTEGER NOT NULL,
                    fName TEXT NOT NULL,
                    lName TEXT NOT NULL,
                    isLeader BOOLEAN NOT NULL DEFAULT 0,
                    attending BOOLEAN NOT NULL,
                    ageGroup TEXT,
                    dietaries TEXT,
                    dietaryNotes TEXT,
                    drinks TEXT,
                    accessibility TEXT,
                    interest TEXT,
                    FOREIGN KEY (groupID) REFERENCES groups(id) ON DELETE CASCADE
                    )''')

    # Updates the host posts for guests to read on the guest page
    cursor.execute('''CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    body TEXT NOT NULL,
                    createdAt TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime'))
                    )''')

    # Song requests, up to 3 per guest. addedAt is set when the host adds the
    # song to the playlist, which hides it from the host's to-do list.
    cursor.execute('''CREATE TABLE IF NOT EXISTS songs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guestID INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    artist TEXT,
                    createdAt TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
                    addedAt TEXT,
                    FOREIGN KEY (guestID) REFERENCES guests(id) ON DELETE CASCADE
                    )''')

    conn.commit()
    conn.close()


def generateGroupCode(conn, length=6):
    """Return a random numeric code that no other group is using."""
    while True:
        code = ''.join(secrets.choice(string.digits) for _ in range(length))
        exists = conn.execute('SELECT 1 FROM groups WHERE code = ?', (code,)).fetchone()
        if not exists:
            return code


def sendInviteEmail(group):
    """Email a group their invitation, signup code and registration link.

    `group` is a row from the groups table. Must be called inside a request
    so url_for can build an absolute link.
    """
    partyName = current_app.config['PARTY_NAME']
    registerURL = url_for('register', code=group['code'], _external=True)

    context = dict(
        partyName=partyName,
        familyName=group['familyName'],
        groupSize=group['groupSize'],
        code=group['code'],
        registerURL=registerURL,
    )

    msg = Message(f"You're invited to the {partyName}!", recipients=[group['email']])
    msg.body = render_template('emails/invite.txt', **context)
    msg.html = render_template('emails/invite.html', **context)
    mail.send(msg)
