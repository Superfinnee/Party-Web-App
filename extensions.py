import sqlite3
import secrets
import string
from contextlib import contextmanager
from flask import current_app, render_template, url_for
from flask_mail import Mail, Message

mail = Mail()


def getDB():
    """Open a connection to the party database. Rows behave like dicts.

    Prefer `with dbConnection() as conn:`, which also commits, rolls back and
    closes for you.
    """
    conn = sqlite3.connect(current_app.config['DATABASE'])
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
    except sqlite3.Error:
        conn.close()
        raise
    return conn


@contextmanager
def dbConnection():
    """Use the database for one piece of work:

        with dbConnection() as conn:
            conn.execute(...)

    Saves (commits) when the block finishes. If anything inside goes wrong,
    every change from the block is undone (rolled back) so nothing is left
    half-saved, and the error is passed on for the app's error handlers to
    show a friendly message. The connection is always closed.
    """
    conn = getDB()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class NoFreeCodeError(Exception):
    """Raised when a unique signup code can't be found."""


def initDB():
    with dbConnection() as conn:
        createTables(conn)


def createTables(conn):
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
    # extraSlots counts extra people the host has said yes to, but whose
    # details the group hasn't filled in yet. groupSize already includes them.
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
                    registeredAt TEXT,
                    extraSlots INTEGER NOT NULL DEFAULT 0
                    )''')

    # removedAt is set when the host takes someone off the guest list. They
    # aren't deleted, so the group can be told and the host can undo it.
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
                    removedAt TEXT,
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

    # Everything a group sends the host, shown behind the bell on the host page.
    #   kind:   'extraPeople' (asking to bring more people) or 'contact' (a message)
    #   status: 'open' until the host deals with it, then 'approved' or
    #           'declined' (extraPeople) or 'resolved' (contact)
    # extraApproved is how many people the host said yes to, which can differ
    # from extraRequested. hostReply is an optional note back to the group.
    cursor.execute('''CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    groupID INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    body TEXT,
                    extraRequested INTEGER,
                    extraApproved INTEGER,
                    status TEXT NOT NULL DEFAULT 'open',
                    hostReply TEXT,
                    createdAt TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
                    resolvedAt TEXT,
                    FOREIGN KEY (groupID) REFERENCES groups(id) ON DELETE CASCADE
                    )''')

    upgradeOldDatabase(cursor)


def upgradeOldDatabase(cursor):
    """Bring a database made by an earlier version of the app up to date.

    CREATE TABLE IF NOT EXISTS never changes a table that's already there, so
    newer columns are added here. Safe to run every time the app starts.
    """
    def columns(table):
        return {row['name'] for row in cursor.execute(f'PRAGMA table_info({table})')}

    if 'extraSlots' not in columns('groups'):
        cursor.execute('ALTER TABLE groups ADD COLUMN extraSlots INTEGER NOT NULL DEFAULT 0')
    if 'removedAt' not in columns('guests'):
        cursor.execute('ALTER TABLE guests ADD COLUMN removedAt TEXT')

    # Requests for more people used to be stored on the group itself. Move
    # them into notifications, then drop the old columns.
    if 'extraRequestNote' in columns('groups'):
        cursor.execute('''
            INSERT INTO notifications (groupID, kind, body, extraRequested, status, createdAt, resolvedAt)
            SELECT id, 'extraPeople', extraRequestNote, extraRequested,
                   CASE WHEN extraResolvedAt IS NULL THEN 'open' ELSE 'resolved' END,
                   COALESCE(registeredAt, createdAt), extraResolvedAt
            FROM groups
            WHERE extraRequested IS NOT NULL OR extraRequestNote IS NOT NULL''')
        for column in ('extraRequested', 'extraRequestNote', 'extraResolvedAt'):
            cursor.execute(f'ALTER TABLE groups DROP COLUMN {column}')


def generateGroupCode(conn, length=6, attempts=100):
    """Return a random numeric code that no other group is using.

    Gives up after `attempts` tries rather than looping forever if the codes
    ever run out.
    """
    for _ in range(attempts):
        code = ''.join(secrets.choice(string.digits) for _ in range(length))
        exists = conn.execute('SELECT 1 FROM groups WHERE code = ?', (code,)).fetchone()
        if not exists:
            return code
    raise NoFreeCodeError(f"Couldn't find an unused {length}-digit code after {attempts} tries")


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


def sendDecisionEmail(group, notification):
    """Tell a group what the host decided about their request for more people.

    `notification` is the updated row from the notifications table. Must be
    called inside a request so url_for can build absolute links.
    """
    partyName = current_app.config['PARTY_NAME']
    approved = notification['status'] == 'approved'
    context = dict(
        partyName=partyName,
        familyName=group['familyName'],
        approved=approved,
        requested=notification['extraRequested'],
        approvedCount=notification['extraApproved'],
        hostReply=notification['hostReply'],
        registered=bool(group['registeredAt']),
        loginURL=url_for('home', _external=True),
        registerURL=url_for('register', code=group['code'], _external=True),
    )

    subject = (f"Good news about your {partyName} invitation" if approved
               else f"About your {partyName} invitation")
    msg = Message(subject, recipients=[group['email']])
    msg.body = render_template('emails/decision.txt', **context)
    msg.html = render_template('emails/decision.html', **context)
    mail.send(msg)
