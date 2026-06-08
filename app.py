import os
import json
import math
import bcrypt
import secrets
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, abort)
from authlib.integrations.flask_client import OAuth
from werkzeug.middleware.proxy_fix import ProxyFix
from database import get_db, init_db, seed_db, execute_insert, get_course_avg_rating, get_user_rating, is_enrolled

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# Google OAuth
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID'),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)

# Initialise DB on startup (works with both `python app.py` and gunicorn)
init_db()
seed_db()

AVATAR_COLORS = [
    '#7C3AED', '#DB2777', '#D97706', '#059669',
    '#DC2626', '#2563EB', '#0891B2', '#7C3AED',
]


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def current_user():
    if 'user_id' not in session:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session['user_id'],)).fetchone()
    conn.close()
    return user


# ── EMAIL ────────────────────────────────────────────────────────────────────

def send_reset_email(to_email, username, reset_url):
    gmail_user     = os.environ.get('GMAIL_USER')
    gmail_password = os.environ.get('GMAIL_APP_PASSWORD')

    if not gmail_user or not gmail_password:
        # No email credentials — print link to console for local dev
        print(f"\n[DEV] Password reset link for {username}:\n{reset_url}\n")
        return True

    msg = MIMEMultipart('alternative')
    msg['Subject'] = 'Reset your LingoLeap password'
    msg['From']    = f'LingoLeap <{gmail_user}>'
    msg['To']      = to_email

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:32px;">
      <h2 style="color:#7C3AED;margin-bottom:4px;">Reset your password</h2>
      <p style="color:#444;">Hi {username},</p>
      <p style="color:#444;">Click the button below to set a new password.
         This link expires in <strong>1 hour</strong>.</p>
      <a href="{reset_url}"
         style="display:inline-block;background:#7C3AED;color:#fff;padding:14px 28px;
                border-radius:10px;text-decoration:none;font-weight:bold;margin:16px 0;">
        Reset my password
      </a>
      <p style="color:#888;font-size:12px;">
        If you didn't request this, you can safely ignore this email.<br>
        The link will expire automatically.
      </p>
      <hr style="border:none;border-top:1px solid #eee;margin-top:24px;">
      <p style="color:#bbb;font-size:11px;">LingoLeap &mdash; Learn any language, your way.</p>
    </div>
    """
    msg.attach(MIMEText(html, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"Email send error: {e}")
        return False


# ── AUTH ─────────────────────────────────────────────────────────────────────

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if 'user_id' in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        errors = []
        if len(username) < 3:
            errors.append('Username must be at least 3 characters.')
        if len(password) < 6:
            errors.append('Password must be at least 6 characters.')
        if password != confirm:
            errors.append('Passwords do not match.')

        conn = get_db()
        if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            errors.append('Username already taken.')
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            errors.append('Email already registered.')

        if errors:
            conn.close()
            for e in errors:
                flash(e, 'error')
            return render_template('signup.html', username=username, email=email)

        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        color = AVATAR_COLORS[len(username) % len(AVATAR_COLORS)]
        conn.execute(
            "INSERT INTO users (username, email, password_hash, avatar_color) VALUES (?,?,?,?)",
            (username, email, pw_hash, color)
        )
        conn.commit()
        user_id = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()['id']
        conn.close()
        session['user_id'] = user_id
        session['username'] = username
        return redirect(url_for('onboarding'))  # → pick nickname + avatar

    return render_template('signup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        password = request.form.get('password', '')

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username=? OR email=?",
            (identifier, identifier.lower())
        ).fetchone()
        conn.close()

        if user and bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
            session['user_id'] = user['id']
            session['username'] = user['username']
            if not user['onboarded']:
                return redirect(url_for('onboarding'))
            flash(f'Welcome back, {user["username"]}!', 'success')
            return redirect(request.args.get('next') or url_for('index'))
        flash('Invalid username/email or password.', 'error')

    return render_template('login.html')


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if 'user_id' in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()

        if user:
            if user['password_hash'] == 'google_oauth':
                # Google-only account — no password to reset
                flash('This account uses Google sign-in. Please use the "Continue with Google" button to log in.', 'info')
                conn.close()
                return redirect(url_for('login'))

            # Invalidate any existing tokens for this user
            conn.execute("UPDATE password_reset_tokens SET used=1 WHERE user_id=?", (user['id'],))

            token      = secrets.token_urlsafe(32)
            expires_at = datetime.utcnow() + timedelta(hours=1)
            conn.execute(
                "INSERT INTO password_reset_tokens (user_id, token, expires_at) VALUES (?,?,?)",
                (user['id'], token, str(expires_at))
            )
            conn.commit()

            reset_url = url_for('reset_password', token=token, _external=True)
            send_reset_email(user['email'], user['username'], reset_url)

        conn.close()
        # Always show the same message so we don't reveal whether an email exists
        flash('If that email address is registered, you will receive a reset link shortly. Check your inbox (and spam folder).', 'info')
        return redirect(url_for('login'))

    return render_template('forgot_password.html', user=current_user())


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    conn = get_db()
    reset = conn.execute(
        "SELECT * FROM password_reset_tokens WHERE token=? AND used=0 AND expires_at > datetime('now')",
        (token,)
    ).fetchone()

    if not reset:
        conn.close()
        flash('This reset link is invalid or has expired. Please request a new one.', 'error')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm_password', '')

        errors = []
        if len(password) < 6:
            errors.append('Password must be at least 6 characters.')
        if password != confirm:
            errors.append('Passwords do not match.')

        if errors:
            for e in errors:
                flash(e, 'error')
            conn.close()
            return render_template('reset_password.html', token=token, user=current_user())

        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (pw_hash, reset['user_id']))
        conn.execute("UPDATE password_reset_tokens SET used=1 WHERE id=?", (reset['id'],))
        conn.commit()
        conn.close()

        flash('Password reset successfully! You can now log in with your new password.', 'success')
        return redirect(url_for('login'))

    conn.close()
    return render_template('reset_password.html', token=token, user=current_user())


@app.route('/login/google')
def google_login():
    if 'user_id' in session:
        return redirect(url_for('index'))
    redirect_uri = url_for('google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route('/auth/google/callback')
def google_callback():
    try:
        token = google.authorize_access_token()
    except Exception:
        flash('Google sign-in failed. Please try again.', 'error')
        return redirect(url_for('login'))

    user_info = token.get('userinfo')
    if not user_info:
        flash('Could not retrieve your Google account info.', 'error')
        return redirect(url_for('login'))

    google_id = user_info['sub']
    email     = user_info.get('email', '')
    name      = user_info.get('name', '')
    picture   = user_info.get('picture', '')

    conn = get_db()

    # 1. Already linked to this Google account?
    user = conn.execute("SELECT * FROM users WHERE google_id=?", (google_id,)).fetchone()

    if not user:
        # 2. Same email exists? Link the Google account to it
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user:
            conn.execute("UPDATE users SET google_id=?, avatar_url=? WHERE id=?",
                         (google_id, picture, user['id']))
            conn.commit()
            user = conn.execute("SELECT * FROM users WHERE id=?", (user['id'],)).fetchone()
        else:
            # 3. Brand new user — create account from Google profile
            base = ''.join(c for c in name.lower().replace(' ', '') if c.isalnum()) or 'user'
            username, counter = base, 1
            while conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                username = f"{base}{counter}"
                counter += 1

            color = AVATAR_COLORS[len(username) % len(AVATAR_COLORS)]
            conn.execute(
                "INSERT INTO users (username, email, password_hash, google_id, avatar_url, avatar_color) "
                "VALUES (?,?,'google_oauth',?,?,?)",
                (username, email, google_id, picture, color)
            )
            conn.commit()
            user = conn.execute("SELECT * FROM users WHERE google_id=?", (google_id,)).fetchone()
            flash(f'Welcome to LingoLeap, {username}!', 'success')

    conn.close()
    session['user_id']  = user['id']
    session['username'] = user['username']
    if not user['onboarded']:
        return redirect(url_for('onboarding'))
    flash(f'Welcome back, {user["username"]}!', 'success')
    return redirect(url_for('index'))


# ── ONBOARDING ───────────────────────────────────────────────────────────────

AVATARS = [
    {'id': 1, 'name': 'The Scholar',  'file': 'avatar1.png'},
    {'id': 2, 'name': 'The Traveller','file': 'avatar2.png'},
    {'id': 3, 'name': 'The Professor','file': 'avatar3.png'},
    {'id': 4, 'name': 'The Writer',   'file': 'avatar4.png'},
    {'id': 5, 'name': 'The Academic', 'file': 'avatar5.png'},
]

@app.route('/onboarding', methods=['GET', 'POST'])
@login_required
def onboarding():
    user = current_user()

    if request.method == 'POST':
        new_username = request.form.get('username', '').strip()
        avatar_choice = int(request.form.get('avatar_choice', 0))

        errors = []
        if len(new_username) < 3:
            errors.append('Nickname must be at least 3 characters.')
        if not new_username.replace('_','').replace('-','').isalnum():
            errors.append('Nickname can only contain letters, numbers, hyphens and underscores.')
        if avatar_choice not in [a['id'] for a in AVATARS]:
            errors.append('Please choose an avatar.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('onboarding.html', user=user, avatars=AVATARS,
                                   prefill_username=new_username)

        conn = get_db()
        # Check username not taken by someone else
        existing = conn.execute(
            "SELECT id FROM users WHERE username=? AND id != ?",
            (new_username, user['id'])
        ).fetchone()
        if existing:
            conn.close()
            flash('That nickname is already taken. Try another!', 'error')
            return render_template('onboarding.html', user=user, avatars=AVATARS,
                                   prefill_username=new_username)

        conn.execute(
            "UPDATE users SET username=?, avatar_choice=?, onboarded=1 WHERE id=?",
            (new_username, avatar_choice, user['id'])
        )
        conn.commit()
        conn.close()
        session['username'] = new_username
        flash(f'Welcome to LingoLeap, {new_username}!', 'success')
        return redirect(url_for('index'))

    return render_template('onboarding.html', user=user, avatars=AVATARS,
                           prefill_username=user['username'])


# ── MAIN PAGES ───────────────────────────────────────────────────────────────

@app.route('/')
def index():
    conn = get_db()
    languages = conn.execute("SELECT * FROM languages").fetchall()

    popular = conn.execute("""
        SELECT c.*, l.name as lang_name, l.flag_emoji,
               u.username as creator_name,
               ROUND(AVG(r.rating), 1) as avg_rating,
               COUNT(DISTINCT r.id) as rating_count
        FROM courses c
        JOIN languages l ON c.language_id = l.id
        LEFT JOIN users u ON c.creator_id = u.id
        LEFT JOIN course_ratings r ON c.id = r.course_id
        GROUP BY c.id
        ORDER BY c.enrollment_count DESC
        LIMIT 6
    """).fetchall()

    official = conn.execute("""
        SELECT c.*, l.name as lang_name, l.flag_emoji,
               ROUND(AVG(r.rating), 1) as avg_rating,
               COUNT(DISTINCT r.id) as rating_count
        FROM courses c
        JOIN languages l ON c.language_id = l.id
        LEFT JOIN course_ratings r ON c.id = r.course_id
        WHERE c.is_official = 1
        GROUP BY c.id
        ORDER BY c.enrollment_count DESC
        LIMIT 3
    """).fetchall()

    conn.close()
    return render_template('index.html',
                           languages=languages,
                           popular_courses=popular,
                           official_courses=official,
                           user=current_user())


@app.route('/courses')
def courses():
    lang_filter = request.args.get('lang', '')
    type_filter = request.args.get('type', '')  # 'official' or 'community'
    diff_filter = request.args.get('diff', '')
    search = request.args.get('q', '').strip()
    sort = request.args.get('sort', 'popular')

    conn = get_db()
    languages = conn.execute("SELECT * FROM languages").fetchall()

    where_clauses = ["1=1"]
    params = []

    if lang_filter:
        where_clauses.append("l.code = ?")
        params.append(lang_filter)
    if type_filter == 'official':
        where_clauses.append("c.is_official = 1")
    elif type_filter == 'community':
        where_clauses.append("c.is_official = 0")
    if diff_filter:
        where_clauses.append("c.difficulty = ?")
        params.append(diff_filter)
    if search:
        where_clauses.append("(c.title LIKE ? OR c.description LIKE ?)")
        params.extend([f'%{search}%', f'%{search}%'])

    order_map = {
        'popular': 'c.enrollment_count DESC',
        'newest': 'c.created_at DESC',
        'rating': 'avg_rating DESC',
        'items': 'c.item_count DESC',
    }
    order = order_map.get(sort, 'c.enrollment_count DESC')

    query = f"""
        SELECT c.*, l.name as lang_name, l.flag_emoji, l.code as lang_code,
               u.username as creator_name,
               ROUND(AVG(r.rating), 1) as avg_rating,
               COUNT(DISTINCT r.id) as rating_count
        FROM courses c
        JOIN languages l ON c.language_id = l.id
        LEFT JOIN users u ON c.creator_id = u.id
        LEFT JOIN course_ratings r ON c.id = r.course_id
        WHERE {' AND '.join(where_clauses)}
        GROUP BY c.id
        ORDER BY {order}
    """
    all_courses = conn.execute(query, params).fetchall()
    conn.close()

    return render_template('courses.html',
                           courses=all_courses,
                           languages=languages,
                           lang_filter=lang_filter,
                           type_filter=type_filter,
                           diff_filter=diff_filter,
                           search=search,
                           sort=sort,
                           user=current_user())


@app.route('/courses/<int:course_id>')
def course_detail(course_id):
    conn = get_db()
    course = conn.execute("""
        SELECT c.*, l.name as lang_name, l.flag_emoji, l.code as lang_code,
               l.color_from, l.color_to,
               u.username as creator_name, u.avatar_color as creator_color
        FROM courses c
        JOIN languages l ON c.language_id = l.id
        LEFT JOIN users u ON c.creator_id = u.id
        WHERE c.id = ?
    """, (course_id,)).fetchone()

    if not course:
        abort(404)

    vocab_preview = conn.execute(
        "SELECT * FROM vocab_items WHERE course_id=? ORDER BY position LIMIT 5",
        (course_id,)
    ).fetchall()

    reviews = conn.execute("""
        SELECT r.rating, r.created_at, u.username, u.avatar_color
        FROM course_ratings r
        JOIN users u ON r.user_id = u.id
        WHERE r.course_id=?
        ORDER BY r.created_at DESC
        LIMIT 10
    """, (course_id,)).fetchall()

    rating_info = get_course_avg_rating(conn, course_id)
    user = current_user()
    user_rating = get_user_rating(conn, course_id, user['id'] if user else None)
    enrolled = is_enrolled(conn, course_id, user['id'] if user else None)

    total_levels = max(1, math.ceil(course['item_count'] / WORDS_PER_LEVEL))
    completed_levels = set()
    level_stars = {}
    if user:
        for r in conn.execute(
            "SELECT level_number, stars FROM user_level_progress WHERE user_id=? AND course_id=? AND completed=1",
            (user['id'], course_id)
        ).fetchall():
            completed_levels.add(r['level_number'])
            level_stars[r['level_number']] = r['stars']

    # First incomplete level (= the one to play next)
    next_level = total_levels
    for lvl in range(1, total_levels + 1):
        if lvl not in completed_levels:
            next_level = lvl
            break

    conn.close()
    return render_template('course_detail.html',
                           course=course,
                           vocab_preview=vocab_preview,
                           reviews=reviews,
                           rating_info=rating_info,
                           user_rating=user_rating,
                           enrolled=enrolled,
                           user=user,
                           total_levels=total_levels,
                           completed_levels=completed_levels,
                           level_stars=level_stars,
                           next_level=next_level)


@app.route('/courses/<int:course_id>/enroll', methods=['POST'])
@login_required
def enroll(course_id):
    conn = get_db()
    user = current_user()
    if not is_enrolled(conn, course_id, user['id']):
        conn.execute(
            "INSERT OR IGNORE INTO enrollments (course_id, user_id) VALUES (?,?)",
            (course_id, user['id'])
        )
        conn.execute(
            "UPDATE courses SET enrollment_count = enrollment_count + 1 WHERE id=?",
            (course_id,)
        )
        conn.commit()
        flash('You\'re enrolled! Let\'s start learning.', 'success')
    conn.close()
    return redirect(url_for('learn', course_id=course_id))


@app.route('/courses/<int:course_id>/rate', methods=['POST'])
@login_required
def rate_course(course_id):
    rating = int(request.form.get('rating', 0))
    if rating < 1 or rating > 5:
        flash('Invalid rating.', 'error')
        return redirect(url_for('course_detail', course_id=course_id))

    user = current_user()
    conn = get_db()
    conn.execute(
        "INSERT INTO course_ratings (course_id, user_id, rating) VALUES (?,?,?) "
        "ON CONFLICT(course_id, user_id) DO UPDATE SET rating=excluded.rating",
        (course_id, user['id'], rating)
    )
    conn.commit()
    conn.close()
    flash('Rating saved!', 'success')
    return redirect(url_for('course_detail', course_id=course_id))


# ── LEARNING ─────────────────────────────────────────────────────────────────

WORDS_PER_LEVEL = 5


def _auto_enroll(conn, course_id, user_id):
    if not is_enrolled(conn, course_id, user_id):
        conn.execute("INSERT OR IGNORE INTO enrollments (course_id, user_id) VALUES (?,?)",
                     (course_id, user_id))
        conn.execute("UPDATE courses SET enrollment_count = enrollment_count + 1 WHERE id=?",
                     (course_id,))
        conn.commit()


@app.route('/learn/<int:course_id>')
@login_required
def learn(course_id):
    """Redirect to the first incomplete level (or last level if all done)."""
    user = current_user()
    conn = get_db()
    course = conn.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
    if not course:
        conn.close()
        abort(404)
    _auto_enroll(conn, course_id, user['id'])
    total_levels = max(1, math.ceil(course['item_count'] / WORDS_PER_LEVEL))
    completed = {r['level_number'] for r in conn.execute(
        "SELECT level_number FROM user_level_progress WHERE user_id=? AND course_id=? AND completed=1",
        (user['id'], course_id)
    ).fetchall()}
    conn.close()
    for lvl in range(1, total_levels + 1):
        if lvl not in completed:
            return redirect(url_for('learn_level', course_id=course_id, level_num=lvl))
    return redirect(url_for('learn_level', course_id=course_id, level_num=total_levels))


@app.route('/learn/<int:course_id>/level/<int:level_num>')
@login_required
def learn_level(course_id, level_num):
    user = current_user()
    conn = get_db()
    course = conn.execute("""
        SELECT c.*, l.name as lang_name, l.flag_emoji, l.code as lang_code
        FROM courses c JOIN languages l ON c.language_id = l.id
        WHERE c.id=?
    """, (course_id,)).fetchone()
    if not course:
        conn.close()
        abort(404)
    _auto_enroll(conn, course_id, user['id'])

    total_levels = max(1, math.ceil(course['item_count'] / WORDS_PER_LEVEL))
    level_num = max(1, min(level_num, total_levels))

    # Words for this level
    offset = (level_num - 1) * WORDS_PER_LEVEL
    level_words = conn.execute(
        "SELECT * FROM vocab_items WHERE course_id=? ORDER BY position LIMIT ? OFFSET ?",
        (course_id, WORDS_PER_LEVEL, offset)
    ).fetchall()

    # All course words (for distractors)
    all_words = conn.execute(
        "SELECT * FROM vocab_items WHERE course_id=? ORDER BY position",
        (course_id,)
    ).fetchall()

    # User progress on all words
    progress_rows = conn.execute(
        "SELECT vocab_item_id, correct_count, incorrect_count FROM user_progress WHERE user_id=?",
        (user['id'],)
    ).fetchall()
    progress = {r['vocab_item_id']: {'correct': r['correct_count'], 'incorrect': r['incorrect_count']}
                for r in progress_rows}

    # Completed levels
    completed_levels = {r['level_number'] for r in conn.execute(
        "SELECT level_number FROM user_level_progress WHERE user_id=? AND course_id=? AND completed=1",
        (user['id'], course_id)
    ).fetchall()}

    # Review words: up to 3 wrong words from completed levels
    review_words = []
    if completed_levels:
        completed_word_ids = set()
        for cl in completed_levels:
            cl_offset = (cl - 1) * WORDS_PER_LEVEL
            for w in conn.execute(
                "SELECT id FROM vocab_items WHERE course_id=? ORDER BY position LIMIT ? OFFSET ?",
                (course_id, WORDS_PER_LEVEL, cl_offset)
            ).fetchall():
                completed_word_ids.add(w['id'])
        # Sort by most incorrect
        review_candidates = sorted(
            [w for w in all_words if w['id'] in completed_word_ids],
            key=lambda w: progress.get(w['id'], {}).get('incorrect', 0),
            reverse=True
        )
        review_words = review_candidates[:3]

    conn.close()

    def word_to_dict(w):
        return {
            'id': w['id'], 'word': w['word'], 'translation': w['translation'],
            'example': w['example_sentence'] or '', 'pronunciation': w['pronunciation'] or '',
            'incorrect': progress.get(w['id'], {}).get('incorrect', 0),
            'correct':   progress.get(w['id'], {}).get('correct',   0),
        }

    return render_template('learn.html',
        course=course,
        level_num=level_num,
        total_levels=total_levels,
        is_completed=(level_num in completed_levels),
        completed_levels=list(completed_levels),
        level_words_json=json.dumps([word_to_dict(w) for w in level_words]),
        all_words_json=json.dumps([word_to_dict(w) for w in all_words]),
        review_words_json=json.dumps([word_to_dict(w) for w in review_words]),
        user=user,
    )


@app.route('/api/progress', methods=['POST'])
@login_required
def update_progress():
    data = request.get_json()
    vocab_item_id = data.get('vocab_item_id')
    correct = data.get('correct', False)
    xp_gain = int(data.get('xp_gain', 10))
    course_id = data.get('course_id')

    user = current_user()
    conn = get_db()

    if correct:
        conn.execute("""
            INSERT INTO user_progress (user_id, vocab_item_id, correct_count, last_seen)
            VALUES (?,?,1,CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, vocab_item_id)
            DO UPDATE SET correct_count = correct_count + 1, last_seen = CURRENT_TIMESTAMP
        """, (user['id'], vocab_item_id))
        conn.execute("UPDATE users SET xp = xp + ? WHERE id=?", (xp_gain, user['id']))
    else:
        conn.execute("""
            INSERT INTO user_progress (user_id, vocab_item_id, incorrect_count, last_seen)
            VALUES (?,?,1,CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, vocab_item_id)
            DO UPDATE SET incorrect_count = incorrect_count + 1, last_seen = CURRENT_TIMESTAMP
        """, (user['id'], vocab_item_id))

    conn.commit()
    new_xp = conn.execute("SELECT xp FROM users WHERE id=?", (user['id'],)).fetchone()['xp']
    conn.close()
    return jsonify({'ok': True, 'xp': new_xp})


@app.route('/api/level-complete', methods=['POST'])
@login_required
def level_complete():
    data = request.get_json()
    course_id  = data.get('course_id')
    level_num  = data.get('level_num')
    stars      = data.get('stars', 1)
    xp_bonus   = data.get('xp_bonus', 50)

    user = current_user()
    conn = get_db()
    conn.execute("""
        INSERT INTO user_level_progress (user_id, course_id, level_number, completed, stars, completed_at)
        VALUES (?,?,?,1,?,CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, course_id, level_number)
        DO UPDATE SET completed=1, stars=MAX(user_level_progress.stars, EXCLUDED.stars), completed_at=CURRENT_TIMESTAMP
    """, (user['id'], course_id, level_num, stars))
    conn.execute("UPDATE users SET xp = xp + ? WHERE id=?", (xp_bonus, user['id']))
    conn.commit()
    new_xp = conn.execute("SELECT xp FROM users WHERE id=?", (user['id'],)).fetchone()['xp']
    conn.close()
    return jsonify({'ok': True, 'xp': new_xp})


# ── CREATE COURSE ─────────────────────────────────────────────────────────────

@app.route('/create', methods=['GET', 'POST'])
@login_required
def create_course():
    conn = get_db()
    languages = conn.execute("SELECT * FROM languages").fetchall()
    conn.close()

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        language_id = request.form.get('language_id', '')
        difficulty = request.form.get('difficulty', 'beginner')
        category = request.form.get('category', 'vocabulary')

        words = request.form.getlist('word[]')
        translations = request.form.getlist('translation[]')
        examples = request.form.getlist('example[]')
        pronunciations = request.form.getlist('pronunciation[]')

        errors = []
        if len(title) < 5:
            errors.append('Course title must be at least 5 characters.')
        if len(description) < 20:
            errors.append('Please write a longer description (at least 20 characters).')
        if not language_id:
            errors.append('Please select a language.')

        valid_items = [(w.strip(), t.strip(), e.strip(), p.strip())
                       for w, t, e, p in zip(words, translations, examples, pronunciations)
                       if w.strip() and t.strip()]
        if len(valid_items) < 3:
            errors.append('Please add at least 3 vocabulary items.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('create_course.html',
                                   languages=languages,
                                   user=current_user(),
                                   form_data=request.form)

        user = current_user()
        conn = get_db()
        course_id = execute_insert(conn,
            "INSERT INTO courses (title, description, language_id, creator_id, is_official, difficulty, category, item_count) VALUES (?,?,?,?,0,?,?,?)",
            (title, description, language_id, user['id'], difficulty, category, len(valid_items))
        )

        for i, (w, t, ex, pr) in enumerate(valid_items):
            conn.execute(
                "INSERT INTO vocab_items (course_id, word, translation, example_sentence, pronunciation, position) VALUES (?,?,?,?,?,?)",
                (course_id, w, t, ex, pr, i)
            )

        conn.commit()
        conn.close()
        flash(f'Course "{title}" created successfully!', 'success')
        return redirect(url_for('course_detail', course_id=course_id))

    return render_template('create_course.html', languages=languages, user=current_user())


# ── PROFILE ───────────────────────────────────────────────────────────────────

@app.route('/profile')
@login_required
def profile():
    user = current_user()
    conn = get_db()

    enrolled_courses = conn.execute("""
        SELECT c.*, l.name as lang_name, l.flag_emoji,
               e.completed_items, e.enrolled_at
        FROM enrollments e
        JOIN courses c ON e.course_id = c.id
        JOIN languages l ON c.language_id = l.id
        WHERE e.user_id=?
        ORDER BY e.enrolled_at DESC
    """, (user['id'],)).fetchall()

    created_courses = conn.execute("""
        SELECT c.*, l.name as lang_name, l.flag_emoji,
               ROUND(AVG(r.rating), 1) as avg_rating,
               COUNT(DISTINCT r.id) as rating_count
        FROM courses c
        JOIN languages l ON c.language_id = l.id
        LEFT JOIN course_ratings r ON c.id = r.course_id
        WHERE c.creator_id=?
        GROUP BY c.id
        ORDER BY c.created_at DESC
    """, (user['id'],)).fetchall()

    total_correct = conn.execute(
        "SELECT COALESCE(SUM(correct_count), 0) as total FROM user_progress WHERE user_id=?",
        (user['id'],)
    ).fetchone()['total']

    conn.close()

    level = max(1, user['xp'] // 500 + 1)
    xp_in_level = user['xp'] % 500
    xp_to_next = 500

    return render_template('profile.html',
                           user=user,
                           enrolled_courses=enrolled_courses,
                           created_courses=created_courses,
                           total_correct=total_correct,
                           level=level,
                           xp_in_level=xp_in_level,
                           xp_to_next=xp_to_next)


# ── LEADERBOARD ───────────────────────────────────────────────────────────────

def xp_tier(xp, rank=None):
    if rank and rank <= 5:
        return 'honour'
    if xp >= 5000: return 'platinum'
    if xp >= 2000: return 'gold'
    if xp >= 500:  return 'silver'
    return 'bronze'

TIER_META = {
    'honour':   {'label': 'Roll of Honour', 'icon': '👑', 'color': 'from-yellow-400 to-amber-500',  'text': 'text-amber-700',  'bg': 'bg-amber-50',  'border': 'border-amber-300'},
    'platinum': {'label': 'Platinum',        'icon': '💎', 'color': 'from-cyan-400 to-blue-500',     'text': 'text-cyan-700',   'bg': 'bg-cyan-50',   'border': 'border-cyan-300'},
    'gold':     {'label': 'Gold',            'icon': '🥇', 'color': 'from-yellow-300 to-yellow-500', 'text': 'text-yellow-700', 'bg': 'bg-yellow-50', 'border': 'border-yellow-300'},
    'silver':   {'label': 'Silver',          'icon': '🥈', 'color': 'from-gray-300 to-gray-400',     'text': 'text-gray-600',   'bg': 'bg-gray-50',   'border': 'border-gray-300'},
    'bronze':   {'label': 'Bronze',          'icon': '🥉', 'color': 'from-orange-300 to-orange-500', 'text': 'text-orange-700', 'bg': 'bg-orange-50', 'border': 'border-orange-300'},
}

@app.route('/leaderboard')
def leaderboard():
    conn = get_db()
    all_users = conn.execute(
        "SELECT id, username, xp, avatar_color FROM users ORDER BY xp DESC"
    ).fetchall()
    conn.close()

    ranked = []
    for i, u in enumerate(all_users, 1):
        tier = xp_tier(u['xp'], rank=i)
        ranked.append({
            'rank': i, 'id': u['id'], 'username': u['username'],
            'xp': u['xp'], 'avatar_color': u['avatar_color'],
            'tier': tier, 'meta': TIER_META[tier],
        })

    honour = [u for u in ranked if u['tier'] == 'honour']
    platinum = [u for u in ranked if u['tier'] == 'platinum']
    gold     = [u for u in ranked if u['tier'] == 'gold']
    silver   = [u for u in ranked if u['tier'] == 'silver']
    bronze   = [u for u in ranked if u['tier'] == 'bronze']

    user = current_user()
    my_rank = next((u for u in ranked if user and u['id'] == user['id']), None)

    return render_template('leaderboard.html',
        honour=honour, platinum=platinum, gold=gold, silver=silver, bronze=bronze,
        tier_meta=TIER_META, user=user, my_rank=my_rank)


# ── ERROR HANDLERS ────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return render_template('404.html', user=current_user()), 404


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    debug = os.environ.get('FLASK_ENV') != 'production'
    app.run(debug=debug, host='0.0.0.0', port=port)
