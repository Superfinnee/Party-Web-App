import hmac, re, sqlite3
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, session, url_for, flash, current_app
from werkzeug.exceptions import HTTPException
from werkzeug.security import generate_password_hash, check_password_hash
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect, CSRFError
from config import Config
from extensions import (mail, initDB, dbConnection, generateGroupCode, sendInviteEmail, sendDecisionEmail, NoFreeCodeError)

# The folders are capitalised, so tell Flask where they are. macOS doesn't
# care about case but Linux servers do.
app = Flask(__name__, template_folder="Templates", static_folder="Static")
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

MAX_GROUP_SIZE = 20
MIN_PASSWORD_LENGTH = 8
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Options shown on the register form. The form is built from these lists and
# submissions are checked against them, so add or remove options here.
# Each option is (value, important). Important ones are highlighted on the form.
DIETARY_GROUPS = {
    "Major Food Allergies": [
        ("All Nuts", True), ("Peanuts", False), ("Tree Nuts", False),
        ("Shellfish", False), ("Fish", False), ("Eggs", False),
        ("Soy", False), ("Sesame", False), ("Sulphites", False),
    ],
    "Lifestyle": [
        ("Vegetarian", False), ("Vegan", False), ("Pescetarian", False),
    ],
    "Religious": [
        ("Halal", False), ("Kosher", False),
    ],
    "Medical and Intolerances": [
        ("Gluten-Free", False), ("Coeliac", True), ("Dairy Free", False),
        ("Lactose Intolerant", False), ("Low Sodium", False),
        ("Heart Healthy", False), ("Diabetic Friendly", False),
    ],
}
DIETARY_OPTIONS = {value for options in DIETARY_GROUPS.values() for value, _ in options}

AGE_GROUPS = ["Adult (18+)", "Teen (13-17)", "Child (4-12)", "Toddler (0-3)"]
DRINK_OPTIONS = ["Alcoholic drinks", "Non-alcoholic only"]

SONGS_PER_PERSON = 3


def login_required(f):
    """Only logged-in guests. The host is sent to their own page."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get('status') == 'admin':
            return redirect(url_for('host'))
        if session.get('status') != 'guest' or 'groupID' not in session:
            flash("Please log in to see that page", "error")
            return redirect(url_for("home"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get('status') != "admin":
            flash("Please log in as the host to access that page", "error")
            return redirect(url_for("home"))
        return f(*args, **kwargs)
    return wrapper


def startSession(status, groupID=None):
    """Log someone in. Clearing first stops an old session's data carrying over."""
    session.clear()
    session.permanent = True
    session['status'] = status
    if groupID is not None:
        session['groupID'] = groupID


def landingPage():
    """Where to send someone after an error: their own page, or the login page."""
    status = session.get('status')
    if status == 'admin':
        return url_for('host')
    if status == 'guest':
        return url_for('guest')
    return url_for('home')


@app.template_filter('friendlyTime')
def friendlyTime(value):
    """'2026-11-10T21:47:50' -> '10 Nov, 9:47 PM'"""
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value
    return f"{moment.day} {moment:%b}, {moment.hour % 12 or 12}:{moment:%M %p}"


# Words that already describe the whole group, so "Smith Family" stays as it is
# rather than becoming "Smith Familys".
GROUP_WORDS = {'family', 'families', 'household', 'group', 'crew', 'clan', 'gang', 'team'}


@app.template_filter('pluralName')
def pluralName(name):
    """Make a family name plural for greetings: 'Smith' -> 'Smiths', 'Jones' -> 'Joneses'.

    Family names never change their spelling, so 'Kennedy' -> 'Kennedys' (not
    'Kennedies') and 'Wolf' -> 'Wolfs'. Names ending in s, x, z, ch or sh take 'es'.
    """
    name = str(name or '').strip()
    if not name:
        return name

    lastWord = name.split()[-1]
    if lastWord.lower() in GROUP_WORDS or not lastWord[-1].isalpha():
        return name

    ending = 'es' if lastWord.lower().endswith(('s', 'x', 'z', 'ch', 'sh')) else 's'
    # Keep shouty names shouty: 'JONES' -> 'JONESES'
    if lastWord.isupper() and len(lastWord) > 1:
        ending = ending.upper()
    return name + ending


with app.app_context():
    try:
        initDB()
    except sqlite3.Error:
        # Nothing works without the database, so stop here with a clear reason
        app.logger.critical("Couldn't set up the database at %r. Check DATABASE in .env "
        "and that the folder can be written to.", app.config['DATABASE'])
        raise


# ---------------------------------------------------------------- Error pages
#
# These catch anything the routes don't handle themselves, so a guest sees a
# friendly page or message instead of a crash. Each problem is written to the
# log with its full details for the host to look into.

def showError(title, message, status):
    """Render the friendly error page, or plain text if even that fails."""
    try:
        return render_template("error.html", title=title, message=message, backURL=landingPage()), status
    except Exception:
        current_app.logger.exception("Couldn't show the error page")
        return f"{title}. {message}", status, {'Content-Type': 'text/plain; charset=utf-8'}


@app.errorhandler(404)
@app.errorhandler(405)
def pageNotFound(error):
    return showError("Page not found", "That page doesn't exist. Check the link, or head back to your page.", error.code)


@app.errorhandler(429)
def tooManyRequests(error):
    # Shown as a page rather than a redirect, because redirecting would count
    # as another request and hit the limit again
    return showError("Slow down a little",
                     "There have been too many attempts in a short time. "
                     "Please wait a minute, then try again.", 429)


@app.errorhandler(CSRFError)
def formExpired(error):
    """The form's security token was missing or out of date, usually because
    the page was left open for hours or the session ended."""
    current_app.logger.warning("Rejected form on %s: %s", request.path, error.description)
    flash("That page had been open for a while, so it didn't go through. Please try again.", "error")
    if request.path == url_for('register'):
        return redirect(url_for('register', code=request.form.get('code', '')))
    return redirect(landingPage())


@app.errorhandler(sqlite3.Error)
def databaseError(error):
    """The database couldn't be read or written. dbConnection has already
    undone any half-finished changes."""
    current_app.logger.exception("Database error on %s %s", request.method, request.path)
    if request.method == 'POST':
        flash("Something went wrong and your change wasn't saved. Please try again.", "error")
        return redirect(landingPage())
    return showError("Something went wrong",
                     "We couldn't load this page just now. Please try again in a moment.", 500)


@app.errorhandler(500)
def serverError(error):
    # Flask has already logged the details of what went wrong
    return showError("Something went wrong",
                     "Sorry, something broke on our end. Please try again, "
                     "and let the host know if it keeps happening.", 500)


@app.errorhandler(HTTPException)
def otherHTTPError(error):
    """Any other HTTP error (e.g. 400 Bad Request) gets the same friendly page."""
    return showError(error.name, "That request couldn't be completed. Please go back and try again.",
                     error.code or 500)


# ---------------------------------------------------------------- Login

def passwordMatches(storedHash, password):
    """check_password_hash raises on a damaged hash. Treat that as 'no match'."""
    try:
        return check_password_hash(storedHash, password)
    except (ValueError, TypeError):
        current_app.logger.error("A group has a password hash that can't be read")
        return False


@app.route("/", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def home():
    """One login page for everyone. The host goes to /host, guests to /guest."""
    if session.get('status') == 'admin':
        return redirect(url_for('host'))
    if session.get('status') == 'guest':
        return redirect(url_for('guest'))

    if request.method == "POST":
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        hostEmail = current_app.config.get('HOST_EMAIL')
        hostPassword = current_app.config.get('HOST_PASSWORD')
        if hostEmail and hostPassword and hmac.compare_digest(email.encode(), hostEmail.encode()):
            if hmac.compare_digest(password.encode(), hostPassword.encode()):
                startSession('admin')
                return redirect(url_for('host'))
        else:
            # The host could invite two groups with the same email, so check each one
            with dbConnection() as conn:
                groups = conn.execute(
                    'SELECT id, password FROM groups WHERE email = ? AND password IS NOT NULL',
                    (email,)).fetchall()
            for group in groups:
                if passwordMatches(group['password'], password):
                    startSession('guest', group['id'])
                    return redirect(url_for('guest'))

        flash("Incorrect email or password", "error")

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You've been logged out", "success")
    return redirect(url_for('home'))


# ---------------------------------------------------------------- Guest page

def getGroupGuests(conn, groupID):
    """The logged-in group's guests, each with their song requests attached."""
    guests = [dict(row) for row in conn.execute(
        'SELECT * FROM guests WHERE groupID = ? ORDER BY isLeader DESC, id', (groupID,))]
    for guest in guests:
        guest['songs'] = conn.execute(
            'SELECT * FROM songs WHERE guestID = ? ORDER BY id', (guest['id'],)).fetchall()
        guest['slotsLeft'] = max(SONGS_PER_PERSON - len(guest['songs']), 0)
    return guests


def renderGuestPage(status=200):
    """Build the logged-in group's page. Forms that fail validation call this
    too, so the page comes back with what they typed still filled in."""
    with dbConnection() as conn:
        group = conn.execute('SELECT * FROM groups WHERE id = ?', (session['groupID'],)).fetchone()
        if group is None:
            session.clear()
            flash("Your group could not be found. Please contact the host.", "error")
            return redirect(url_for('home'))

        guests = getGroupGuests(conn, group['id'])
        messages = conn.execute('SELECT * FROM messages ORDER BY createdAt DESC, id DESC').fetchall()
        notifications = conn.execute(
            'SELECT * FROM notifications WHERE groupID = ? ORDER BY createdAt DESC, id DESC',
            (group['id'],)).fetchall()

    activeGuests = [g for g in guests if not g['removedAt']]
    removedGuests = [g for g in guests if g['removedAt']]
    # Most recent yes from the host, so its note can go with the "add people" form
    latestApproval = next((n for n in notifications if n['status'] == 'approved'), None)

    return render_template("guest.html", group=group, guests=activeGuests,
            removedGuests=removedGuests, removedEntirely=isRemovedEntirely(guests),
            messages=messages, notifications=notifications,
            latestApproval=latestApproval, songsPerPerson=SONGS_PER_PERSON,
            dietaryGroups=DIETARY_GROUPS, ageGroups=AGE_GROUPS,
            drinkOptions=DRINK_OPTIONS, maxGroupSize=MAX_GROUP_SIZE), status


def isRemovedEntirely(guests):
    """True when the host has taken everyone in the group off the guest list."""
    return bool(guests) and all(g['removedAt'] for g in guests)


@app.route("/guest")
@login_required
def guest():
    return renderGuestPage()


@app.route("/guest/extra", methods=["POST"])
@login_required
def addExtraGuests():
    """Details for the extra people the host said yes to."""
    with dbConnection() as conn:
        group = conn.execute('SELECT * FROM groups WHERE id = ?', (session['groupID'],)).fetchone()
        removed = group is not None and isRemovedEntirely(getGroupGuests(conn, group['id']))

    if group is None or removed or not group['extraSlots']:
        flash("There aren't any extra spots to fill in.", "error")
        return redirect(url_for('guest'))

    slots = group['extraSlots']
    members, errors = [], []
    for i in range(slots):
        member, memberErrors = parseMember(i, isLeader=False, label=f"Extra guest {i + 1}")
        members.append(member)
        errors.extend(memberErrors)

    if errors:
        for error in errors:
            flash(error, "error")
        return renderGuestPage(400)

    try:
        with dbConnection() as conn:
            # Only succeeds once, so a double-click can't add everyone twice
            claimed = conn.execute(
                'UPDATE groups SET extraSlots = 0 WHERE id = ? AND extraSlots = ?',
                (group['id'], slots)).rowcount
            if claimed != 1:
                conn.rollback()
                flash("Those people have already been added.", "error")
                return redirect(url_for('guest'))
            for member in members:
                insertGuest(conn, group['id'], member)
    except sqlite3.Error:
        current_app.logger.exception("Couldn't add extra guests for group %s", group['id'])
        flash("Something went wrong, so nobody was added. Your answers are still below. "
              "Please try again.", "error")
        return renderGuestPage(500)

    names = [m['fName'] for m in members]
    nameList = names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]
    flash(f"Thanks! {nameList} {'has' if len(names) == 1 else 'have'} been added to your group.", "success")
    return redirect(url_for('guest'))


@app.route("/guest/contact", methods=["POST"])
@login_required
def contactHost():
    """A message from the group to the host, optionally asking to bring more people."""
    extraCount, message, errors = parseExtraRequest(countField='extraRequested', noteField='message')
    if not message and not extraCount:
        errors.append("Write a message for the host first.")

    if errors:
        for error in errors:
            flash(error, "error")
        return renderGuestPage(400)

    try:
        with dbConnection() as conn:
            conn.execute('INSERT INTO notifications (groupID, kind, body, extraRequested) VALUES (?, ?, ?, ?)',
            (session['groupID'], 'extraPeople' if extraCount else 'contact',
            message, extraCount))
    except sqlite3.Error:
        current_app.logger.exception("Couldn't save a message from group %s", session['groupID'])
        flash("Your message couldn't be sent just now. It's still below, so please try again.", "error")
        return renderGuestPage(500)

    flash("Your message has been sent to the host. Any reply will show up here.", "success")
    return redirect(url_for('guest', _anchor='contact'))


@app.route("/guest/songs", methods=["POST"])
@login_required
def addSongs():
    """Song fields are named title_<guestID> / artist_<guestID>, one pair per empty slot."""
    added = 0
    try:
        with dbConnection() as conn:
            # Only loop over this group's own attending guests, so a tampered form
            # can't add songs for someone in another group.
            for guest in getGroupGuests(conn, session['groupID']):
                if not guest['attending'] or guest['removedAt']:
                    continue
                titles = request.form.getlist(f"title_{guest['id']}")
                artists = request.form.getlist(f"artist_{guest['id']}")
                slotsLeft = guest['slotsLeft']

                for title, artist in zip(titles, artists):
                    title, artist = title.strip()[:150], artist.strip()[:150]
                    if not title or slotsLeft <= 0:
                        continue
                    conn.execute('INSERT INTO songs (guestID, title, artist) VALUES (?, ?, ?)',
                    (guest['id'], title, artist or None))
                    slotsLeft -= 1
                    added += 1
    except sqlite3.Error:
        # All of the songs are undone together, so none are half-saved
        current_app.logger.exception("Couldn't save songs for group %s", session['groupID'])
        flash("Your songs couldn't be sent just now. Please try again.", "error")
        return redirect(url_for('guest', _anchor='songs'))

    if added:
        flash(f"Thanks! {added} {'song' if added == 1 else 'songs'} sent to the DJ.", "success")
    else:
        flash("Enter at least one song title.", "error")
    return redirect(url_for('guest', _anchor='songs'))


@app.route("/guest/songs/<int:songID>/delete", methods=["POST"])
@login_required
def deleteSong(songID):
    """Guests can take back a request until the host has added it to the playlist."""
    with dbConnection() as conn:
        deleted = conn.execute('''
            DELETE FROM songs
            WHERE id = ? AND addedAt IS NULL
              AND guestID IN (SELECT id FROM guests WHERE groupID = ?)''',
            (songID, session['groupID'])).rowcount

    if not deleted:
        flash("That song is already on the playlist, so it can't be removed.", "error")
    return redirect(url_for('guest', _anchor='songs'))


# ---------------------------------------------------------------- Host


@app.route("/host", methods=["GET", "POST"])
@admin_required
def host():
    if request.method == "POST":
        return addGroup()

    with dbConnection() as conn:
        # Counts leave out anyone the host has removed from the guest list
        groups = conn.execute('''
            SELECT g.*,
                   COUNT(gu.id) AS registeredCount,
                   COALESCE(SUM(gu.attending), 0) AS attendingCount
            FROM groups g
            LEFT JOIN guests gu ON gu.groupID = g.id AND gu.removedAt IS NULL
            GROUP BY g.id
            ORDER BY g.createdAt DESC, g.id DESC
        ''').fetchall()
        guestsByGroup = {}
        for person in conn.execute('SELECT * FROM guests ORDER BY isLeader DESC, id'):
            guestsByGroup.setdefault(person['groupID'], []).append(person)
        messages = conn.execute('SELECT * FROM messages ORDER BY createdAt DESC, id DESC').fetchall()
        songs = conn.execute('''
            SELECT s.*, gu.fName, gu.lName, g.familyName
            FROM songs s
            JOIN guests gu ON gu.id = s.guestID AND gu.removedAt IS NULL
            JOIN groups g ON g.id = gu.groupID
            ORDER BY s.createdAt, s.id
        ''').fetchall()
        # Extra people each group is waiting to hear back about
        openAsks = dict(conn.execute('''
            SELECT groupID, SUM(COALESCE(extraRequested, 0)) FROM notifications
            WHERE kind = 'extraPeople' AND status = 'open' GROUP BY groupID''').fetchall())
        openCount = openNotificationCount(conn)

    pendingSongs = [s for s in songs if not s['addedAt']]
    addedSongs = sorted((s for s in songs if s['addedAt']), key=lambda s: s['addedAt'], reverse=True)

    return render_template("host.html", groups=groups, guestsByGroup=guestsByGroup,
        maxGroupSize=MAX_GROUP_SIZE, messages=messages,
        pendingSongs=pendingSongs, addedSongs=addedSongs,
        openAsks=openAsks, openCount=openCount)


def openNotificationCount(conn):
    """How many guest notifications are waiting for the host. Shown on the bell."""
    return conn.execute("SELECT COUNT(*) FROM notifications WHERE status = 'open'").fetchone()[0]


def addGroup():
    """The 'Invite a group' form on the host page."""
    email = request.form.get('email', '').strip().lower()
    familyName = request.form.get('familyName', '').strip()
    groupSizeRaw = request.form.get('groupSize', '').strip()

    errors = []
    if not EMAIL_PATTERN.match(email):
        errors.append("Enter a valid email address.")
    if not familyName:
        errors.append("Enter a family or group name.")
    try:
        groupSize = int(groupSizeRaw)
        if not 1 <= groupSize <= MAX_GROUP_SIZE:
            raise ValueError
    except ValueError:
        groupSize = None
        errors.append(f"Group size must be a whole number from 1 to {MAX_GROUP_SIZE}.")

    if errors:
        for error in errors:
            flash(error, "error")
        return redirect(url_for('host', _anchor='invite'))

    # Save the group first, on its own, so it isn't lost if the email fails
    try:
        with dbConnection() as conn:
            code = generateGroupCode(conn)
            cursor = conn.execute(
                'INSERT INTO groups (familyName, groupSize, email, code) VALUES (?, ?, ?, ?)',
                (familyName, groupSize, email, code))
            group = conn.execute('SELECT * FROM groups WHERE id = ?', (cursor.lastrowid,)).fetchone()
    except (sqlite3.Error, NoFreeCodeError):
        current_app.logger.exception("Couldn't add group %r", familyName)
        flash(f"{familyName} couldn't be added just now. Nothing was saved, so please try again.", "error")
        return redirect(url_for('host', _anchor='invite'))

    if sendInvite(group):
        flash(f"{familyName} added and their invitation was emailed to {email}.", "success")
    else:
        flash(f"{familyName} was added, but the email didn't send. "
              "Check the mail settings in .env and use Resend.", "error")

    # Redirect so refreshing the page doesn't add the group twice
    return redirect(url_for('host'))


# ---------------------------------------------------------------- Host: notifications

@app.route("/host/notifications")
@admin_required
def notifications():
    """Everything groups have sent the host: requests for more people and messages."""
    with dbConnection() as conn:
        rows = conn.execute('''
            SELECT n.*, g.familyName, g.email, g.groupSize, g.registeredAt,
            (SELECT COUNT(*) FROM guests gu WHERE gu.groupID = g.id) AS guestCount,
            (SELECT COUNT(*) FROM guests gu WHERE gu.groupID = g.id
            AND gu.removedAt IS NULL) AS activeCount
            FROM notifications n
            JOIN groups g ON g.id = n.groupID
            ORDER BY n.createdAt DESC, n.id DESC''').fetchall()

    openNotes = [n for n in rows if n['status'] == 'open']
    doneNotes = sorted((n for n in rows if n['status'] != 'open'),
    key=lambda n: n['resolvedAt'] or '', reverse=True)
    return render_template("notifications.html", openNotes=openNotes, doneNotes=doneNotes,
    openCount=len(openNotes), maxGroupSize=MAX_GROUP_SIZE)


# What each button on a notification does, and which kinds it applies to
DECISIONS = {
    'approve': ('approved', 'extraPeople'),
    'decline': ('declined', 'extraPeople'),
    'resolve': ('resolved', 'contact'),
}


@app.route("/host/notifications/<int:notificationID>/decide", methods=["POST"])
@admin_required
def decideNotification(notificationID):
    """Say yes (to any number of people), no, or mark a message as done."""
    back = redirect(url_for('notifications'))
    decision = request.form.get('decision')
    reply = request.form.get('reply', '').strip()[:1000] or None

    with dbConnection() as conn:
        note = conn.execute('SELECT * FROM notifications WHERE id = ?', (notificationID,)).fetchone()
    if note is None:
        flash("That notification no longer exists.", "error")
        return back
    if decision not in DECISIONS or DECISIONS[decision][1] != note['kind']:
        flash("Choose one of the buttons on the notification.", "error")
        return back

    newStatus = DECISIONS[decision][0]
    approvedCount = None
    if decision == 'approve':
        try:
            approvedCount = int(request.form.get('count', '').strip())
            if not 1 <= approvedCount <= MAX_GROUP_SIZE:
                raise ValueError
        except ValueError:
            flash(f"Enter how many people to add, from 1 to {MAX_GROUP_SIZE}. "
                  "To turn the request down, press Decline instead.", "error")
            return back

    with dbConnection() as conn:
        # "status = 'open'" stops a double-click from adding the people twice
        changed = conn.execute('''
            UPDATE notifications SET status = ?, extraApproved = ?, hostReply = ?, resolvedAt = ?
            WHERE id = ? AND status = 'open' ''',
            (newStatus, approvedCount, reply, datetime.now().isoformat(timespec='seconds'),
             notificationID)).rowcount
        if not changed:
            flash("That's already been dealt with.", "error")
            return back

        if approvedCount:
            # A registered group fills in the new people from their page. One
            # that hasn't registered yet just gets more cards on the register form.
            conn.execute('''
                UPDATE groups
                SET groupSize = groupSize + :n,
                    extraSlots = extraSlots + CASE WHEN registeredAt IS NULL THEN 0 ELSE :n END
                WHERE id = :id''', {'n': approvedCount, 'id': note['groupID']})

        group = conn.execute('SELECT * FROM groups WHERE id = ?', (note['groupID'],)).fetchone()
        note = conn.execute('SELECT * FROM notifications WHERE id = ?', (notificationID,)).fetchone()

    if newStatus == 'resolved':
        flash("Marked as done." + (" Your reply will show on their page." if reply else ""), "success")
        return back

    emailed = sendDecision(group, note)
    if newStatus == 'approved':
        people = f"{approvedCount} more {'person' if approvedCount == 1 else 'people'}"
        flash(f"Said yes to {people} for {group['familyName']}. "
              + ("They've been emailed and can add the details from their page." if emailed else
                 "The email didn't send, but they'll see it on their page when they log in."),
              "success" if emailed else "error")
    else:
        flash(f"Declined {group['familyName']}'s request. "
              + ("They've been emailed to let them know." if emailed else
                 "The email didn't send, but they'll see it on their page when they log in."),
              "success" if emailed else "error")
    return back


@app.route("/host/notifications/<int:notificationID>/reopen", methods=["POST"])
@admin_required
def reopenNotification(notificationID):
    """Move a declined request or finished message back to 'needs a reply'.

    Approvals can't be reopened, because the group may already have added
    the extra people.
    """
    with dbConnection() as conn:
        conn.execute('''
            UPDATE notifications SET status = 'open', hostReply = NULL, resolvedAt = NULL
            WHERE id = ? AND status IN ('declined', 'resolved')''', (notificationID,))
    return redirect(url_for('notifications'))


def sendDecision(group, notification):
    """Email the group the host's answer. Returns True if it sent."""
    try:
        sendDecisionEmail(group, notification)
        return True
    except Exception:
        current_app.logger.exception("Failed to email decision to group %s", group['id'])
        return False


# ---------------------------------------------------------------- Host: guest list

@app.route("/host/guests/<int:guestID>/remove", methods=["POST"])
@admin_required
def removeGuest(guestID):
    """Take someone off the guest list. Their group sees this when they log in."""
    with dbConnection() as conn:
        person = conn.execute('SELECT * FROM guests WHERE id = ?', (guestID,)).fetchone()
        if person:
            conn.execute('UPDATE guests SET removedAt = ? WHERE id = ? AND removedAt IS NULL',
            (datetime.now().isoformat(timespec='seconds'), guestID))

    if person is None:
        flash("That guest no longer exists.", "error")
    else:
        flash(f"{person['fName']} {person['lName']} was removed from the guest list. "
            "Their group will see this when they log in.", "success")
    return redirect(url_for('host', _anchor='groups'))


@app.route("/host/guests/<int:guestID>/restore", methods=["POST"])
@admin_required
def restoreGuest(guestID):
    """Put someone back on the guest list."""
    with dbConnection() as conn:
        person = conn.execute('SELECT * FROM guests WHERE id = ?', (guestID,)).fetchone()
        if person:
            conn.execute('UPDATE guests SET removedAt = NULL WHERE id = ?', (guestID,))

    if person is None:
        flash("That guest no longer exists.", "error")
    else:
        flash(f"{person['fName']} {person['lName']} is back on the guest list.", "success")
    return redirect(url_for('host', _anchor='groups'))


@app.route("/host/messages", methods=["POST"])
@admin_required
def postMessage():
    title = request.form.get('title', '').strip()[:120]
    body = request.form.get('body', '').strip()[:2000]
    if not body:
        flash("Write a message before posting.", "error")
        return redirect(url_for('host', _anchor='updates'))

    try:
        with dbConnection() as conn:
            conn.execute('INSERT INTO messages (title, body) VALUES (?, ?)', (title or None, body))
    except sqlite3.Error:
        current_app.logger.exception("Couldn't post an update")
        flash("Your update couldn't be posted just now. Please try again.", "error")
        return redirect(url_for('host', _anchor='updates'))

    flash("Update posted. Guests will see it on their page.", "success")
    return redirect(url_for('host', _anchor='updates'))


@app.route("/host/messages/<int:messageID>/delete", methods=["POST"])
@admin_required
def deleteMessage(messageID):
    with dbConnection() as conn:
        conn.execute('DELETE FROM messages WHERE id = ?', (messageID,))
    flash("Update deleted.", "success")
    return redirect(url_for('host', _anchor='updates'))


@app.route("/host/songs/<int:songID>/added", methods=["POST"])
@admin_required
def markSongAdded(songID):
    """Hide a song from the to-do list once it's on the playlist."""
    with dbConnection() as conn:
        conn.execute('UPDATE songs SET addedAt = ? WHERE id = ? AND addedAt IS NULL',
                     (datetime.now().isoformat(timespec='seconds'), songID))
    return redirect(url_for('host', _anchor='songs'))


@app.route("/host/songs/<int:songID>/undo", methods=["POST"])
@admin_required
def unmarkSongAdded(songID):
    """Put a song back on the to-do list, in case it was hidden by mistake."""
    with dbConnection() as conn:
        conn.execute('UPDATE songs SET addedAt = NULL WHERE id = ?', (songID,))
    return redirect(url_for('host', _anchor='songs'))


@app.route("/host/resend/<int:groupID>", methods=["POST"])
@admin_required
def resendInvite(groupID):
    with dbConnection() as conn:
        group = conn.execute('SELECT * FROM groups WHERE id = ?', (groupID,)).fetchone()

    if group is None:
        flash("That group no longer exists.", "error")
    elif sendInvite(group):
        flash(f"Invitation resent to {group['email']}.", "success")
    else:
        flash("The email didn't send. Check the mail settings in .env.", "error")
    return redirect(url_for('host', _anchor='groups'))


def sendInvite(group):
    """Send the invite email and record when it went out. Returns True if it sent."""
    try:
        sendInviteEmail(group)
    except Exception:
        # Anything from a wrong password to no internet. The group is already
        # saved, so the host can fix the settings and press Resend.
        current_app.logger.exception("Failed to send invite to group %s", group['id'])
        return False

    try:
        with dbConnection() as conn:
            conn.execute('UPDATE groups SET invitedAt = ? WHERE id = ?',
            (datetime.now().isoformat(timespec='seconds'), group['id']))
    except sqlite3.Error:
        # The email still went, so report success. The host page will just say
        # "Email not sent" for this group, and Resend is harmless.
        current_app.logger.exception("Invite sent but couldn't record it for group %s", group['id'])
    return True


# ---------------------------------------------------------------- Register

def findGroupByCode(conn, code):
    code = (code or '').strip()
    if not code.isdigit():
        return None
    return conn.execute('SELECT * FROM groups WHERE code = ?', (code,)).fetchone()


def parseMember(i, isLeader, label=None):
    """Read and validate guest number i from the submitted form.

    `label` is how error messages name this person, e.g. "Extra guest 1".
    """
    form = request.form
    label = label or ("Group leader" if isLeader else f"Guest {i + 1}")
    errors = []

    member = {
        'fName': form.get(f'fName_{i}', '').strip(),
        'lName': form.get(f'lName_{i}', '').strip(),
        'attending': form.get(f'attending_{i}'),
        'ageGroup': form.get(f'ageGroup_{i}'),
        'dietaries': [d for d in form.getlist(f'dietaries_{i}') if d in DIETARY_OPTIONS],
        'dietaryNotes': form.get(f'dietaryNotes_{i}', '').strip()[:500],
        'drinks': form.get(f'drinks_{i}'),
        'accessibility': form.get(f'accessibility_{i}', '').strip()[:500],
        'interest': form.get(f'interest_{i}', '').strip()[:200],
        'isLeader': isLeader,
    }

    if not member['fName'] or not member['lName']:
        errors.append(f"{label}: enter a first and last name.")
    if member['attending'] not in ('yes', 'no'):
        errors.append(f"{label}: say whether you're attending.")
    member['attending'] = member['attending'] == 'yes'

    if member['attending']:
        if member['ageGroup'] not in AGE_GROUPS:
            errors.append(f"{label}: choose an age group.")
        if member['drinks'] not in DRINK_OPTIONS:
            member['drinks'] = None
    else:
        # None of the other questions matter for people who aren't coming
        member.update(ageGroup=None, dietaries=[], dietaryNotes='', drinks=None,
                      accessibility='', interest='')

    return member, errors


def insertGuest(conn, groupID, member):
    """Save one parsed guest to the database and return their new id."""
    return conn.execute('''
        INSERT INTO guests (groupID, fName, lName, isLeader, attending, ageGroup,
            dietaries, dietaryNotes, drinks, accessibility, interest)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (groupID, member['fName'], member['lName'], member['isLeader'],
         member['attending'], member['ageGroup'], ', '.join(member['dietaries']),
         member['dietaryNotes'], member['drinks'], member['accessibility'],
         member['interest'])).lastrowid


def parseExtraRequest(countField='extraRequested', noteField='extraRequestNote'):
    """Read an optional 'we'd like to bring more people' request from the form.

    Returns (extraCount, note, errors). Both are None when nothing was asked for.
    On the register page the fields are disabled while the panel is closed, so
    they only arrive when the group has opened it.
    """
    form = request.form
    countRaw = form.get(countField, '').strip()
    note = form.get(noteField, '').strip()[:1000] or None
    errors = []

    extraCount = None
    if countRaw:
        try:
            extraCount = int(countRaw)
            if not 1 <= extraCount <= MAX_GROUP_SIZE:
                raise ValueError
        except ValueError:
            extraCount = None
            errors.append(f"Extra people: enter a whole number from 1 to {MAX_GROUP_SIZE}.")

    if extraCount and not note:
        errors.append("Extra people: add a short message for the host, "
            "for example who they are or if they're plus ones.")

    return extraCount, note, errors


@app.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def register():
    code = request.values.get('code', '')
    if not code:
        return render_template("register.html", group=None)

    with dbConnection() as conn:
        group = findGroupByCode(conn, code)

    if group is None:
        flash("We couldn't find that code. Check your invitation email and try again.", "error")
        return render_template("register.html", group=None)

    if group['registeredAt']:
        flash("This group has already registered.", "error")
        return render_template("register.html", group=None)

    formContext = dict(group=group, dietaryGroups=DIETARY_GROUPS, ageGroups=AGE_GROUPS,
        drinkOptions=DRINK_OPTIONS, minPasswordLength=MIN_PASSWORD_LENGTH,
        maxGroupSize=MAX_GROUP_SIZE)

    if request.method == "GET":
        return render_template("register.html", **formContext)

    members, errors = [], []
    for i in range(group['groupSize']):
        member, memberErrors = parseMember(i, isLeader=(i == 0))
        members.append(member)
        errors.extend(memberErrors)

    password = request.form.get('password', '')
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"Group leader: the password must be at least {MIN_PASSWORD_LENGTH} characters.")
    elif password != request.form.get('confirmPassword', ''):
        errors.append("Group leader: the passwords don't match.")

    extraCount, extraNote, extraErrors = parseExtraRequest()
    errors.extend(extraErrors)

    if errors:
        for error in errors:
            flash(error, "error")
        # Re-show the form with everything they typed still filled in
        return render_template("register.html", **formContext), 400

    try:
        # Everything in this block is saved together or not at all
        with dbConnection() as conn:
            # Claim the group first. The "registeredAt IS NULL" check means that if
            # two people submit at the same time, only one of them succeeds.
            now = datetime.now().isoformat(timespec='seconds')
            claimed = conn.execute(
                'UPDATE groups SET password = ?, registeredAt = ? WHERE id = ? AND registeredAt IS NULL',
                (generate_password_hash(password), now, group['id'])).rowcount
            if claimed != 1:
                conn.rollback()
                flash("This group has already registered.", "error")
                return render_template("register.html", group=None)

            leaderID = None
            for member in members:
                guestID = insertGuest(conn, group['id'], member)
                if member['isLeader']:
                    leaderID = guestID

            conn.execute('UPDATE groups SET leaderID = ? WHERE id = ?', (leaderID, group['id']))

            # The request goes behind the host's bell for them to say yes or no
            if extraCount or extraNote:
                conn.execute('''INSERT INTO notifications (groupID, kind, body, extraRequested)
                VALUES (?, 'extraPeople', ?, ?)''',
                (group['id'], extraNote, extraCount))
    except sqlite3.Error:
        current_app.logger.exception("Registration failed for group %s", group['id'])
        flash("Something went wrong saving your details, so nothing was saved. "
            "Your answers are still below. Please re-enter the password and try again.", "error")
        # Show the form again with their answers, so they don't have to start over
        return render_template("register.html", **formContext), 500

    # Log the group straight in so they land on their page
    startSession('guest', group['id'])
    flash(f"Thanks {members[0]['fName']}! The {group['familyName']} group is registered. "
        f"Next time, log in with {group['email']} and the password you just created.", "success")
    if extraCount or extraNote:
        flash("Your request to bring more people has been sent to the host.", "success")
    return redirect(url_for('guest'))


if __name__ == "__main__":
    app.run(debug=True)
