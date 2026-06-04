import os
import json
import bcrypt
import secrets
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, abort)
from authlib.integrations.flask_client import OAuth
from database import get_db, init_db, seed_db, get_course_avg_rating, get_user_rating, is_enrolled

app = Flask(__name__)
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
        flash(f'Welcome to LingoLeap, {username}! 🎉', 'success')
        return redirect(url_for('index'))

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
            flash(f'Welcome back, {user["username"]}! 👋', 'success')
            return redirect(request.args.get('next') or url_for('index'))
        flash('Invalid username/email or password.', 'error')

    return render_template('login.html')


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))


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
            flash(f'Welcome to LingoLeap, {username}! 🎉', 'success')

    conn.close()
    session['user_id']  = user['id']
    session['username'] = user['username']
    flash(f'Welcome back, {user["username"]}! 👋', 'success')
    return redirect(url_for('index'))


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

    conn.close()
    return render_template('course_detail.html',
                           course=course,
                           vocab_preview=vocab_preview,
                           reviews=reviews,
                           rating_info=rating_info,
                           user_rating=user_rating,
                           enrolled=enrolled,
                           user=user)


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
        flash('You\'re enrolled! Let\'s start learning. 🚀', 'success')
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
    flash('Rating saved! ⭐', 'success')
    return redirect(url_for('course_detail', course_id=course_id))


# ── LEARNING ─────────────────────────────────────────────────────────────────

@app.route('/learn/<int:course_id>')
@login_required
def learn(course_id):
    conn = get_db()
    course = conn.execute("""
        SELECT c.*, l.name as lang_name, l.flag_emoji
        FROM courses c JOIN languages l ON c.language_id = l.id
        WHERE c.id=?
    """, (course_id,)).fetchone()

    if not course:
        abort(404)

    user = current_user()

    # Auto-enroll if not enrolled
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

    vocab = conn.execute(
        "SELECT * FROM vocab_items WHERE course_id=? ORDER BY position",
        (course_id,)
    ).fetchall()

    # Get user's prior progress
    progress = {}
    rows = conn.execute(
        "SELECT vocab_item_id, correct_count, incorrect_count FROM user_progress WHERE user_id=?",
        (user['id'],)
    ).fetchall()
    for r in rows:
        progress[r['vocab_item_id']] = {
            'correct': r['correct_count'],
            'incorrect': r['incorrect_count']
        }

    vocab_json = json.dumps([{
        'id': v['id'],
        'word': v['word'],
        'translation': v['translation'],
        'example': v['example_sentence'] or '',
        'pronunciation': v['pronunciation'] or '',
        'correct': progress.get(v['id'], {}).get('correct', 0),
        'incorrect': progress.get(v['id'], {}).get('incorrect', 0),
    } for v in vocab])

    conn.close()
    return render_template('learn.html', course=course, vocab_json=vocab_json, user=user)


@app.route('/api/progress', methods=['POST'])
@login_required
def update_progress():
    data = request.get_json()
    vocab_item_id = data.get('vocab_item_id')
    correct = data.get('correct', False)
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
        conn.execute("UPDATE users SET xp = xp + 10 WHERE id=?", (user['id'],))
    else:
        conn.execute("""
            INSERT INTO user_progress (user_id, vocab_item_id, incorrect_count, last_seen)
            VALUES (?,?,1,CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, vocab_item_id)
            DO UPDATE SET incorrect_count = incorrect_count + 1, last_seen = CURRENT_TIMESTAMP
        """, (user['id'], vocab_item_id))

    # Update enrollment completed_items
    if course_id and correct:
        conn.execute("""
            UPDATE enrollments SET completed_items = (
                SELECT COUNT(DISTINCT up.vocab_item_id)
                FROM user_progress up
                JOIN vocab_items vi ON up.vocab_item_id = vi.id
                WHERE up.user_id=? AND vi.course_id=? AND up.correct_count > 0
            )
            WHERE user_id=? AND course_id=?
        """, (user['id'], course_id, user['id'], course_id))

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
        conn.execute(
            "INSERT INTO courses (title, description, language_id, creator_id, is_official, difficulty, category, item_count) VALUES (?,?,?,?,0,?,?,?)",
            (title, description, language_id, user['id'], difficulty, category, len(valid_items))
        )
        course_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        for i, (w, t, ex, pr) in enumerate(valid_items):
            conn.execute(
                "INSERT INTO vocab_items (course_id, word, translation, example_sentence, pronunciation, position) VALUES (?,?,?,?,?,?)",
                (course_id, w, t, ex, pr, i)
            )

        conn.commit()
        conn.close()
        flash(f'Course "{title}" created successfully! 🎉', 'success')
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


# ── ERROR HANDLERS ────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return render_template('404.html', user=current_user()), 404


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    debug = os.environ.get('FLASK_ENV') != 'production'
    app.run(debug=debug, host='0.0.0.0', port=port)
