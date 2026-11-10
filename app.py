import hmac, re, sqlite3
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, session, url_for, flash, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from config import Config
from extensions import mail, initDB, getDB, generateGroupCode, sendInviteEmail

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
        ("Peanuts", False), ("Tree Nuts", False), ("All Nuts", True),
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


@app.template_filter('friendlyTime')
def friendlyTime(value):
    """'2026-11-10T21:47:50' -> '10 Nov, 9:47 PM'"""
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value
    return f"{moment.day} {moment:%b}, {moment.hour % 12 or 12}:{moment:%M %p}"


with app.app_context():
    initDB()


# ---------------------------------------------------------------- Login

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
            conn = getDB()
            groups = conn.execute(
                'SELECT id, password FROM groups WHERE email = ? AND password IS NOT NULL',
                (email,)).fetchall()
            conn.close()
            for group in groups:
                if check_password_hash(group['password'], password):
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


@app.route("/guest")
@login_required
def guest():
    conn = getDB()
    group = conn.execute('SELECT * FROM groups WHERE id = ?', (session['groupID'],)).fetchone()
    if group is None:
        conn.close()
        session.clear()
        flash("Your group could not be found. Please contact the host.", "error")
        return redirect(url_for('home'))

    guests = getGroupGuests(conn, group['id'])
    messages = conn.execute('SELECT * FROM messages ORDER BY createdAt DESC, id DESC').fetchall()
    conn.close()

    return render_template("guest.html", group=group, guests=guests, messages=messages,
                           songsPerPerson=SONGS_PER_PERSON)


@app.route("/guest/songs", methods=["POST"])
@login_required
def addSongs():
    """Song fields are named title_<guestID> / artist_<guestID>, one pair per empty slot."""
    conn = getDB()
    added = 0
    try:
        # Only loop over this group's own attending guests, so a tampered form
        # can't add songs for someone in another group.
        for guest in getGroupGuests(conn, session['groupID']):
            if not guest['attending']:
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
        conn.commit()
    finally:
        conn.close()

    if added:
        flash(f"Thanks! {added} {'song' if added == 1 else 'songs'} sent to the DJ.", "success")
    else:
        flash("Enter at least one song title.", "error")
    return redirect(url_for('guest', _anchor='songs'))


@app.route("/guest/songs/<int:songID>/delete", methods=["POST"])
@login_required
def deleteSong(songID):
    """Guests can take back a request until the host has added it to the playlist."""
    conn = getDB()
    deleted = conn.execute('''
        DELETE FROM songs
        WHERE id = ? AND addedAt IS NULL
          AND guestID IN (SELECT id FROM guests WHERE groupID = ?)''',
        (songID, session['groupID'])).rowcount
    conn.commit()
    conn.close()

    if not deleted:
        flash("That song is already on the playlist, so it can't be removed.", "error")
    return redirect(url_for('guest', _anchor='songs'))


# ---------------------------------------------------------------- Host


@app.route("/host", methods=["GET", "POST"])
@admin_required
def host():
    if request.method == "POST":
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
            return redirect(url_for('host'))

        conn = getDB()
        try:
            code = generateGroupCode(conn)
            cursor = conn.execute(
                'INSERT INTO groups (familyName, groupSize, email, code) VALUES (?, ?, ?, ?)',
                (familyName, groupSize, email, code))
            conn.commit()
            group = conn.execute('SELECT * FROM groups WHERE id = ?', (cursor.lastrowid,)).fetchone()

            if sendInvite(conn, group):
                flash(f"{familyName} added and their invitation was emailed to {email}.", "success")
            else:
                flash(f"{familyName} was added, but the email didn't send. "
                      "Check the mail settings in .env and use Resend.", "error")
        finally:
            conn.close()

        # Redirect so refreshing the page doesn't add the group twice
        return redirect(url_for('host'))

    conn = getDB()
    groups = conn.execute('''
        SELECT g.*,
               COUNT(gu.id) AS registeredCount,
               COALESCE(SUM(gu.attending), 0) AS attendingCount
        FROM groups g
        LEFT JOIN guests gu ON gu.groupID = g.id
        GROUP BY g.id
        ORDER BY g.createdAt DESC, g.id DESC
    ''').fetchall()
    messages = conn.execute('SELECT * FROM messages ORDER BY createdAt DESC, id DESC').fetchall()
    songs = conn.execute('''
        SELECT s.*, gu.fName, gu.lName, g.familyName
        FROM songs s
        JOIN guests gu ON gu.id = s.guestID
        JOIN groups g ON g.id = gu.groupID
        ORDER BY s.createdAt, s.id
    ''').fetchall()
    conn.close()

    pendingSongs = [s for s in songs if not s['addedAt']]
    addedSongs = sorted((s for s in songs if s['addedAt']), key=lambda s: s['addedAt'], reverse=True)

    return render_template("host.html", groups=groups, maxGroupSize=MAX_GROUP_SIZE,
                           messages=messages, pendingSongs=pendingSongs, addedSongs=addedSongs)


@app.route("/host/messages", methods=["POST"])
@admin_required
def postMessage():
    title = request.form.get('title', '').strip()[:120]
    body = request.form.get('body', '').strip()[:2000]
    if not body:
        flash("Write a message before posting.", "error")
        return redirect(url_for('host', _anchor='updates'))

    conn = getDB()
    conn.execute('INSERT INTO messages (title, body) VALUES (?, ?)', (title or None, body))
    conn.commit()
    conn.close()
    flash("Update posted. Guests will see it on their page.", "success")
    return redirect(url_for('host', _anchor='updates'))


@app.route("/host/messages/<int:messageID>/delete", methods=["POST"])
@admin_required
def deleteMessage(messageID):
    conn = getDB()
    conn.execute('DELETE FROM messages WHERE id = ?', (messageID,))
    conn.commit()
    conn.close()
    flash("Update deleted.", "success")
    return redirect(url_for('host', _anchor='updates'))


@app.route("/host/songs/<int:songID>/added", methods=["POST"])
@admin_required
def markSongAdded(songID):
    """Hide a song from the to-do list once it's on the playlist."""
    conn = getDB()
    conn.execute('UPDATE songs SET addedAt = ? WHERE id = ? AND addedAt IS NULL',
                 (datetime.now().isoformat(timespec='seconds'), songID))
    conn.commit()
    conn.close()
    return redirect(url_for('host', _anchor='songs'))


@app.route("/host/songs/<int:songID>/undo", methods=["POST"])
@admin_required
def unmarkSongAdded(songID):
    """Put a song back on the to-do list, in case it was hidden by mistake."""
    conn = getDB()
    conn.execute('UPDATE songs SET addedAt = NULL WHERE id = ?', (songID,))
    conn.commit()
    conn.close()
    return redirect(url_for('host', _anchor='songs'))


@app.route("/host/resend/<int:groupID>", methods=["POST"])
@admin_required
def resendInvite(groupID):
    conn = getDB()
    try:
        group = conn.execute('SELECT * FROM groups WHERE id = ?', (groupID,)).fetchone()
        if group is None:
            flash("That group no longer exists.", "error")
        elif sendInvite(conn, group):
            flash(f"Invitation resent to {group['email']}.", "success")
        else:
            flash("The email didn't send. Check the mail settings in .env.", "error")
    finally:
        conn.close()
    return redirect(url_for('host'))


def sendInvite(conn, group):
    """Send the invite email and record when it went out. Returns True on success."""
    try:
        sendInviteEmail(group)
    except Exception:
        current_app.logger.exception("Failed to send invite to group %s", group['id'])
        return False

    conn.execute('UPDATE groups SET invitedAt = ? WHERE id = ?',
                 (datetime.now().isoformat(timespec='seconds'), group['id']))
    conn.commit()
    return True


# ---------------------------------------------------------------- Register

def findGroupByCode(conn, code):
    code = (code or '').strip()
    if not code.isdigit():
        return None
    return conn.execute('SELECT * FROM groups WHERE code = ?', (code,)).fetchone()


def parseMember(i, isLeader):
    """Read and validate guest number i from the submitted form."""
    form = request.form
    label = "Group leader" if isLeader else f"Guest {i + 1}"
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
        # Catering answers don't matter for people who aren't coming
        member.update(ageGroup=None, dietaries=[], dietaryNotes='', drinks=None, accessibility='')

    return member, errors


@app.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def register():
    code = request.values.get('code', '')
    if not code:
        return render_template("register.html", group=None)

    conn = getDB()
    try:
        group = findGroupByCode(conn, code)

        if group is None:
            flash("We couldn't find that code. Check your invitation email and try again.", "error")
            return render_template("register.html", group=None)

        if group['registeredAt']:
            flash("This group has already registered.", "error")
            return render_template("register.html", group=None)

        formContext = dict(group=group, dietaryGroups=DIETARY_GROUPS, ageGroups=AGE_GROUPS,
            drinkOptions=DRINK_OPTIONS, minPasswordLength=MIN_PASSWORD_LENGTH)

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

        if errors:
            for error in errors:
                flash(error, "error")
            # Re-show the form with everything they typed still filled in
            return render_template("register.html", **formContext), 400

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
            cursor = conn.execute('''
                INSERT INTO guests (groupID, fName, lName, isLeader, attending, ageGroup,
                    dietaries, dietaryNotes, drinks, accessibility, interest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (group['id'], member['fName'], member['lName'], member['isLeader'],
                 member['attending'], member['ageGroup'], ', '.join(member['dietaries']),
                 member['dietaryNotes'], member['drinks'], member['accessibility'],
                 member['interest']))
            if member['isLeader']:
                leaderID = cursor.lastrowid

        conn.execute('UPDATE groups SET leaderID = ? WHERE id = ?', (leaderID, group['id']))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        current_app.logger.exception("Registration failed for code %s", code)
        flash("Something went wrong saving your details. Please try again.", "error")
        return render_template("register.html", group=None), 500
    finally:
        conn.close()

    # Log the group straight in so they land on their page
    startSession('guest', group['id'])
    flash(f"Thanks {members[0]['fName']}! The {group['familyName']} group is registered. "
          f"Next time, log in with {group['email']} and the password you just created.", "success")
    return redirect(url_for('guest'))


if __name__ == "__main__":
    app.run(debug=True)
