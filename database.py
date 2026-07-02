"""
database.py — works with both SQLite (local) and PostgreSQL (Railway production).
Set DATABASE_URL environment variable to use PostgreSQL; otherwise falls back to SQLite.
"""
import os
import re
import bcrypt

# ── Backend detection ─────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get('DATABASE_URL', '')
# Railway sometimes gives postgres:// — psycopg2 needs postgresql://
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

BACKEND = 'postgres' if DATABASE_URL else 'sqlite'

if BACKEND == 'postgres':
    import psycopg2
    import psycopg2.extras
else:
    import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lingoleap.db')


# ── Row wrapper ───────────────────────────────────────────────────────────────

class Row(dict):
    """Dict that also supports positional access row[0] (like sqlite3.Row)."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


# ── SQL dialect adapter ───────────────────────────────────────────────────────

def _to_pg(sql):
    """Translate SQLite-flavoured SQL to PostgreSQL."""
    sql = sql.replace('?', '%s')
    # INSERT OR IGNORE → INSERT … ON CONFLICT DO NOTHING
    if re.search(r'\bINSERT\s+OR\s+IGNORE\b', sql, re.IGNORECASE):
        sql = re.sub(r'\bINSERT\s+OR\s+IGNORE\b', 'INSERT', sql, flags=re.IGNORECASE)
        if 'ON CONFLICT' not in sql.upper():
            sql = sql.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
    sql = sql.replace("datetime('now')", 'NOW()')
    return sql


# ── Thin connection wrapper ───────────────────────────────────────────────────

class _Result:
    """Wraps a cursor so fetchone/fetchall always return Row dicts."""
    def __init__(self, cursor, backend, last_id=None):
        self._c = cursor
        self._backend = backend
        self.lastrowid = last_id

    def fetchone(self):
        row = self._c.fetchone()
        if row is None:
            return None
        return Row(row) if self._backend == 'postgres' else row

    def fetchall(self):
        rows = self._c.fetchall()
        return [Row(r) for r in rows] if self._backend == 'postgres' else rows


class DB:
    """Unified connection for SQLite and PostgreSQL."""

    def __init__(self, raw, backend):
        self._raw = raw
        self._backend = backend

    def _new_cursor(self):
        if self._backend == 'postgres':
            return self._raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        self._raw.row_factory = sqlite3.Row
        return self._raw.cursor()

    def execute(self, sql, params=()):
        c = self._new_cursor()
        adapted = _to_pg(sql) if self._backend == 'postgres' else sql
        c.execute(adapted, params)
        last_id = getattr(c, 'lastrowid', None)
        return _Result(c, self._backend, last_id)

    def executemany(self, sql, params_seq):
        c = self._new_cursor()
        adapted = _to_pg(sql) if self._backend == 'postgres' else sql
        c.executemany(adapted, params_seq)

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()


def get_db():
    if BACKEND == 'postgres':
        conn = psycopg2.connect(DATABASE_URL)
        return DB(conn, 'postgres')
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return DB(conn, 'sqlite')


def execute_insert(conn, sql, params=()):
    """Run an INSERT and return the auto-generated id (portable)."""
    if BACKEND == 'postgres':
        pg_sql = _to_pg(sql).rstrip(';') + ' RETURNING id'
        result = conn.execute(pg_sql, params)
        row = result.fetchone()
        return row['id'] if row else None
    result = conn.execute(sql, params)
    return result.lastrowid


# ── Schema ────────────────────────────────────────────────────────────────────

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL DEFAULT '',
    google_id TEXT UNIQUE,
    avatar_url TEXT,
    avatar_color TEXT NOT NULL DEFAULT '#7C3AED',
    avatar_choice INTEGER DEFAULT 0,
    onboarded INTEGER NOT NULL DEFAULT 0,
    xp INTEGER NOT NULL DEFAULT 0,
    api_token TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS languages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE,
    flag_emoji TEXT NOT NULL,
    color_from TEXT NOT NULL,
    color_to TEXT NOT NULL,
    description TEXT
);
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    language_id INTEGER NOT NULL,
    creator_id INTEGER,
    is_official INTEGER NOT NULL DEFAULT 0,
    difficulty TEXT NOT NULL DEFAULT 'beginner',
    category TEXT DEFAULT 'vocabulary',
    enrollment_count INTEGER NOT NULL DEFAULT 0,
    item_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (language_id) REFERENCES languages(id),
    FOREIGN KEY (creator_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS vocab_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    word TEXT NOT NULL,
    translation TEXT NOT NULL,
    example_sentence TEXT,
    pronunciation TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (course_id) REFERENCES courses(id)
);
CREATE TABLE IF NOT EXISTS course_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(course_id, user_id),
    FOREIGN KEY (course_id) REFERENCES courses(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    enrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_items INTEGER NOT NULL DEFAULT 0,
    UNIQUE(course_id, user_id),
    FOREIGN KEY (course_id) REFERENCES courses(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS user_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    vocab_item_id INTEGER NOT NULL,
    correct_count INTEGER NOT NULL DEFAULT 0,
    incorrect_count INTEGER NOT NULL DEFAULT 0,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, vocab_item_id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (vocab_item_id) REFERENCES vocab_items(id)
);
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMP NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS user_level_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    course_id INTEGER NOT NULL,
    level_number INTEGER NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0,
    stars INTEGER NOT NULL DEFAULT 0,
    completed_at TIMESTAMP,
    UNIQUE(user_id, course_id, level_number),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (course_id) REFERENCES courses(id)
);
"""

_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL DEFAULT '',
    google_id TEXT UNIQUE,
    avatar_url TEXT,
    avatar_color TEXT NOT NULL DEFAULT '#7C3AED',
    avatar_choice INTEGER DEFAULT 0,
    onboarded INTEGER NOT NULL DEFAULT 0,
    xp INTEGER NOT NULL DEFAULT 0,
    api_token TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS languages (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE,
    flag_emoji TEXT NOT NULL,
    color_from TEXT NOT NULL,
    color_to TEXT NOT NULL,
    description TEXT
);
CREATE TABLE IF NOT EXISTS courses (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    language_id INTEGER NOT NULL REFERENCES languages(id),
    creator_id INTEGER REFERENCES users(id),
    is_official INTEGER NOT NULL DEFAULT 0,
    difficulty TEXT NOT NULL DEFAULT 'beginner',
    category TEXT DEFAULT 'vocabulary',
    enrollment_count INTEGER NOT NULL DEFAULT 0,
    item_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS vocab_items (
    id SERIAL PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses(id),
    word TEXT NOT NULL,
    translation TEXT NOT NULL,
    example_sentence TEXT,
    pronunciation TEXT,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS course_ratings (
    id SERIAL PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(course_id, user_id)
);
CREATE TABLE IF NOT EXISTS enrollments (
    id SERIAL PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    enrolled_at TIMESTAMP DEFAULT NOW(),
    completed_items INTEGER NOT NULL DEFAULT 0,
    UNIQUE(course_id, user_id)
);
CREATE TABLE IF NOT EXISTS user_progress (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    vocab_item_id INTEGER NOT NULL REFERENCES vocab_items(id),
    correct_count INTEGER NOT NULL DEFAULT 0,
    incorrect_count INTEGER NOT NULL DEFAULT 0,
    last_seen TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, vocab_item_id)
);
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    token TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMP NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS user_level_progress (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    course_id INTEGER NOT NULL REFERENCES courses(id),
    level_number INTEGER NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0,
    stars INTEGER NOT NULL DEFAULT 0,
    completed_at TIMESTAMP,
    UNIQUE(user_id, course_id, level_number)
);
"""


def init_db():
    conn = get_db()
    schema = _PG_SCHEMA if BACKEND == 'postgres' else _SQLITE_SCHEMA
    for stmt in schema.split(';'):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()

    # Migrations — add new columns to existing databases safely
    for sql in [
        "ALTER TABLE users ADD COLUMN google_id TEXT",
        "ALTER TABLE users ADD COLUMN avatar_url TEXT",
        "ALTER TABLE users ADD COLUMN avatar_choice INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN onboarded INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN api_token TEXT",
    ]:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            pass  # Column already exists

    conn.close()


# ── Seed data ─────────────────────────────────────────────────────────────────

def seed_db():
    conn = get_db()
    if conn.execute("SELECT COUNT(*) FROM languages").fetchone()[0] > 0:
        conn.close()
        return

    # Languages
    for row in [
        ('German',     'de', '🇩🇪', '#000000', '#FFCE00',
         'German is spoken by over 100 million people across Germany, Austria, Switzerland and more.'),
        ('Lithuanian', 'lt', '🇱🇹', '#006A44', '#C1272D',
         'Lithuanian is one of the oldest living Indo-European languages, spoken by ~3 million people.'),
        ('Nepali',     'ne', '🇳🇵', '#003893', '#DC143C',
         'Nepali is spoken by over 17 million people and is the official language of Nepal.'),
    ]:
        execute_insert(conn,
            "INSERT INTO languages (name, code, flag_emoji, color_from, color_to, description) VALUES (?,?,?,?,?,?)",
            row)

    # Mock community users
    hashed = bcrypt.hashpw(b'placeholder123', bcrypt.gensalt()).decode()
    mock_users = [
        ('GermanNerd2024',  'german@example.com',  '#7C3AED'),
        ('FoodieInBerlin',  'foodie@example.com',  '#DB2777'),
        ('BalticExplorer',  'baltic@example.com',  '#D97706'),
        ('HimalayanHiker',  'hiker@example.com',   '#059669'),
        ('BerlinStreetKid', 'berlin@example.com',  '#DC2626'),
        ('CorpLinguist',    'corp@example.com',    '#2563EB'),
        ('BalticSinger',    'singer@example.com',  '#D97706'),
        ('KathmanduKid',    'ktm@example.com',     '#7C3AED'),
        ('ArtTeacher',      'art@example.com',     '#DB2777'),
        ('LanguageLover',   'lang@example.com',    '#059669'),
    ]
    for (username, email, color) in mock_users:
        execute_insert(conn,
            "INSERT INTO users (username, email, password_hash, avatar_color) VALUES (?,?,?,?)",
            (username, email, hashed, color))

    def lang_id(code):
        return conn.execute("SELECT id FROM languages WHERE code=?", (code,)).fetchone()['id']

    def user_id(username):
        return conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()['id']

    def add_course(title, desc, lang_code, official, difficulty, category, enroll_count, creator=None):
        return execute_insert(conn,
            "INSERT INTO courses (title, description, language_id, creator_id, is_official, difficulty, category, enrollment_count) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (title, desc, lang_id(lang_code), user_id(creator) if creator else None,
             1 if official else 0, difficulty, category, enroll_count))

    def add_vocab(course_id, items):
        for i, item in enumerate(items):
            execute_insert(conn,
                "INSERT INTO vocab_items (course_id, word, translation, example_sentence, pronunciation, position) VALUES (?,?,?,?,?,?)",
                (course_id, item[0], item[1], item[2] if len(item) > 2 else '', item[3] if len(item) > 3 else '', i))
        conn.execute("UPDATE courses SET item_count=? WHERE id=?", (len(items), course_id))

    # ── German Basics ──────────────────────────────────────────────────────────
    cid = add_course('German Basics: Essential Words',
        'Start your German journey with the 40 most important everyday words. Perfect for absolute beginners.',
        'de', True, 'beginner', 'vocabulary', 8420)
    add_vocab(cid, [
        ('ich','I','Ich bin müde.','ikh'),
        ('du','you (informal)','Du bist toll.','doo'),
        ('er / sie / es','he / she / it','Er ist nett.','air/zee/es'),
        ('wir','we','Wir lernen Deutsch.','veer'),
        ('ja','yes','Ja, das stimmt!','yah'),
        ('nein','no','Nein, das ist falsch.','nine'),
        ('bitte','please / you\'re welcome','Bitte schön!','bit-uh'),
        ('danke','thank you','Danke schön!','dan-kuh'),
        ('hallo','hello','Hallo! Wie geht\'s?','ha-lo'),
        ('tschüss','goodbye (informal)','Tschüss! Bis morgen!','chüss'),
        ('gut','good','Das ist gut.','goot'),
        ('schlecht','bad','Das Wetter ist schlecht.','shlesht'),
        ('groß','big','Das Haus ist groß.','groas'),
        ('klein','small','Die Katze ist klein.','kline'),
        ('der Mann','the man','Der Mann liest.','dair man'),
        ('die Frau','the woman','Die Frau lacht.','dee frow'),
        ('das Kind','the child','Das Kind spielt.','das kint'),
        ('der Hund','the dog','Der Hund bellt.','dair hoont'),
        ('die Katze','the cat','Die Katze schläft.','dee kat-zuh'),
        ('das Haus','the house','Das Haus ist groß.','das hows'),
        ('das Auto','the car','Das Auto ist neu.','das ow-to'),
        ('das Buch','the book','Das Buch ist interessant.','das bookh'),
        ('das Wasser','the water','Ich trinke Wasser.','das vas-er'),
        ('das Essen','the food','Das Essen schmeckt gut.','das es-en'),
        ('die Schule','the school','Die Schule beginnt um 8.','dee shoo-luh'),
        ('die Stadt','the city','Berlin ist eine schöne Stadt.','dee shtat'),
        ('heute','today','Heute ist Montag.','hoy-tuh'),
        ('morgen','tomorrow','Morgen gehe ich schwimmen.','mor-gen'),
        ('gestern','yesterday','Gestern war ich müde.','ges-tern'),
        ('der Tag','the day','Schönen Tag!','dair tahg'),
        ('die Nacht','the night','Gute Nacht!','dee nakht'),
        ('jetzt','now','Ich bin jetzt müde.','yetst'),
        ('hier','here','Ich bin hier.','heer'),
        ('dort','there','Das Buch liegt dort.','dort'),
        ('viel','a lot / many','Ich habe viel Arbeit.','feel'),
        ('wenig','a little / few','Ich habe wenig Zeit.','vay-nikh'),
        ('neu','new','Das Auto ist neu.','noy'),
        ('alt','old','Das Haus ist alt.','alt'),
        ('schön','beautiful','Was für ein schöner Tag!','shö n'),
        ('ja, bitte','yes please','Kaffee? Ja, bitte!','yah bit-uh'),
    ])

    # ── German Verbs ───────────────────────────────────────────────────────────
    cid = add_course('German Verbs: Top 30 Action Words',
        'Master the 30 most essential German verbs to start forming real sentences.',
        'de', True, 'beginner', 'vocabulary', 6130)
    add_vocab(cid, [
        ('sein','to be','Ich bin glücklich.','zine'),
        ('haben','to have','Ich habe ein Buch.','hah-ben'),
        ('gehen','to go','Ich gehe nach Hause.','gay-en'),
        ('kommen','to come','Kommst du auch?','kom-en'),
        ('sehen','to see','Ich sehe den Hund.','zay-en'),
        ('hören','to hear','Ich höre Musik.','hö-ren'),
        ('sprechen','to speak','Er spricht Deutsch.','shpreshen'),
        ('sagen','to say','Was sagst du?','zah-gen'),
        ('machen','to make / do','Was machst du?','makh-en'),
        ('essen','to eat','Ich esse Pizza.','es-en'),
        ('trinken','to drink','Sie trinkt Kaffee.','trin-ken'),
        ('schlafen','to sleep','Das Baby schläft.','shlah-fen'),
        ('kaufen','to buy','Ich kaufe Brot.','kow-fen'),
        ('arbeiten','to work','Er arbeitet viel.','ar-bite-en'),
        ('spielen','to play','Die Kinder spielen.','shpee-len'),
        ('lernen','to learn','Wir lernen Deutsch.','lair-nen'),
        ('lesen','to read','Sie liest gern.','lay-zen'),
        ('schreiben','to write','Ich schreibe einen Brief.','shry-ben'),
        ('fahren','to drive / travel','Wir fahren nach Berlin.','fah-ren'),
        ('laufen','to run / walk','Er läuft jeden Tag.','low-fen'),
        ('wohnen','to live / reside','Ich wohne in München.','voh-nen'),
        ('lieben','to love','Ich liebe dich.','lee-ben'),
        ('kennen','to know (someone)','Kennst du ihn?','ken-en'),
        ('wissen','to know (a fact)','Ich weiß es nicht.','vis-en'),
        ('wollen','to want','Ich will Kaffee.','vol-en'),
        ('können','to be able to','Ich kann schwimmen.','kö-nen'),
        ('müssen','to have to / must','Ich muss gehen.','müs-en'),
        ('helfen','to help','Kannst du mir helfen?','hel-fen'),
        ('fragen','to ask','Darf ich fragen?','frah-gen'),
        ('antworten','to answer','Er antwortet nicht.','ant-vor-ten'),
    ])

    # ── German Numbers ─────────────────────────────────────────────────────────
    cid = add_course('German Numbers & Counting',
        'Count from 1 to 1000 in German. Includes time expressions and useful phrases.',
        'de', True, 'beginner', 'vocabulary', 4890)
    add_vocab(cid, [
        ('null','0 (zero)','Die Temperatur ist null Grad.','nool'),
        ('eins','1 (one)','Ich habe eins.','ines'),
        ('zwei','2 (two)','Ich habe zwei Äpfel.','tsvai'),
        ('drei','3 (three)','Drei Kinder spielen.','dry'),
        ('vier','4 (four)','Es ist vier Uhr.','feer'),
        ('fünf','5 (five)','Fünf Minuten bitte.','fünf'),
        ('sechs','6 (six)','Sechs Eier, bitte.','zeks'),
        ('sieben','7 (seven)','Sieben Tage hat eine Woche.','zee-ben'),
        ('acht','8 (eight)','Ich arbeite acht Stunden.','akht'),
        ('neun','9 (nine)','Neun Monate.','noyn'),
        ('zehn','10 (ten)','Zehn Minuten zu spät.','tsayn'),
        ('zwanzig','20 (twenty)','Sie ist zwanzig Jahre alt.','tsvan-tsikh'),
        ('dreißig','30 (thirty)','Es sind dreißig Grad.','dry-sikh'),
        ('vierzig','40 (forty)','Er ist vierzig Jahre alt.','feer-tsikh'),
        ('fünfzig','50 (fifty)','Fünfzig Euro, bitte.','fünf-tsikh'),
        ('hundert','100 (one hundred)','Hundert Prozent!','hun-dert'),
        ('tausend','1000 (one thousand)','Ein tausend Euro.','tow-zend'),
        ('erste/r/s','first','Das ist mein erstes Mal.','air-stuh'),
        ('zweite/r/s','second','Das zweite Kind.','tsvai-tuh'),
        ('letzte/r/s','last','Das letzte Stück.','lets-tuh'),
    ])

    # ── Lithuanian Basics ──────────────────────────────────────────────────────
    cid = add_course('Lithuanian Basics: Essential Words',
        'Your first 35 Lithuanian words — greetings, everyday objects, and key expressions.',
        'lt', True, 'beginner', 'vocabulary', 3210)
    add_vocab(cid, [
        ('taip','yes','Taip, tai tiesa.','tayp'),
        ('ne','no','Ne, ačiū.','neh'),
        ('labas','hello / hi','Labas! Kaip sekasi?','lah-bas'),
        ('ačiū','thank you','Labai ačiū!','ah-choo'),
        ('prašau','please','Prašau, padėk man.','pra-show'),
        ('atsiprašau','sorry / excuse me','Atsiprašau, aš nesuprantu.','at-si-pra-show'),
        ('viso gero','goodbye','Viso gero! Iki!','vee-so geh-ro'),
        ('vyras','man','Tas vyras yra mokytojas.','vee-ras'),
        ('moteris','woman','Ta moteris dainuoja.','mo-teh-ris'),
        ('vaikas','child','Vaikas žaidžia parke.','vai-kas'),
        ('šuo','dog','Šuo loja.','shuo'),
        ('katė','cat','Katė miega.','ka-teh'),
        ('namas','house','Mūsų namas yra didelis.','na-mas'),
        ('automobilis','car','Automobilis yra raudonas.','au-to-mo-bee-lis'),
        ('knyga','book','Ši knyga yra įdomi.','k-nee-ga'),
        ('vanduo','water','Ar galiu gauti vandens?','van-duo'),
        ('maistas','food','Maistas yra skanus.','mais-tas'),
        ('mokykla','school','Mokykla prasideda 8 val.','mo-kik-la'),
        ('gatvė','street','Gatvė yra ilga.','gat-veh'),
        ('miestas','city','Vilnius yra gražus miestas.','mies-tas'),
        ('šalis','country','Lietuva yra graži šalis.','sha-lis'),
        ('laikas','time','Laikas bėga greitai.','lai-kas'),
        ('diena','day','Geros dienos!','dee-eh-na'),
        ('naktis','night','Labos nakties!','nak-tis'),
        ('šiandien','today','Šiandien yra pirmadienis.','shian-dien'),
        ('rytoj','tomorrow','Rytoj eisiu į parduotuvę.','ree-toy'),
        ('vakar','yesterday','Vakar aš buvau pavargęs.','va-kar'),
        ('labas rytas','good morning','Labas rytas! Kaip sekasi?','la-bas ree-tas'),
        ('labanakt','good night','Labanakt! Malonių sapnų.','la-ba-nakt'),
        ('kaip sekasi?','how are you?','Labas! Kaip sekasi?','kaip seh-ka-si'),
    ])

    # ── Lithuanian Colors & Numbers ────────────────────────────────────────────
    cid = add_course('Lithuanian: Colors & Numbers',
        'Learn all the basic colors and numbers 1-20 in Lithuanian.',
        'lt', True, 'beginner', 'vocabulary', 1870)
    add_vocab(cid, [
        ('vienas','1 (one)','Aš turiu vieną katiną.','vieh-nas'),
        ('du / dvi','2 (two)','Du vyrai.','doo/dvee'),
        ('trys','3 (three)','Trys dienos.','trees'),
        ('keturi','4 (four)','Keturi metų laikai.','keh-too-ree'),
        ('penki','5 (five)','Penki pirštai.','pen-kee'),
        ('šeši','6 (six)','Šeši mėnesiai.','sheh-shee'),
        ('septyni','7 (seven)','Savaitė turi septyni dienas.','sep-tee-nee'),
        ('aštuoni','8 (eight)','Aštuoni žmonės.','ash-two-nee'),
        ('devyni','9 (nine)','Devyni gyvūnai.','deh-vee-nee'),
        ('dešimt','10 (ten)','Dešimt minučių.','deh-shimt'),
        ('raudona','red','Raudona rožė.','rau-do-na'),
        ('žalia','green','Žalia žolė.','zha-lya'),
        ('mėlyna','blue','Mėlynas dangus.','meh-lee-na'),
        ('geltona','yellow','Geltona saulė.','gel-to-na'),
        ('balta','white','Balta sniegas.','bal-ta'),
        ('juoda','black','Juodas katinas.','juo-da'),
        ('rožinė','pink','Rožinė suknelė.','ro-zhih-neh'),
        ('oranžinė','orange','Oranžinis apelsinas.','o-ran-zhih-neh'),
        ('violetinė','purple','Violetinė gėlė.','vyo-leh-tih-neh'),
        ('pilka','grey','Pilkas oras.','pil-ka'),
    ])

    # ── Nepali Basics ──────────────────────────────────────────────────────────
    cid = add_course('Nepali Basics: Essential Words',
        'Start your Nepali adventure! Learn 35 essential words with romanization and Devanagari script.',
        'ne', True, 'beginner', 'vocabulary', 2940)
    add_vocab(cid, [
        ('ho','yes','हो, यो सहि हो।','हो'),
        ('hoina','no','होइन, मलाई चाहिँदैन।','होइन'),
        ('namaste','namaste','नमस्ते! कस्तो छ?','नमस्ते'),
        ('dhanyabad','thank you','तपाईंलाई धन्यवाद।','धन्यवाद'),
        ('kripaya','please','कृपया मलाई मद्दत गर्नुहोस्।','कृपया'),
        ('maaf garnuhos','sorry / excuse me','माफ गर्नुहोस्, म बुझिनँ।','माफ गर्नुहोस्'),
        ('alvida','goodbye','अलविदा! फेरि भेटौँला।','अलविदा'),
        ('bachcha','child','बच्चा पार्कमा खेल्छ।','बच्चा'),
        ('kukur','dog','कुकुर भुक्छ।','कुकुर'),
        ('biralo','cat','बिरालो सुत्छ।','बिरालो'),
        ('ghar','house','हाम्रो घर ठूलो छ।','घर'),
        ('gadi','car','गाडी रातो छ।','गाडी'),
        ('kitab','book','यो किताब रोचक छ।','किताब'),
        ('pani','water','मलाई पानी चाहियो।','पानी'),
        ('khana','food','खाना मिठो छ।','खाना'),
        ('vidyalay','school','विद्यालय ८ बजे सुरु हुन्छ।','विद्यालय'),
        ('sadak','road / street','सडक लामो छ।','सडक'),
        ('shahar','city','काठमाण्डौ सुन्दर शहर हो।','शहर'),
        ('desh','country','नेपाल सुन्दर देश हो।','देश'),
        ('pani','water','मलाई पानी चाहियो।','पानी'),
        ('din','day','शुभ दिन!','दिन'),
        ('raat','night','शुभ रात्री!','रात'),
        ('aaj','today','आज सोमबार हो।','आज'),
        ('bholi','tomorrow','भोलि बजार जान्छु।','भोलि'),
        ('hijo','yesterday','हिजो म थाकेको थिएँ।','हिजो'),
        ('shubha prabhaat','good morning','शुभ प्रभात! कस्तो छ?','शुभ प्रभात'),
        ('shubha raatri','good night','शुभ रात्री! राम्रो सपना।','शुभ रात्री'),
        ('kasto cha','how are you?','नमस्ते! कस्तो छ?','कस्तो छ?'),
        ('thik cha dhanyabad','fine, thank you','ठीक छ, धन्यवाद।','ठीक छ, धन्यवाद'),
        ('ramro','good / nice','यो खाना राम्रो छ।','राम्रो'),
    ])

    # ── Nepali Travel ──────────────────────────────────────────────────────────
    cid = add_course('Nepali Travel Phrases',
        'Essential phrases for travelling in Nepal — hotels, directions, shopping, and emergencies.',
        'ne', True, 'beginner', 'conversation', 2010)
    add_vocab(cid, [
        ('hotel kahaan cha','Where is the hotel?','माफ गर्नुहोस्, होटल कहाँ छ?','होटल कहाँ छ?'),
        ('kati parcha','How much does it cost?','यो कति पर्छ?','कति पर्छ?'),
        ('malai chaahiyo','I need / I want','मलाई पानी चाहियो।','मलाई चाहियो'),
        ('maddat garnuhos','Help!','मद्दत गर्नुहोस्! आपतकाल!','मद्दत गर्नुहोस्!'),
        ('aspatal','hospital','नजिकको अस्पताल कहाँ छ?','अस्पताल'),
        ('baayaa','left','बायाँ मोड्नुहोस्।','बायाँ'),
        ('daayaa','right','दायाँ मोड्नुहोस्।','दायाँ'),
        ('sojho','straight ahead','सोझो जानुहोस्।','सोझो'),
        ('bil lyaaunuhos','bring the bill','बिल ल्याउनुहोस् कृपया।','बिल ल्याउनुहोस्'),
        ('ramro','good / nice','यो खाना राम्रो छ।','राम्रो'),
        ('mahango','expensive','यो धेरै महँगो छ।','महँगो'),
        ('sasto','cheap','केही सस्तो छ?','सस्तो'),
        ('bajaar','market','बजार कहाँ छ?','बजार'),
        ('ma nepali bujhdinaa','I don\'t understand Nepali','माफ गर्नुहोस्, म नेपाली बुझ्दिनँ।','म नेपाली बुझ्दिनँ'),
        ('angreji bolnuhuncha','Do you speak English?','तपाईं अंग्रेजी बोल्नुहुन्छ?','अंग्रेजी बोल्नुहुन्छ?'),
    ])

    # ── Community courses ──────────────────────────────────────────────────────
    cid = add_course('German Food & Drinks',
        'Hungry in Germany? This vocab list covers everything you need to order food, understand menus, and talk about your favourite meals.',
        'de', False, 'beginner', 'vocabulary', 3860, creator='FoodieInBerlin')
    add_vocab(cid, [
        ('das Brot','bread','Ich esse Brot zum Frühstück.','das broht'),
        ('die Butter','butter','Butter aufs Brot.','dee boo-ter'),
        ('der Käse','cheese','Schweizer Käse ist lecker.','dair kay-zuh'),
        ('die Wurst','sausage','Bratwurst ist typisch deutsch.','dee voorst'),
        ('das Fleisch','meat','Ich esse kein Fleisch.','das flysh'),
        ('der Fisch','fish','Fisch ist gesund.','dair fish'),
        ('das Gemüse','vegetables','Ich esse viel Gemüse.','das guh-mü-zuh'),
        ('das Obst','fruit','Obst ist gesund.','das opst'),
        ('der Apfel','apple','Ein Apfel am Tag...','dair ap-fel'),
        ('die Kartoffel','potato','Kartoffeln sind typisch.','dee kar-tof-el'),
        ('die Milch','milk','Kaffee mit Milch, bitte.','dee milkh'),
        ('der Kaffee','coffee','Ich trinke morgens Kaffee.','dair kaf-ay'),
        ('das Bier','beer','Ein Bier, bitte!','das beer'),
        ('der Wein','wine','Rotwein oder Weißwein?','dair vine'),
        ('das Eis','ice cream','Ich hätte gern ein Eis.','das ice'),
        ('der Kuchen','cake','Der Kuchen ist lecker.','dair kookhen'),
        ('lecker','delicious / tasty','Das schmeckt lecker!','lek-er'),
        ('scharf','spicy / hot','Das ist zu scharf für mich.','sharp'),
        ('süß','sweet','Der Kuchen ist sehr süß.','züss'),
        ('sauer','sour','Die Zitrone ist sauer.','zow-er'),
    ])

    cid = add_course('German Business Phrases',
        'Nail your German business meetings, emails, and presentations with these essential professional phrases.',
        'de', False, 'intermediate', 'conversation', 2760, creator='CorpLinguist')
    add_vocab(cid, [
        ('die Besprechung','meeting','Die Besprechung beginnt um 9.','dee buh-shprech-ung'),
        ('der Termin','appointment','Ich habe einen Termin.','dair ter-meen'),
        ('die Präsentation','presentation','Ihre Präsentation war toll.','dee prä-zen-tat-syon'),
        ('der Bericht','report','Der Bericht ist fertig.','dair buh-rikht'),
        ('die Rechnung','invoice','Bitte senden Sie die Rechnung.','dee rekh-nung'),
        ('der Vertrag','contract','Haben Sie den Vertrag gelesen?','dair fer-trahg'),
        ('die Frist','deadline','Die Frist ist Freitag.','dee frist'),
        ('das Budget','budget','Das Budget ist zu niedrig.','das bü-djay'),
        ('die Strategie','strategy','Was ist unsere Strategie?','dee stra-teh-gee'),
        ('Mit freundlichen Grüßen','Kind regards (email closing)','Mit freundlichen Grüßen, Max.','mit froyn-li-khen'),
        ('Könnten Sie bitte...?','Could you please...?','Könnten Sie bitte anrufen?','kö-nen zee bit-uh'),
        ('die Zusammenarbeit','collaboration','Die Zusammenarbeit war großartig.','dee tsoo-za-men-ar-bite'),
    ])

    cid = add_course('1000 Most Common German Words',
        'Research shows knowing the top 1000 words covers 85% of everyday speech. This is a must-have course!',
        'de', False, 'intermediate', 'vocabulary', 7340, creator='GermanNerd2024')
    add_vocab(cid, [
        ('und','and','Du und ich.','oont'),
        ('in','in','Ich wohne in Berlin.','in'),
        ('von','of / from','Das Buch von Goethe.','fon'),
        ('mit','with','Mit dir.','mit'),
        ('auf','on / onto','Auf dem Tisch.','owf'),
        ('für','for','Das ist für dich.','für'),
        ('nicht','not','Das ist nicht wahr.','nikht'),
        ('aber','but','Schön, aber teuer.','ah-ber'),
        ('oder','or','Tee oder Kaffee?','oh-der'),
        ('wenn','if / when','Wenn es regnet...','ven'),
        ('noch','still / yet / more','Noch ein Bier?','nokh'),
        ('als','as / than / when','Größer als ich.','als'),
        ('nur','only / just','Nur ein bisschen.','noor'),
        ('auch','also / too','Ich auch!','owkh'),
        ('schon','already','Ich bin schon fertig.','shone'),
        ('so','so / such','So ein schöner Tag!','zo'),
        ('echt','really / genuinely','Das meinst du echt?','ekht'),
        ('sehr','very','Es ist sehr kalt.','zair'),
        ('vielleicht','maybe / perhaps','Vielleicht morgen.','fee-laikht'),
        ('immer','always','Er ist immer pünktlich.','im-er'),
    ])

    cid = add_course('German Slang & Street Language',
        'Sound like a local! Berlin street slang and youth language you won\'t find in textbooks.',
        'de', False, 'intermediate', 'vocabulary', 4230, creator='BerlinStreetKid')
    add_vocab(cid, [
        ('krass','crazy / intense / cool','Das ist total krass!','krass'),
        ('geil','awesome / brilliant','Das Konzert war so geil!','gile'),
        ('chillen','to chill / relax','Wir chillen heute Abend.','chil-en'),
        ('digga','dude / mate (Berlin slang)','Ey digga, was geht?','dig-a'),
        ('Alter!','Man! / Wow! (exclamation)','Alter, hast du das gesehen?!','al-ter'),
        ('Bock haben','to feel like doing something','Ich hab Bock auf Pizza.','bok hah-ben'),
        ('kein Bock','not in the mood','Ich hab heute kein Bock.','kine bok'),
        ('lässig','cool / laid-back','Der Typ ist echt lässig.','les-ikh'),
        ('voll','totally / really (intensifier)','Das ist voll gut!','fol'),
        ('auf jeden Fall','definitely / for sure','Auf jeden Fall komme ich!','owf yay-den fal'),
        ('Quatsch!','Nonsense! / Rubbish!','Das ist doch totaler Quatsch!','kvatsh'),
        ('abchecken','to check out / scope out','Lass uns den Laden abchecken.','ap-chek-en'),
    ])

    cid = add_course('Nepali for Trekkers',
        'Heading to the Himalayas? Essential Nepali vocabulary for trekking — trails, lodges, and making friends.',
        'ne', False, 'beginner', 'conversation', 3480, creator='HimalayanHiker')
    add_vocab(cid, [
        ('himal','mountain / Himalaya','हिमाल धेरै अग्लो छ।','हिमाल'),
        ('baato','path / trail','बाटो कहाँ छ?','बाटो'),
        ('laj','lodge (teahouse)','नजिकको लज कहाँ छ?','लज'),
        ('daal bhaat','lentil soup and rice','दाल भात खानु छ?','दाल भात'),
        ('thakai lagyo','I am tired','धेरै हिँडेँ, थकाइ लाग्यो।','थकाइ लाग्यो'),
        ('chiso','cold','आज धेरै चिसो छ।','चिसो'),
        ('taato','hot / warm','चिया तातो छ।','तातो'),
        ('ma biraami chu','I am sick','मलाई माफ गर्नुहोस्, म बिरामी छु।','म बिरामी छु'),
        ('daaktar','doctor','डाक्टर कहाँ छ?','डाक्टर'),
        ('ukaalo','uphill','अझै धेरै उकालो छ?','उकालो'),
        ('oraalo','downhill','ओरालो सजिलो छ।','ओरालो'),
        ('haawaa','wind','धेरै हावा छ।','हावा'),
        ('hiuu','snow','माथि हिउँ छ।','हिउँ'),
        ('mausam','weather','भोलि मौसम कस्तो हुन्छ?','मौसम'),
        ('torch','torch / flashlight','टर्च छ तपाईंसँग?','टर्च'),
    ])

    cid = add_course('Basic Nepali Greetings & Small Talk',
        'Make Nepali friends instantly! Greetings and small talk phrases to break the ice.',
        'ne', False, 'beginner', 'conversation', 2190, creator='KathmanduKid')
    add_vocab(cid, [
        ('namaste/namaskar','hello / greetings','नमस्ते! तपाईं कस्तो हुनुहुन्छ?','नमस्ते / नमस्कार'),
        ('tapaaiko naam ke ho','What is your name?','तपाईंको नाम के हो, कृपया?','तपाईंको नाम के हो?'),
        ('mero naam ... ho','My name is ...','मेरो नाम Sarah हो।','मेरो नाम ... हो'),
        ('tapaai kahaabata','Where are you from?','तपाईं कहाँबाट हुनुहुन्छ?','तपाईं कहाँबाट हुनुहुन्छ?'),
        ('ma ... bata hu','I am from ...','म बेलायत बाट हुँ।','म ... बाट हुँ'),
        ('khushi lagyo bheter','Nice to meet you','खुसी लाग्यो भेटेर!','खुसी लाग्यो भेटेर'),
        ('ma vidyarthi hu','I am a student','म विद्यार्थी हुँ।','म विद्यार्थी हुँ'),
        ('saathi','friend','तिमी मेरो राम्रो साथी हौ।','साथी'),
        ('pheri bhetaula','See you again','ठीक छ, फेरि भेटौँला!','फेरि भेटौँला'),
    ])

    cid = add_course('Lithuanian Phrases for Travelers',
        'Visiting Vilnius or the Baltic coast? Phrases to navigate Lithuania like a pro!',
        'lt', False, 'beginner', 'conversation', 1640, creator='BalticExplorer')
    add_vocab(cid, [
        ('Kur yra...?','Where is...?','Kur yra viešbutis?','koor ee-ra'),
        ('Kiek kainuoja?','How much does it cost?','Kiek kainuoja šis bilietas?','kiek kai-nuo-ya'),
        ('Man reikia...','I need...','Man reikia pagalbos.','man rei-kya'),
        ('Pagalba!','Help!','Pagalba! Greitoji!','pa-gal-ba'),
        ('viešbutis','hotel','Geras viešbutis mieste.','viesh-boo-tis'),
        ('restoranas','restaurant','Ar žinote gerą restoraną?','res-to-ra-nas'),
        ('kairė','left','Pasukite į kairę.','kai-reh'),
        ('dešinė','right','Pasukite į dešinę.','deh-shi-neh'),
        ('tiesiai','straight ahead','Eikite tiesiai.','tee-eh-siai'),
        ('Aš nesuprantu','I don\'t understand','Atsiprašau, aš nesuprantu.','ash nes-oo-pran-too'),
        ('Kalbate angliškai?','Do you speak English?','Atsiprašau, kalbate angliškai?','kal-ba-teh ang-lish-kai'),
        ('tualetas','toilet / bathroom','Kur yra tualetas?','tua-leh-tas'),
    ])

    cid = add_course('Lithuanian Folk Song Vocabulary',
        'Explore the rich tradition of Lithuanian folk songs (dainos). Poetic vocabulary from beautiful traditional songs.',
        'lt', False, 'intermediate', 'vocabulary', 520, creator='BalticSinger')
    add_vocab(cid, [
        ('daina','song / folk song','Lietuvių dainos yra gražios.','dai-na'),
        ('dainuoti','to sing','Ji moka dainuoti.','dai-nuo-ti'),
        ('upė','river','Upė teka per mišką.','oo-peh'),
        ('miškas','forest','Miškas yra gilus.','mish-kas'),
        ('saulė','sun','Saulė šviečia.','sau-leh'),
        ('mėnulis','moon','Mėnulis šviečia naktį.','meh-noo-lis'),
        ('žvaigždė','star','Žvaigždės žiba naktį.','zhvaig-zdeh'),
        ('gėlė','flower','Gėlė kvepėjo.','geh-leh'),
        ('Lietuva','Lithuania','Lietuva, tėvyne mūsų.','lee-eh-too-va'),
        ('tėvynė','homeland / fatherland','Myliu savo tėvynę.','teh-vee-neh'),
        ('laisvė','freedom','Laisvė yra brangiausia.','lais-veh'),
        ('meilė','love','Meilė stipresnė už viską.','mei-leh'),
    ])

    # ── Seed ratings ───────────────────────────────────────────────────────────
    ratings = [
        ('GermanNerd2024','German Food & Drinks',5),
        ('BerlinStreetKid','German Food & Drinks',5),
        ('CorpLinguist','German Food & Drinks',4),
        ('LanguageLover','German Food & Drinks',5),
        ('GermanNerd2024','German Business Phrases',4),
        ('FoodieInBerlin','German Business Phrases',5),
        ('FoodieInBerlin','1000 Most Common German Words',5),
        ('BerlinStreetKid','1000 Most Common German Words',5),
        ('CorpLinguist','1000 Most Common German Words',5),
        ('KathmanduKid','Nepali for Trekkers',5),
        ('LanguageLover','Nepali for Trekkers',5),
        ('HimalayanHiker','Basic Nepali Greetings & Small Talk',5),
        ('LanguageLover','Basic Nepali Greetings & Small Talk',5),
        ('BalticSinger','Lithuanian Phrases for Travelers',5),
        ('LanguageLover','Lithuanian Folk Song Vocabulary',5),
        ('FoodieInBerlin','German Slang & Street Language',5),
        ('GermanNerd2024','German Slang & Street Language',4),
        ('LanguageLover','German Slang & Street Language',5),
    ]
    for (uname, ctitle, rating) in ratings:
        uid = conn.execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone()
        crs = conn.execute("SELECT id FROM courses WHERE title=?", (ctitle,)).fetchone()
        if uid and crs:
            execute_insert(conn,
                "INSERT OR IGNORE INTO course_ratings (course_id, user_id, rating) VALUES (?,?,?)",
                (crs['id'], uid['id'], rating))

    conn.commit()
    conn.close()
    print("Database seeded successfully.")


def update_db():
    """Idempotent migration: add new languages and courses without touching existing data."""
    conn = get_db()

    def ensure_language(name, code, flag, c_from, c_to, desc):
        row = conn.execute("SELECT id FROM languages WHERE code=?", (code,)).fetchone()
        if row:
            return row['id']
        return execute_insert(conn,
            "INSERT INTO languages (name, code, flag_emoji, color_from, color_to, description) VALUES (?,?,?,?,?,?)",
            (name, code, flag, c_from, c_to, desc))

    def ensure_course(title, desc, lang_code, official, difficulty, category, enroll_count, creator_name=None):
        row = conn.execute("SELECT id FROM courses WHERE title=?", (title,)).fetchone()
        if row:
            return None  # already exists
        lang = conn.execute("SELECT id FROM languages WHERE code=?", (lang_code,)).fetchone()
        if not lang:
            return None
        creator_id = None
        if creator_name:
            u = conn.execute("SELECT id FROM users WHERE username=?", (creator_name,)).fetchone()
            creator_id = u['id'] if u else None
        return execute_insert(conn,
            "INSERT INTO courses (title, description, language_id, creator_id, is_official, difficulty, category, enrollment_count) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (title, desc, lang['id'], creator_id, 1 if official else 0, difficulty, category, enroll_count))

    def add_vocab(course_id, items):
        if course_id is None:
            return
        existing = conn.execute("SELECT COUNT(*) FROM vocab_items WHERE course_id=?", (course_id,)).fetchone()[0]
        if existing > 0:
            return
        for i, item in enumerate(items):
            execute_insert(conn,
                "INSERT INTO vocab_items (course_id, word, translation, example_sentence, pronunciation, position) VALUES (?,?,?,?,?,?)",
                (course_id, item[0], item[1], item[2] if len(item) > 2 else '', item[3] if len(item) > 3 else '', i))
        conn.execute("UPDATE courses SET item_count=? WHERE id=?", (len(items), course_id))

    # ── New languages ──────────────────────────────────────────────────────────
    ensure_language('English', 'en', '🇬🇧', '#012169', '#C8102E',
        'English is the world\'s most widely spoken language, used by over 1.5 billion people globally.')
    ensure_language('Hindi', 'hi', '🇮🇳', '#FF9933', '#138808',
        'Hindi is spoken by over 600 million people and is the official language of India.')

    # ── English: 100 Most Common Conversational Words ──────────────────────────
    cid = ensure_course('English: 100 Most Common Words',
        '100 essential everyday English words that cover the vast majority of real conversation.',
        'en', True, 'beginner', 'vocabulary', 5200)
    add_vocab(cid, [
        ('the','definite article','The cat is on the mat.',''),
        ('a / an','indefinite article','I have a dog.',''),
        ('I','first person pronoun','I am happy.',''),
        ('you','second person pronoun','Are you okay?',''),
        ('he / she / it','third person pronouns','She is my friend.',''),
        ('we','first person plural','We are going home.',''),
        ('they','third person plural','They are coming.',''),
        ('is / are / am','to be (present)','I am tired.',''),
        ('was / were','to be (past)','She was here.',''),
        ('have / has','to have','I have a question.',''),
        ('do / does','auxiliary verb','Do you speak English?',''),
        ('can','ability / possibility','Can you help me?',''),
        ('will','future tense','I will call you.',''),
        ('would','conditional','I would like coffee.',''),
        ('not','negation','I do not understand.',''),
        ('no','negative answer','No, thank you.',''),
        ('yes','affirmative answer','Yes, please.',''),
        ('what','question word','What is your name?',''),
        ('where','question word (place)','Where are you from?',''),
        ('when','question word (time)','When does it start?',''),
        ('who','question word (person)','Who is that?',''),
        ('why','question word (reason)','Why are you late?',''),
        ('how','question word (manner)','How are you?',''),
        ('this','near demonstrative','This is my bag.',''),
        ('that','far demonstrative','That is amazing.',''),
        ('here','location','I am here.',''),
        ('there','location (distant)','It is over there.',''),
        ('now','present time','I am busy now.',''),
        ('then','past / next time','I will see you then.',''),
        ('today','current day','What are you doing today?',''),
        ('tomorrow','next day','See you tomorrow.',''),
        ('yesterday','previous day','I was tired yesterday.',''),
        ('please','polite request','Please sit down.',''),
        ('thank you','gratitude','Thank you so much!',''),
        ('sorry','apology','Sorry, I am late.',''),
        ('excuse me','getting attention / apology','Excuse me, where is the station?',''),
        ('hello','greeting','Hello! How are you?',''),
        ('goodbye','farewell','Goodbye! See you soon.',''),
        ('good','positive quality','That is a good idea.',''),
        ('bad','negative quality','That is a bad sign.',''),
        ('big / large','size','That is a big building.',''),
        ('small / little','size','It is a small problem.',''),
        ('new','recently made','I have a new phone.',''),
        ('old','not new','This is an old book.',''),
        ('happy','positive emotion','I am very happy.',''),
        ('sad','negative emotion','She looks sad.',''),
        ('go','movement away','I will go now.',''),
        ('come','movement toward','Come here please.',''),
        ('see','perception / sight','Can you see that?',''),
        ('hear','perception / sound','I can hear music.',''),
        ('say','verbal expression','What did you say?',''),
        ('think','mental action','I think you are right.',''),
        ('know','knowledge','I do not know.',''),
        ('want','desire','I want some water.',''),
        ('need','necessity','I need help.',''),
        ('like','preference','I like this food.',''),
        ('love','strong affection','I love my family.',''),
        ('get','obtain / become','Can you get me some water?',''),
        ('give','transfer','Give me a moment.',''),
        ('make','create','Let us make a plan.',''),
        ('take','receive / remove','Take this with you.',''),
        ('put','place','Put it on the table.',''),
        ('look','direct eyes','Look at this!',''),
        ('work','employment / function','I work from home.',''),
        ('eat','consume food','Let us eat together.',''),
        ('drink','consume liquid','Would you like a drink?',''),
        ('sleep','rest','I need to sleep.',''),
        ('help','assist','Can you help me?',''),
        ('talk / speak','communicate','We need to talk.',''),
        ('listen','hear attentively','Please listen carefully.',''),
        ('wait','remain until','Please wait a moment.',''),
        ('try','attempt','I will try my best.',''),
        ('stop','cease','Stop right there.',''),
        ('start / begin','commence','Let us start now.',''),
        ('money','currency','How much money is it?',''),
        ('time','duration / hour','What time is it?',''),
        ('day','24-hour period','Have a nice day.',''),
        ('night','dark hours','Good night!',''),
        ('week','seven days','See you next week.',''),
        ('year','365 days','Happy New Year!',''),
        ('friend','companion','She is my best friend.',''),
        ('family','relatives','Family is everything.',''),
        ('home / house','dwelling','I am going home.',''),
        ('food','nourishment','The food is delicious.',''),
        ('water','liquid drink','Can I have some water?',''),
        ('phone','mobile device','My phone is dead.',''),
        ('place','location','This is a beautiful place.',''),
        ('way','route / method','Is this the right way?',''),
        ('thing','object / matter','What is that thing?',''),
        ('person / people','human(s)','There are many people here.',''),
        ('also / too','addition','I like it too.',''),
        ('very','intensifier','I am very tired.',''),
        ('really','emphasis','Is that really true?',''),
        ('maybe / perhaps','uncertainty','Maybe tomorrow.',''),
        ('always','every time','I always wake up early.',''),
        ('never','not ever','I never drink coffee.',''),
        ('again','one more time','Say that again please.',''),
        ('more','greater amount','Can I have more?',''),
        ('much / many','large quantity','Thank you so much.',''),
        ('little / few','small quantity','I have little time.',''),
        ('right / correct','accurate','That is right.',''),
        ('wrong','inaccurate','I think that is wrong.',''),
    ])

    # ── Hindi: 100 Most Common Conversational Words ────────────────────────────
    cid = ensure_course('Hindi: 100 Most Common Words',
        'Learn the 100 most essential Hindi words with romanization for real everyday conversation.',
        'hi', True, 'beginner', 'vocabulary', 4100)
    add_vocab(cid, [
        ('haan (हाँ)','yes','हाँ, यह सही है।','haan'),
        ('nahi (नहीं)','no','नहीं, धन्यवाद।','nah-hee'),
        ('main (मैं)','I / me','मैं ठीक हूँ।','main'),
        ('aap (आप)','you (formal)','आप कैसे हैं?','aap'),
        ('tum (तुम)','you (informal)','तुम कहाँ हो?','tum'),
        ('woh (वो)','he / she / it / that','वो मेरा दोस्त है।','woh'),
        ('hum (हम)','we','हम साथ हैं।','hum'),
        ('woh log (वो लोग)','they','वो लोग जा रहे हैं।','woh log'),
        ('hai (है)','is / am (singular)','वो ठीक है।','hai'),
        ('hain (हैं)','are (plural)','हम यहाँ हैं।','hain'),
        ('tha / thi (था/थी)','was','वो यहाँ था।','tha/thee'),
        ('karna (करना)','to do / to make','मुझे काम करना है।','kar-na'),
        ('jaana (जाना)','to go','मुझे जाना है।','jaa-na'),
        ('aana (आना)','to come','क्या तुम आ सकते हो?','aa-na'),
        ('dekhna (देखना)','to see / look','देखो यहाँ!','dekh-na'),
        ('khaana (खाना)','food / to eat','खाना बहुत अच्छा है।','khaa-na'),
        ('peena (पीना)','to drink','पानी पीना है।','pee-na'),
        ('sona (सोना)','to sleep','मुझे सोना है।','so-na'),
        ('bolna (बोलना)','to speak / say','क्या आप हिंदी बोलते हैं?','bol-na'),
        ('samajhna (समझना)','to understand','मैं समझ गया।','sa-majh-na'),
        ('chahna (चाहना)','to want','मुझे पानी चाहिए।','chah-na'),
        ('milna (मिलना)','to meet / get','आपसे मिलकर खुशी हुई।','mil-na'),
        ('lena (लेना)','to take','यह लो।','le-na'),
        ('dena (देना)','to give','मुझे दे दो।','de-na'),
        ('namaste (नमस्ते)','hello / greetings','नमस्ते! कैसे हैं आप?','na-mas-te'),
        ('alvida (अलविदा)','goodbye','अलविदा! फिर मिलेंगे।','al-vi-da'),
        ('shukriya / dhanyavaad (शुक्रिया)','thank you','बहुत शुक्रिया!','shuk-ri-ya'),
        ('maafi (माफ़ी)','sorry','माफ़ करना।','maa-fi'),
        ('kripaya (कृपया)','please','कृपया बैठिए।','kri-pa-ya'),
        ('accha (अच्छा)','good / okay / I see','अच्छा, ठीक है।','ach-cha'),
        ('bura (बुरा)','bad','यह बुरी बात है।','bu-ra'),
        ('theek hai (ठीक है)','okay / fine','ठीक है, चलो।','theek hai'),
        ('bahut (बहुत)','very / a lot','बहुत अच्छा!','ba-hut'),
        ('thoda (थोड़ा)','a little / some','थोड़ा रुको।','tho-da'),
        ('aur (और)','and / more','और क्या चाहिए?','aur'),
        ('ya (या)','or','चाय या कॉफ़ी?','ya'),
        ('lekin (लेकिन)','but','अच्छा है, लेकिन महंगा है।','le-kin'),
        ('kya (क्या)','what / question marker','क्या आप ठीक हैं?','kya'),
        ('kaun (कौन)','who','वो कौन है?','kaun'),
        ('kahan (कहाँ)','where','बाथरूम कहाँ है?','ka-haan'),
        ('kab (कब)','when','यह कब शुरू होगा?','kab'),
        ('kyon (क्यों)','why','आप क्यों रो रहे हैं?','kyon'),
        ('kaise (कैसे)','how','आप कैसे हैं?','kai-se'),
        ('kitna (कितना)','how much / many','यह कितने का है?','kit-na'),
        ('yahan (यहाँ)','here','मैं यहाँ हूँ।','ya-haan'),
        ('wahan (वहाँ)','there','वो वहाँ है।','va-haan'),
        ('ab (अब)','now','अब जाओ।','ab'),
        ('phir (फिर)','then / again','फिर मिलेंगे।','phir'),
        ('aaj (आज)','today','आज क्या है?','aaj'),
        ('kal (कल)','tomorrow / yesterday','कल मिलते हैं।','kal'),
        ('ghar (घर)','home / house','मैं घर जा रहा हूँ।','ghar'),
        ('dost / yaar (दोस्त)','friend','वो मेरा दोस्त है।','dost'),
        ('pariwar (परिवार)','family','मेरा परिवार यहाँ है।','pa-ri-war'),
        ('paani (पानी)','water','पानी चाहिए।','paa-ni'),
        ('chai (चाय)','tea','एक चाय देना।','chai'),
        ('paise (पैसे)','money','कितने पैसे लगेंगे?','pai-se'),
        ('kaam (काम)','work','मुझे काम है।','kaam'),
        ('naam (नाम)','name','आपका नाम क्या है?','naam'),
        ('waqt / samay (वक्त)','time','अभी वक्त नहीं है।','waqt'),
        ('din (दिन)','day','शुभ दिन!','din'),
        ('raat (रात)','night','शुभ रात्रि!','raat'),
        ('khush (खुश)','happy','मैं बहुत खुश हूँ।','khush'),
        ('dukhi (दुखी)','sad','वो दुखी है।','du-khi'),
        ('bada (बड़ा)','big / large','यह बड़ा है।','ba-da'),
        ('chhota (छोटा)','small / little','यह छोटा है।','chho-ta'),
        ('naya (नया)','new','नया फ़ोन लिया।','na-ya'),
        ('purana (पुराना)','old','यह पुराना है।','pu-ra-na'),
        ('accha lagta hai (अच्छा लगता है)','I like it','मुझे यह अच्छा लगता है।',''),
        ('mujhe pata nahi (मुझे पता नहीं)','I don\'t know','मुझे पता नहीं।',''),
        ('mujhe samajh nahi aaya (समझ नहीं आया)','I don\'t understand','माफ़ करना, समझ नहीं आया।',''),
        ('sunna (सुनना)','to listen / hear','ध्यान से सुनो।','sun-na'),
        ('rukna (रुकना)','to stop / wait','एक मिनट रुको।','ruk-na'),
        ('shuru karna (शुरू करना)','to start','चलो शुरू करते हैं।','shuru karna'),
        ('madad (मदद)','help','मुझे मदद चाहिए।','ma-dad'),
        ('khol (खोल)','open','दरवाज़ा खोलो।','khol'),
        ('band karo (बंद करो)','close / shut it','दरवाज़ा बंद करो।','band karo'),
        ('sahi (सही)','right / correct','यह सही है।','sa-hi'),
        ('galat (गलत)','wrong','यह गलत है।','ga-lat'),
        ('tez (तेज़)','fast / loud','ज़रा धीरे बोलो।','tez'),
        ('dhire (धीरे)','slow / softly','धीरे चलो।','dhi-re'),
        ('saath (साथ)','with / together','मेरे साथ चलो।','saath'),
        ('bina (बिना)','without','बिना पानी के नहीं।','bi-na'),
        ('upar (ऊपर)','up / above','ऊपर जाओ।','u-par'),
        ('neeche (नीचे)','down / below','नीचे आओ।','nee-che'),
        ('andar (अंदर)','inside','अंदर आओ।','an-dar'),
        ('bahar (बाहर)','outside','बाहर जाओ।','ba-har'),
        ('sab (सब)','all / everyone','सब ठीक है।','sab'),
        ('kuch (कुछ)','something / some','कुछ चाहिए?','kuch'),
        ('sirf (सिर्फ)','only / just','सिर्फ एक मिनट।','sirf'),
        ('bhi (भी)','also / too','मैं भी आऊंगा।','bhi'),
        ('nahi to (नहीं तो)','otherwise','जल्दी करो, नहीं तो देर होगी।',''),
        ('isiliye (इसीलिए)','therefore / that\'s why','इसीलिए मैं यहाँ हूँ।',''),
        ('phir bhi (फिर भी)','even so / still','फिर भी कोशिश करो।',''),
        ('zaroor (ज़रूर)','definitely / sure','मैं ज़रूर आऊंगा।','za-roor'),
        ('shayad (शायद)','maybe / perhaps','शायद कल।','sha-yad'),
        ('hamesha (हमेशा)','always','मैं हमेशा यहाँ हूँ।','ha-me-sha'),
        ('kabhi nahi (कभी नहीं)','never','मैं कभी नहीं भूलूंगा।',''),
        ('abhi (अभी)','right now','अभी आओ।','ab-hi'),
        ('jaldi (जल्दी)','quickly / hurry','जल्दी करो!','jal-di'),
        ('mushkil (मुश्किल)','difficult','यह बहुत मुश्किल है।','mush-kil'),
        ('aasaan (आसान)','easy','यह आसान है।','aa-saan'),
    ])

    # ── Hindi Script Course ────────────────────────────────────────────────────
    cid = ensure_course('Learn to Read Hindi Script (Devanagari)',
        'Learn to read and write the Devanagari alphabet used for Hindi. Master vowels, consonants and how they combine.',
        'hi', True, 'beginner', 'vocabulary', 2800)
    add_vocab(cid, [
        ('अ (a)','vowel - short "a" sound (like "u" in "sun")','अ से अनार (a = pomegranate)','a'),
        ('आ (aa)','vowel - long "aa" sound','आ से आम (aa = mango)','aa'),
        ('इ (i)','vowel - short "i" sound','इ से इमली (i = tamarind)','i'),
        ('ई (ee)','vowel - long "ee" sound','ई से ईख (ee = sugarcane)','ee'),
        ('उ (u)','vowel - short "u" sound','उ से उल्लू (u = owl)','u'),
        ('ऊ (oo)','vowel - long "oo" sound','ऊ से ऊन (oo = wool)','oo'),
        ('ए (e)','vowel - "ay" sound','ए से एड़ी (e = heel)','e'),
        ('ओ (o)','vowel - "o" sound','ओ से ओस (o = dew)','o'),
        ('क (ka)','consonant - "k" sound','क से कमल (ka = lotus)','ka'),
        ('ख (kha)','consonant - aspirated "kh"','ख से खरगोश (kha = rabbit)','kha'),
        ('ग (ga)','consonant - "g" sound','ग से गाय (ga = cow)','ga'),
        ('घ (gha)','consonant - aspirated "gh"','घ से घड़ी (gha = clock)','gha'),
        ('च (cha)','consonant - "ch" sound','च से चाय (cha = tea)','cha'),
        ('ज (ja)','consonant - "j" sound','ज से जल (ja = water)','ja'),
        ('त (ta)','consonant - soft "t" sound','त से तरबूज (ta = watermelon)','ta'),
        ('द (da)','consonant - soft "d" sound','द से दिल (da = heart)','da'),
        ('न (na)','consonant - "n" sound','न से नमक (na = salt)','na'),
        ('प (pa)','consonant - "p" sound','प से पानी (pa = water)','pa'),
        ('ब (ba)','consonant - "b" sound','ब से बकरी (ba = goat)','ba'),
        ('म (ma)','consonant - "m" sound','म से माँ (ma = mother)','ma'),
        ('र (ra)','consonant - "r" sound','र से रात (ra = night)','ra'),
        ('ल (la)','consonant - "l" sound','ल से लड़की (la = girl)','la'),
        ('व (va)','consonant - "v/w" sound','व से वन (va = forest)','va'),
        ('स (sa)','consonant - "s" sound','स से सूरज (sa = sun)','sa'),
        ('ह (ha)','consonant - "h" sound','ह से हाथ (ha = hand)','ha'),
        ('की मात्रा (aa matra) - ा','aa vowel sign added to consonant','क + ा = का (kaa)','aa matra'),
        ('ि matra (i)','short i vowel sign','क + ि = कि (ki)','i matra'),
        ('ी matra (ee)','long ee vowel sign','क + ी = की (kee)','ee matra'),
        ('ु matra (u)','u vowel sign','क + ु = कु (ku)','u matra'),
        ('halant (्)','removes inherent vowel to join consonants','क् + त = क्त (kta)','halant'),
    ])

    # ── Nepali Script Course ───────────────────────────────────────────────────
    cid = ensure_course('Learn to Read Nepali Script (Devanagari)',
        'Devanagari is used for both Nepali and Hindi. Master the alphabet step by step with Nepali examples.',
        'ne', True, 'beginner', 'vocabulary', 1900)
    add_vocab(cid, [
        ('अ (a)','vowel - short "a"','अ - अनुहार (face)','a'),
        ('आ (aa)','vowel - long "aa"','आ - आकाश (sky)','aa'),
        ('इ (i)','vowel - short "i"','इ - इनार (well)','i'),
        ('ई (ee)','vowel - long "ee"','ई - ईर्ष्या (jealousy)','ee'),
        ('उ (u)','vowel - short "u"','उ - उपहार (gift)','u'),
        ('ऊ (oo)','vowel - long "oo"','ऊ - ऊन (wool)','oo'),
        ('ए (e)','vowel - "e/ay"','ए - एक (one)','e'),
        ('ओ (o)','vowel - "o"','ओ - ओठ (lip)','o'),
        ('क (ka)','consonant - "k"','क - काम (work)','ka'),
        ('ख (kha)','consonant - aspirated kh','ख - खाना (food)','kha'),
        ('ग (ga)','consonant - "g"','ग - घर (home)','ga'),
        ('घ (gha)','consonant - aspirated gh','घ - घाँस (grass)','gha'),
        ('च (cha)','consonant - "ch"','च - चिया (tea)','cha'),
        ('ज (ja)','consonant - "j"','ज - जल (water)','ja'),
        ('त (ta)','consonant - soft "t"','त - तरकारी (vegetable)','ta'),
        ('द (da)','consonant - soft "d"','द - दिन (day)','da'),
        ('न (na)','consonant - "n"','न - नाम (name)','na'),
        ('प (pa)','consonant - "p"','प - पानी (water)','pa'),
        ('ब (ba)','consonant - "b"','ब - बाटो (path)','ba'),
        ('म (ma)','consonant - "m"','म - मान्छे (person)','ma'),
        ('र (ra)','consonant - "r"','र - रात (night)','ra'),
        ('ल (la)','consonant - "l"','ल - लामो (long)','la'),
        ('व (va)','consonant - "v"','व - वन (forest)','va'),
        ('स (sa)','consonant - "s"','स - साथी (friend)','sa'),
        ('ह (ha)','consonant - "h"','ह - हात (hand)','ha'),
        ('ञ (nya)','consonant - "ny" sound','ञ - ज्ञान (knowledge)','nya'),
        ('ट (tta)','retroflex "t" (tongue curled back)','ट - टाउको (head)','tta'),
        ('ड (dda)','retroflex "d"','ड - डाँडा (ridge/hill)','dda'),
        ('ण (nna)','retroflex "n"','ण - प्राण (life)','nna'),
        ('anuswaar (ं)','nasal sound over vowel','राम्रो becomes रामो without it','anuswaar'),
    ])

    # ── German: 100 Most Common Conversational Words ───────────────────────────
    cid = ensure_course('German: 100 Most Common Conversational Words',
        'The 100 words you will hear most in everyday German conversation — essential for real fluency.',
        'de', True, 'beginner', 'vocabulary', 6800)
    add_vocab(cid, [
        ('ja','yes','Ja, das stimmt!','yah'),
        ('nein','no','Nein, danke.','nine'),
        ('bitte','please / you\'re welcome','Bitte sehr!','bit-uh'),
        ('danke','thank you','Vielen Danke!','dan-kuh'),
        ('hallo','hello','Hallo! Wie geht\'s?','ha-lo'),
        ('tschüss','bye','Tschüss bis morgen!','chüss'),
        ('ja bitte','yes please','Kaffee? Ja bitte!','yah bit-uh'),
        ('entschuldigung','excuse me / sorry','Entschuldigung, wo ist der Bahnhof?','ent-shool-di-gung'),
        ('ich','I','Ich bin müde.','ikh'),
        ('du','you (informal)','Du bist toll.','doo'),
        ('er / sie / es','he / she / it','Er ist nett.','air/zee/es'),
        ('wir','we','Wir gehen nach Hause.','veer'),
        ('sie','they / she (formal)','Sie kommen gleich.','zee'),
        ('sein','to be','Ich bin glücklich.','zine'),
        ('haben','to have','Hast du Zeit?','hah-ben'),
        ('werden','to become / will (future)','Es wird kalt.','vair-den'),
        ('können','can / to be able','Ich kann helfen.','kö-nen'),
        ('müssen','must / to have to','Ich muss gehen.','müs-en'),
        ('wollen','to want','Was willst du?','vol-en'),
        ('sollen','should / supposed to','Du sollst das tun.','zol-en'),
        ('gehen','to go','Gehen wir!','gay-en'),
        ('kommen','to come','Kommst du auch?','kom-en'),
        ('machen','to do / make','Was machst du?','makh-en'),
        ('sagen','to say','Was sagst du?','zah-gen'),
        ('sehen','to see','Ich sehe dich.','zay-en'),
        ('wissen','to know','Ich weiß es.','vis-en'),
        ('geben','to give','Gib mir bitte...','gay-ben'),
        ('nehmen','to take','Nimm das bitte.','nay-men'),
        ('kommen','to come','Komm her!','kom-en'),
        ('gut','good','Das ist gut.','goot'),
        ('schlecht','bad','Das war schlecht.','shlesht'),
        ('groß','big','Ein großes Haus.','groas'),
        ('klein','small','Ein kleines Problem.','kline'),
        ('neu','new','Ein neues Auto.','noy'),
        ('alt','old','Ein altes Buch.','alt'),
        ('richtig','right / correct','Das ist richtig.','rikh-tikh'),
        ('falsch','wrong','Das ist falsch.','falsh'),
        ('schön','beautiful / nice','Schönen Tag!','shö-n'),
        ('schnell','fast','Fahr nicht so schnell!','shnel'),
        ('langsam','slow','Bitte langsam sprechen.','lang-zam'),
        ('was','what','Was ist das?','vas'),
        ('wer','who','Wer bist du?','vair'),
        ('wo','where','Wo bist du?','vo'),
        ('wann','when','Wann kommst du?','van'),
        ('warum','why','Warum lachst du?','var-um'),
        ('wie','how','Wie geht\'s?','vee'),
        ('wie viel','how much','Wie viel kostet das?','vee feel'),
        ('hier','here','Ich bin hier.','heer'),
        ('dort','there','Schau dort!','dort'),
        ('jetzt','now','Ich muss jetzt gehen.','yetst'),
        ('dann','then','Bis dann!','dan'),
        ('heute','today','Heute ist Montag.','hoy-tuh'),
        ('morgen','tomorrow','Bis morgen!','mor-gen'),
        ('gestern','yesterday','Gestern war schön.','ges-tern'),
        ('immer','always','Er ist immer pünktlich.','im-er'),
        ('nie','never','Das mache ich nie.','nee'),
        ('oft','often','Ich gehe oft ins Kino.','oft'),
        ('manchmal','sometimes','Manchmal bin ich müde.','manch-maal'),
        ('sehr','very','Ich bin sehr müde.','zair'),
        ('auch','also / too','Ich auch!','owkh'),
        ('noch','still / yet','Ich bin noch hier.','nokh'),
        ('schon','already','Ich bin schon fertig.','shone'),
        ('nur','only','Nur ein bisschen.','noor'),
        ('aber','but','Schön, aber teuer.','ah-ber'),
        ('oder','or','Tee oder Kaffee?','oh-der'),
        ('und','and','Du und ich.','oont'),
        ('weil','because','Ich bin müde, weil ich arbeite.','vile'),
        ('wenn','if / when','Wenn es regnet...','ven'),
        ('dass','that (conjunction)','Ich denke, dass es gut ist.','das'),
        ('der / die / das','the (m/f/n)','Das Haus ist groß.','dair/dee/das'),
        ('ein / eine','a / an','Ein Buch. Eine Katze.','ine/ine-uh'),
        ('mein / meine','my','Das ist mein Buch.','mine/mine-uh'),
        ('dein / deine','your','Wo ist deine Tasche?','dine/dine-uh'),
        ('kein / keine','no / not a','Ich habe keine Zeit.','kine/kine-uh'),
        ('mit','with','Komm mit mir!','mit'),
        ('ohne','without','Ohne dich bin ich nichts.','oh-nuh'),
        ('für','for','Das ist für dich.','für'),
        ('von','from / of','Von wo kommst du?','fon'),
        ('zu','to / too','Ich gehe zu Hause.','tsoo'),
        ('auf','on / up','Auf dem Tisch.','owf'),
        ('in','in','Ich bin in Berlin.','in'),
        ('an','at / on','Am Montag.','an'),
        ('um','around / at (time)','Um 8 Uhr.','um'),
        ('nach','after / to (city)','Nach dem Essen.','nakh'),
        ('vor','before / in front of','Vor dem Haus.','for'),
        ('Zeit','time','Hast du Zeit?','tsait'),
        ('Tag','day','Guten Tag!','tahg'),
        ('Nacht','night','Gute Nacht!','nakht'),
        ('Woche','week','Nächste Woche.','vo-khuh'),
        ('Jahr','year','Frohes neues Jahr!','yaar'),
        ('Haus','house','Ich bin zu Hause.','hows'),
        ('Geld','money','Ich habe kein Geld.','gelt'),
        ('Arbeit','work','Ich muss zur Arbeit.','ar-bite'),
        ('Freund / Freundin','friend (m/f)','Er ist mein Freund.','froynd'),
        ('Familie','family','Meine Familie ist groß.','fa-mee-lee-uh'),
        ('Essen','food / meal','Das Essen ist gut.','es-en'),
        ('Wasser','water','Ich trinke Wasser.','vas-er'),
        ('Entschuldigung','excuse me / sorry','Entschuldigung!','ent-shool-di-gung'),
        ('Hilfe!','Help!','Hilfe! Ich brauche Hilfe!','hil-fuh'),
        ('nicht verstehen','to not understand','Ich verstehe das nicht.','nikht fer-shtay-en'),
        ('wiederholen','to repeat','Können Sie das wiederholen?','vee-der-ho-len'),
    ])

    # ── Lithuanian: 100 Most Common Conversational Words ──────────────────────
    cid = ensure_course('Lithuanian: 100 Most Common Conversational Words',
        'The 100 words that make up the backbone of everyday Lithuanian conversation.',
        'lt', True, 'beginner', 'vocabulary', 2900)
    add_vocab(cid, [
        ('taip','yes','Taip, aš sutinku.','tayp'),
        ('ne','no','Ne, ačiū.','neh'),
        ('prašau','please','Prašau, padėk man.','pra-show'),
        ('ačiū','thank you','Labai ačiū!','ah-choo'),
        ('labas','hello','Labas! Kaip sekasi?','lah-bas'),
        ('viso gero','goodbye','Viso gero! Iki!','vee-so geh-ro'),
        ('atsiprašau','sorry / excuse me','Atsiprašau, aš nesuprantu.','at-si-pra-show'),
        ('labas rytas','good morning','Labas rytas!','lah-bas ree-tas'),
        ('labas vakaras','good evening','Labas vakaras!','lah-bas va-ka-ras'),
        ('labanakt','good night','Labanakt!','la-ba-nakt'),
        ('aš','I','Aš esu studentas.','ash'),
        ('tu','you (informal)','Tu esi geras.','too'),
        ('jis / ji','he / she','Jis yra mokytojas.','yis/yi'),
        ('mes','we','Mes einame.','mes'),
        ('jie / jos','they (m/f)','Jie ateina.','yie/yos'),
        ('būti','to be','Aš esu laimingas.','boo-ti'),
        ('turėti','to have','Aš turiu knygą.','too-reh-ti'),
        ('eiti','to go','Einame!','ei-ti'),
        ('ateiti','to come','Jis ateina.','a-tei-ti'),
        ('daryti','to do / make','Ką tu darai?','da-ree-ti'),
        ('sakyti','to say','Ką sakai?','sa-kee-ti'),
        ('matyti','to see','Aš matau tave.','ma-tee-ti'),
        ('žinoti','to know','Aš nežinau.','zhi-no-ti'),
        ('norėti','to want','Ko tu nori?','no-reh-ti'),
        ('valgyti','to eat','Einame valgyti!','val-gee-ti'),
        ('gerti','to drink','Ar nori gerti?','ger-ti'),
        ('miegoti','to sleep','Reikia miegoti.','mie-go-ti'),
        ('kalbėti','to speak / talk','Ar kalbi lietuviškai?','kal-beh-ti'),
        ('suprasti','to understand','Aš nesuprantu.','soo-pras-ti'),
        ('padėti','to help','Gali padėti?','pa-deh-ti'),
        ('geras','good','Tai gera idėja.','geh-ras'),
        ('blogas','bad','Tai blogai.','blo-gas'),
        ('didelis','big','Didelis namas.','di-deh-lis'),
        ('mažas','small','Mažas vaikas.','ma-zhas'),
        ('naujas','new','Naujas automobilis.','nau-yas'),
        ('senas','old','Senas namas.','seh-nas'),
        ('greitas','fast','Greitas automobilis.','grei-tas'),
        ('lėtas','slow','Lėtas eismas.','leh-tas'),
        ('teisingas','right / correct','Tai teisinga.','tei-sing-as'),
        ('neteisingas','wrong','Tai neteisinga.','ne-tei-sing-as'),
        ('gražus','beautiful','Gražus miestas.','gra-zhoos'),
        ('kas','what / who','Kas tai yra?','kas'),
        ('kur','where','Kur tu esi?','koor'),
        ('kada','when','Kada ateisi?','ka-da'),
        ('kodėl','why','Kodėl verksi?','ko-dehl'),
        ('kaip','how','Kaip sekasi?','kaip'),
        ('kiek','how much / many','Kiek tai kainuoja?','kiek'),
        ('čia','here','Aš esu čia.','chia'),
        ('ten','there','Žiūrėk ten!','ten'),
        ('dabar','now','Aš dabar einu.','da-bar'),
        ('tada','then / at that time','Iki tada!','ta-da'),
        ('šiandien','today','Šiandien yra pirmadienis.','shian-dien'),
        ('rytoj','tomorrow','Iki rytoj!','ree-toy'),
        ('vakar','yesterday','Vakar buvo šilta.','va-kar'),
        ('visada','always','Jis visada vėluoja.','vi-sa-da'),
        ('niekada','never','Aš niekada nerūkau.','nie-ka-da'),
        ('dažnai','often','Aš dažnai vaikštau.','dazh-nai'),
        ('kartais','sometimes','Kartais einu į kiną.','kar-tais'),
        ('labai','very','Labai ačiū!','la-bai'),
        ('taip pat','also / too','Aš taip pat.','tayp pat'),
        ('dar','still / yet','Aš dar čia.','dar'),
        ('jau','already','Aš jau baigiau.','yau'),
        ('tik','only','Tik vienas.','tik'),
        ('bet','but','Geras, bet brangus.','bet'),
        ('arba','or','Arbata arba kava?','ar-ba'),
        ('ir','and','Tu ir aš.','ir'),
        ('nes','because','Einu, nes reikia.','nes'),
        ('jei','if','Jei ateis...','yei'),
        ('kad','that (conjunction)','Manau, kad tai tiesa.','kad'),
        ('su','with','Ateik su manimi.','soo'),
        ('be','without','Be tavęs graudu.','beh'),
        ('už','for / behind','Ačiū už viską.','oozh'),
        ('iš','from / out of','Iš kur tu?','ish'),
        ('į','to / into','Einame į namus.','ing'),
        ('ant','on','Ant stalo.','ant'),
        ('po','under / after','Po stalu.','po'),
        ('laikas','time','Kiek dabar laikas?','lai-kas'),
        ('diena','day','Geros dienos!','dee-eh-na'),
        ('naktis','night','Labos nakties!','nak-tis'),
        ('savaitė','week','Kitą savaitę.','sa-vai-teh'),
        ('metai','year','Laimingų Naujų Metų!','meh-tai'),
        ('namas','house','Aš einu namo.','na-mas'),
        ('pinigai','money','Neturiu pinigų.','pi-ni-gai'),
        ('darbas','work','Turiu daug darbo.','dar-bas'),
        ('draugas / draugė','friend (m/f)','Jis mano draugas.','drau-gas'),
        ('šeima','family','Mano šeima didelė.','shei-ma'),
        ('maistas','food','Maistas skanus.','mais-tas'),
        ('vanduo','water','Norėčiau vandens.','van-duo'),
        ('pagalba','help','Man reikia pagalbos.','pa-gal-ba'),
        ('nesuprantu','I don\'t understand','Atsiprašau, nesuprantu.','ne-soo-pran-too'),
        ('pakartokite','please repeat','Prašau pakartokite.','pa-kar-to-ki-te'),
        ('angliškai','in English','Kalbate angliškai?','ang-lish-kai'),
        ('kaina','price','Kokia kaina?','kai-na'),
        ('labai gerai','very good / great','Labai gerai!','la-bai geh-rai'),
        ('iki','until / bye (casual)','Iki ryto!','i-ki'),
        ('Lietuva','Lithuania','Lietuva yra graži.','lie-too-va'),
    ])

    # ── Nepali: 100 Most Common Conversational Words ───────────────────────────
    cid = ensure_course('Nepali: 100 Most Common Conversational Words',
        'The 100 words that form the core of everyday Nepali conversation, with Devanagari script.',
        'ne', True, 'beginner', 'vocabulary', 2600)
    add_vocab(cid, [
        ('ho (हो)','yes / is','हो, यो सहि हो।','हो'),
        ('hoina (होइन)','no / is not','होइन, मलाई चाहिँदैन।','होइन'),
        ('kripaya (कृपया)','please','कृपया मद्दत गर्नुहोस्।','कृपया'),
        ('dhanyabad (धन्यवाद)','thank you','धेरै धन्यवाद!','धन्यवाद'),
        ('namaste (नमस्ते)','hello','नमस्ते! कस्तो छ?','नमस्ते'),
        ('alvida (अलविदा)','goodbye','अलविदा! फेरि भेटौँला।','अलविदा'),
        ('maaf garnuhos (माफ गर्नुहोस्)','sorry / excuse me','माफ गर्नुहोस्।','माफ गर्नुहोस्'),
        ('subha prabhat (शुभ प्रभात)','good morning','शुभ प्रभात!','शुभ प्रभात'),
        ('subha ratri (शुभ रात्री)','good night','शुभ रात्री!','शुभ रात्री'),
        ('ma (म)','I / me','म ठीक छु।','म'),
        ('tapai (तपाईं)','you (formal)','तपाईं कस्तो हुनुहुन्छ?','तपाईं'),
        ('timi (तिमी)','you (informal)','तिमी कहाँ छौ?','तिमी'),
        ('u (ऊ)','he / she','ऊ मेरो साथी हो।','ऊ'),
        ('hami (हामी)','we','हामी सँगै छौँ।','हामी'),
        ('uniharu (उनीहरू)','they','उनीहरू जाँदैछन्।','उनीहरू'),
        ('chha (छ)','is / are (present)','खाना राम्रो छ।','छ'),
        ('thiyo (थियो)','was / were (past)','ऊ यहाँ थियो।','थियो'),
        ('garnu (गर्नु)','to do / make','मलाई काम गर्नु छ।','गर्नु'),
        ('jaanu (जानु)','to go','म जान्छु।','जानु'),
        ('aaunu (आउनु)','to come','तपाईं आउनुहोस्।','आउनु'),
        ('herne (हेर्ने)','to see / look','यता हेर्नुहोस्।','हेर्ने'),
        ('khanu (खानु)','to eat','खाना खानु छ?','खानु'),
        ('pinu (पिउनु)','to drink','पानी पिउनुहोस्।','पिउनु'),
        ('sutnu (सुत्नु)','to sleep','मलाई सुत्नु छ।','सुत्नु'),
        ('bolnu (बोल्नु)','to speak','नेपाली बोल्नुहुन्छ?','बोल्नु'),
        ('bujhnu (बुझ्नु)','to understand','म बुझ्दिनँ।','बुझ्नु'),
        ('chahinu (चाहिनु)','to need / want','मलाई पानी चाहियो।','चाहिनु'),
        ('bhetnु (भेट्नु)','to meet','तपाईंलाई भेटेर खुशी लाग्यो।','भेट्नु'),
        ('linu (लिनु)','to take','यो लिनुहोस्।','लिनु'),
        ('dinu (दिनु)','to give','मलाई दिनुहोस्।','दिनु'),
        ('ramro (राम्रो)','good / nice / beautiful','यो खाना राम्रो छ।','राम्रो'),
        ('naramro (नराम्रो)','bad / not good','यो नराम्रो छ।','नराम्रो'),
        ('thulo (ठूलो)','big / large','ठूलो घर छ।','ठूलो'),
        ('sano (सानो)','small / little','सानो समस्या।','सानो'),
        ('naya (नयाँ)','new','नयाँ फोन किनेँ।','नयाँ'),
        ('purano (पुरानो)','old','पुरानो किताब।','पुरानो'),
        ('thik (ठीक)','okay / fine / correct','ठीक छ।','ठीक'),
        ('galat (गलत)','wrong','यो गलत छ।','गलत'),
        ('dherai (धेरै)','very / a lot / many','धेरै धन्यवाद।','धेरै'),
        ('ali ali (अलि अलि)','a little','अलि अलि नेपाली बोल्छु।','अलि अलि'),
        ('ke (के)','what','यो के हो?','के'),
        ('ko (को)','who','ऊ को हो?','को'),
        ('kahan (कहाँ)','where','बाथरूम कहाँ छ?','कहाँ'),
        ('kahile (कहिले)','when','यो कहिले सुरु हुन्छ?','कहिले'),
        ('kina (किन)','why','तिमी किन रोइरहेका छौ?','किन'),
        ('kasari (कसरी)','how','यो कसरी गर्ने?','कसरी'),
        ('kati (कति)','how much / many','यो कति पर्छ?','कति'),
        ('yahan (यहाँ)','here','म यहाँ छु।','यहाँ'),
        ('tyahan (त्यहाँ)','there','त्यहाँ हेर्नुहोस्।','त्यहाँ'),
        ('ahile (अहिले)','now','अहिले जानुहोस्।','अहिले'),
        ('pachhi (पछि)','later / after','पछि भेटौँला।','पछि'),
        ('aaja (आज)','today','आज सोमबार हो।','आज'),
        ('bholi (भोलि)','tomorrow','भोलि भेटौँला।','भोलि'),
        ('hijo (हिजो)','yesterday','हिजो म थाकेको थिएँ।','हिजो'),
        ('sadhai (सधैँ)','always','म सधैँ यहाँ छु।','सधैँ'),
        ('kahilye pani (कहिल्यै पनि)','never','म त्यहाँ कहिल्यै गइनँ।','कहिल्यै पनि'),
        ('prayah (प्रायः)','often','म प्रायः यहाँ आउँछु।','प्रायः'),
        ('kabhi kabhi (कहिलेकाहीँ)','sometimes','कहिलेकाहीँ बिर्सन्छु।','कहिलेकाहीँ'),
        ('ra (र)','and','तिमी र म।','र'),
        ('ya (वा)','or','चिया वा कफी?','वा'),
        ('tara (तर)','but','राम्रो छ, तर महँगो।','तर'),
        ('kinabhane (किनभने)','because','म थाकेँ, किनभने धेरै हिँडेँ।','किनभने'),
        ('yadi (यदि)','if','यदि मौसम राम्रो भए...','यदि'),
        ('sanga (सँग)','with','मसँग आउनुहोस्।','सँग'),
        ('bina (बिना)','without','पानी बिना सम्भव छैन।','बिना'),
        ('lai (लाई)','to / for (dative marker)','मलाई पानी चाहियो।','लाई'),
        ('bata (बाट)','from','म काठमाडौँबाट हुँ।','बाट'),
        ('ma (मा)','in / at / on','घरमा छु।','मा'),
        ('ko (को)','of / possessive marker','नेपालको राजधानी।','को'),
        ('samay (समय)','time','अहिले कति समय भयो?','समय'),
        ('din (दिन)','day','शुभ दिन!','दिन'),
        ('raat (रात)','night','शुभ रात्री।','रात'),
        ('haptaa (हप्ता)','week','अर्को हप्ता भेटौँला।','हप्ता'),
        ('barsha (बर्ष)','year','नयाँ बर्षको शुभकामना!','बर्ष'),
        ('ghar (घर)','home / house','म घर जान्छु।','घर'),
        ('paisa (पैसा)','money','कति पैसा लाग्छ?','पैसा'),
        ('kaam (काम)','work','मलाई काम छ।','काम'),
        ('saathi (साथी)','friend','ऊ मेरो साथी हो।','साथी'),
        ('pariwar (परिवार)','family','मेरो परिवार सानो छ।','परिवार'),
        ('khaana (खाना)','food','खाना खानुहोस्।','खाना'),
        ('paani (पानी)','water','पानी चाहियो।','पानी'),
        ('maddat (मद्दत)','help','मलाई मद्दत चाहियो।','मद्दत'),
        ('bujhina (बुझिनँ)','I don\'t understand','माफ गर्नुहोस्, म बुझिनँ।','बुझिनँ'),
        ('pheri bhannus (फेरि भन्नुस्)','please say again','फेरि भन्नुस् कृपया।','फेरि भन्नुस्'),
        ('angrejima (अंग्रेजीमा)','in English','अंग्रेजीमा भन्नुस्।','अंग्रेजीमा'),
        ('mool (मूल्य)','price','मूल्य कति हो?','मूल्य'),
        ('aafno (आफ्नो)','own / self\'s','यो मेरो आफ्नो घर हो।','आफ्नो'),
        ('sabai (सबै)','all / everyone','सबै ठीक छ।','सबै'),
        ('kehi (केही)','something / some','केही चाहिन्छ?','केही'),
        ('matra (मात्र)','only / just','एक मिनेट मात्र।','मात्र'),
        ('pani (पनि)','also / too','म पनि जान्छु।','पनि'),
        ('thaha chha (थाहा छ)','I know','मलाई थाहा छ।','थाहा छ'),
        ('thaha chhaina (थाहा छैन)','I don\'t know','मलाई थाहा छैन।','थाहा छैन'),
        ('pakkaa (पक्का)','sure / definitely','पक्का आउँछु।','पक्का'),
        ('sायद (सायद)','maybe','सायद भोलि।','सायद'),
        ('khushi (खुशी)','happy','म धेरै खुशी छु।','खुशी'),
        ('dukhi (दुखी)','sad','ऊ दुखी देखिन्छ।','दुखी'),
        ('Nepal (नेपाल)','Nepal','नेपाल सुन्दर देश हो।','नेपाल'),
    ])

    # ── New languages: Spanish, Russian, Ukrainian, Polish ────────────────────
    ensure_language('Spanish', 'es', '🇪🇸', '#AA151B', '#F1BF00',
        'Spanish is spoken by over 500 million people worldwide, making it the world\'s second most spoken native language.')
    ensure_language('Russian', 'ru', '🇷🇺', '#003399', '#CC0000',
        'Russian is spoken by over 150 million people and is the most widely spoken Slavic language in the world.')
    ensure_language('Ukrainian', 'uk', '🇺🇦', '#005BBB', '#FFD500',
        'Ukrainian is spoken by over 40 million people and is the official language of Ukraine.')
    ensure_language('Polish', 'pl', '🇵🇱', '#DC143C', '#FFFFFF',
        'Polish is spoken by over 45 million people and is the most widely spoken West Slavic language.')

    # ── Spanish: Basics ───────────────────────────────────────────────────────
    cid = ensure_course('Spanish Basics: Essential Words',
        'Start your Spanish journey with 40 essential everyday words. Perfect for absolute beginners.',
        'es', True, 'beginner', 'vocabulary', 7200)
    add_vocab(cid, [
        ('sí','yes','Sí, claro.','see'),
        ('no','no','No, gracias.','no'),
        ('por favor','please','Por favor, ayúdame.','por fa-vor'),
        ('gracias','thank you','¡Muchas gracias!','gra-syas'),
        ('hola','hello','¡Hola! ¿Cómo estás?','o-la'),
        ('adiós','goodbye','¡Adiós! Hasta luego.','a-dyos'),
        ('buenos días','good morning','¡Buenos días! ¿Cómo estás?','bwe-nos dee-as'),
        ('buenas noches','good night','¡Buenas noches! Hasta mañana.','bwe-nas no-ches'),
        ('perdón / disculpe','excuse me / sorry','Perdón, no entiendo.','per-don'),
        ('de nada','you\'re welcome','¡De nada! Con gusto.','de na-da'),
        ('yo','I','Yo soy estudiante.','yo'),
        ('tú','you (informal)','¿Tú hablas español?','too'),
        ('él / ella','he / she','Él es mi amigo.','el/e-ya'),
        ('nosotros','we','Nosotros somos amigos.','no-so-tros'),
        ('ellos / ellas','they','Ellos hablan español.','e-yos'),
        ('ser / estar','to be','Soy de España.','ser/es-tar'),
        ('tener','to have','Tengo un libro.','te-ner'),
        ('ir','to go','¿A dónde vas?','eer'),
        ('venir','to come','¿Puedes venir?','ve-neer'),
        ('hacer','to do / make','¿Qué haces?','a-ser'),
        ('querer','to want / love','Quiero café.','ke-rer'),
        ('poder','can / to be able','¿Puedes ayudarme?','po-der'),
        ('hablar','to speak','Hablo español.','a-blar'),
        ('comer','to eat','Vamos a comer.','ko-mer'),
        ('beber','to drink','¿Quieres beber algo?','be-ber'),
        ('dormir','to sleep','Necesito dormir.','dor-meer'),
        ('bueno','good','¡Qué bueno!','bwe-no'),
        ('malo','bad','Eso es malo.','ma-lo'),
        ('grande','big','Una ciudad grande.','gran-de'),
        ('pequeño','small','Un problema pequeño.','pe-ke-nyo'),
        ('nuevo','new','Un coche nuevo.','nwe-vo'),
        ('viejo','old','Una casa vieja.','vye-ho'),
        ('agua','water','Quiero agua, por favor.','a-gwa'),
        ('comida','food','La comida está rica.','ko-mee-da'),
        ('casa','house / home','Voy a casa.','ka-sa'),
        ('dinero','money','No tengo dinero.','dee-ne-ro'),
        ('amigo / amiga','friend (m/f)','Es mi mejor amigo.','a-mee-go'),
        ('familia','family','Mi familia es grande.','fa-mee-lya'),
        ('hoy','today','¿Qué haces hoy?','oy'),
        ('mañana','tomorrow','Hasta mañana.','ma-nya-na'),
    ])

    # ── Spanish: 100 Most Common Conversational Words ─────────────────────────
    cid = ensure_course('Spanish: 100 Most Common Conversational Words',
        'The 100 words that form the backbone of everyday Spanish conversation.',
        'es', True, 'beginner', 'vocabulary', 6100)
    add_vocab(cid, [
        ('sí','yes','Sí, tienes razón.','see'),
        ('no','no','No, gracias.','no'),
        ('por favor','please','Por favor, habla más despacio.','por fa-vor'),
        ('gracias','thank you','¡Muchas gracias!','gra-syas'),
        ('de nada','you\'re welcome','¡De nada!','de na-da'),
        ('hola','hello','¡Hola! ¿Qué tal?','o-la'),
        ('adiós / hasta luego','goodbye / see you','¡Hasta luego!','a-dyos'),
        ('perdón / lo siento','sorry','Lo siento mucho.','lo syen-to'),
        ('disculpe','excuse me','Disculpe, ¿dónde está el baño?','dis-kul-pe'),
        ('yo','I','Yo soy de México.','yo'),
        ('tú / usted','you (inf/formal)','¿Usted habla inglés?','too/us-ted'),
        ('él / ella','he / she','Ella es muy simpática.','el/e-ya'),
        ('nosotros','we','Nosotros vamos al cine.','no-so-tros'),
        ('ellos','they','Ellos llegaron tarde.','e-yos'),
        ('ser','to be (permanent)','Soy español.','ser'),
        ('estar','to be (temporary)','Estoy bien.','es-tar'),
        ('tener','to have','¿Tienes tiempo?','te-ner'),
        ('haber','to have (auxiliary)','He comido.','a-ber'),
        ('hacer','to do / make','¿Qué haces?','a-ser'),
        ('poder','can / to be able','¿Puedes ayudarme?','po-der'),
        ('querer','to want','Quiero agua.','ke-rer'),
        ('decir','to say','¿Qué dices?','de-seer'),
        ('ir','to go','Voy al mercado.','eer'),
        ('ver','to see','¿Ves eso?','ver'),
        ('saber','to know (fact)','No sé.','sa-ber'),
        ('dar','to give','Dame un momento.','dar'),
        ('venir','to come','¿Puedes venir?','ve-neer'),
        ('hablar','to speak','¿Hablas español?','a-blar'),
        ('comer','to eat','Vamos a comer.','ko-mer'),
        ('necesitar','to need','Necesito ayuda.','ne-se-si-tar'),
        ('bueno','good','¡Qué bueno!','bwe-no'),
        ('malo','bad','Eso está mal.','ma-lo'),
        ('grande','big','Un país grande.','gran-de'),
        ('pequeño','small','Un detalle pequeño.','pe-ke-nyo'),
        ('mucho','a lot / very','Muchas gracias.','mu-cho'),
        ('poco','a little','Un poco de agua.','po-ko'),
        ('muy','very','Estoy muy cansado.','mwee'),
        ('bien','well / good','Estoy bien.','byen'),
        ('mal','badly / bad','Me siento mal.','mal'),
        ('más','more','¿Más café?','mas'),
        ('menos','less','Menos ruido, por favor.','me-nos'),
        ('qué','what','¿Qué es esto?','ke'),
        ('quién','who','¿Quién eres tú?','kyen'),
        ('dónde','where','¿Dónde está el baño?','don-de'),
        ('cuándo','when','¿Cuándo llegas?','kwan-do'),
        ('por qué','why','¿Por qué tardas?','por ke'),
        ('cómo','how','¿Cómo estás?','ko-mo'),
        ('cuánto','how much / many','¿Cuánto cuesta?','kwan-to'),
        ('aquí / acá','here','Estoy aquí.','a-kee'),
        ('allí / allá','there','Míralo allí.','a-yee'),
        ('ahora','now','Ven ahora.','a-o-ra'),
        ('después','after / later','Hablamos después.','des-pwes'),
        ('hoy','today','¿Qué haces hoy?','oy'),
        ('mañana','tomorrow','Hasta mañana.','ma-nya-na'),
        ('ayer','yesterday','Ayer estuve en casa.','a-yer'),
        ('siempre','always','Siempre llega tarde.','syem-pre'),
        ('nunca','never','Nunca bebo alcohol.','nun-ka'),
        ('a veces','sometimes','A veces como pizza.','a ve-ses'),
        ('y','and','Tú y yo.','ee'),
        ('o','or','¿Té o café?','o'),
        ('pero','but','Es bueno, pero caro.','pe-ro'),
        ('porque','because','No fui porque estaba enfermo.','por-ke'),
        ('si','if','Si quieres...','see'),
        ('que','that (conjunction)','Creo que sí.','ke'),
        ('con','with','Ven conmigo.','kon'),
        ('sin','without','Sin ti no puedo.','seen'),
        ('para','for / in order to','Esto es para ti.','pa-ra'),
        ('de','of / from','Soy de España.','de'),
        ('en','in / at','Estoy en casa.','en'),
        ('a','to / at','Voy a la tienda.','a'),
        ('por','for / through / by','Gracias por todo.','por'),
        ('el / la / los / las','the','El gato. La casa.','el/la'),
        ('un / una','a / an','Un libro. Una silla.','un/u-na'),
        ('mi / mis','my','Mi familia.','mee'),
        ('tu / tus','your','Tu casa.','too'),
        ('su / sus','his / her / their','Su nombre.','soo'),
        ('este / esta','this','Este libro es mío.','es-te'),
        ('ese / esa','that','Esa idea es buena.','e-se'),
        ('tiempo','time / weather','No tengo tiempo.','tyem-po'),
        ('día','day','¡Buen día!','dee-a'),
        ('noche','night','Buenas noches.','no-che'),
        ('semana','week','La semana que viene.','se-ma-na'),
        ('año','year','¡Feliz año nuevo!','a-nyo'),
        ('casa','home / house','Voy a casa.','ka-sa'),
        ('dinero','money','No tengo dinero.','dee-ne-ro'),
        ('trabajo','work / job','Voy al trabajo.','tra-ba-ho'),
        ('amigo / amiga','friend','Mi mejor amigo.','a-mee-go'),
        ('familia','family','Mi familia es todo.','fa-mee-lya'),
        ('agua','water','Un vaso de agua.','a-gwa'),
        ('comida','food','La comida está deliciosa.','ko-mee-da'),
        ('ayuda','help','¡Necesito ayuda!','a-yoo-da'),
        ('no entiendo','I don\'t understand','Lo siento, no entiendo.','no en-tyen-do'),
        ('no sé','I don\'t know','No sé la respuesta.','no se'),
        ('repita por favor','please repeat','¿Puede repetir, por favor?','re-pee-ta'),
        ('¿habla inglés?','do you speak English?','¿Habla inglés?','a-bla in-gles'),
        ('claro','of course / sure','¡Claro que sí!','kla-ro'),
        ('quizás / tal vez','maybe','Quizás mañana.','kee-sas'),
        ('también','also / too','Yo también.','tam-byen'),
        ('solo / solamente','only / just','Solo un momento.','so-lo'),
        ('ya','already / now','Ya terminé.','ya'),
        ('todavía','still / yet','Todavía estoy aquí.','to-da-vee-a'),
        ('rápido','fast / quickly','Habla más rápido.','ra-pee-do'),
        ('despacio','slowly','Habla más despacio.','des-pa-syo'),
    ])

    # ── Spanish: Verbs ────────────────────────────────────────────────────────
    cid = ensure_course('Spanish Verbs: Top 30 Action Words',
        'Master the 30 most essential Spanish verbs to start forming real sentences.',
        'es', True, 'beginner', 'vocabulary', 4800)
    add_vocab(cid, [
        ('ser','to be (permanent)','Soy de España.','ser'),
        ('estar','to be (temporary)','Estoy bien.','es-tar'),
        ('tener','to have','Tengo hambre.','te-ner'),
        ('hacer','to do / make','¿Qué haces?','a-ser'),
        ('ir','to go','Voy al mercado.','eer'),
        ('poder','can / be able to','¿Puedo ayudarte?','po-der'),
        ('decir','to say','¿Qué dices?','de-seer'),
        ('saber','to know','No lo sé.','sa-ber'),
        ('querer','to want','Quiero aprender.','ke-rer'),
        ('llegar','to arrive','Llego a las tres.','ye-gar'),
        ('pasar','to pass / happen','¿Qué pasó?','pa-sar'),
        ('deber','should / must','Debo estudiar.','de-ber'),
        ('poner','to put / place','Pon el libro aquí.','po-ner'),
        ('parecer','to seem','Parece fácil.','pa-re-ser'),
        ('quedar','to remain / stay','Me quedo aquí.','ke-dar'),
        ('creer','to believe / think','Creo que sí.','kre-er'),
        ('hablar','to speak','Hablo español.','a-blar'),
        ('llevar','to carry / wear','Llevo una chaqueta.','ye-var'),
        ('dejar','to leave / let','Déjame tranquilo.','de-har'),
        ('seguir','to follow / continue','Sigue todo recto.','se-geer'),
        ('encontrar','to find / meet','Encontré a mi amigo.','en-kon-trar'),
        ('llamar','to call','Te llamo luego.','ya-mar'),
        ('venir','to come','¿Puedes venir?','ve-neer'),
        ('pensar','to think','Pienso que sí.','pen-sar'),
        ('salir','to leave / go out','Salgo a las ocho.','sa-leer'),
        ('volver','to return','Vuelvo pronto.','vol-ver'),
        ('tomar','to take / drink','Tomo café.','to-mar'),
        ('conocer','to know (person)','Conozco a María.','ko-no-ser'),
        ('vivir','to live','Vivo en Madrid.','vee-veer'),
        ('abrir','to open','Abre la puerta.','a-breer'),
    ])

    # ── Russian: Basics ───────────────────────────────────────────────────────
    cid = ensure_course('Russian Basics: Essential Words',
        'Learn 40 essential Russian words with Cyrillic script and romanization. Perfect for beginners.',
        'ru', True, 'beginner', 'vocabulary', 4100)
    add_vocab(cid, [
        ('да (da)','yes','Да, конечно.','da'),
        ('нет (nyet)','no','Нет, спасибо.','nyet'),
        ('пожалуйста (pozhaluysta)','please / you\'re welcome','Пожалуйста, помогите.','po-zha-luy-sta'),
        ('спасибо (spasibo)','thank you','Большое спасибо!','spa-see-ba'),
        ('привет (privet)','hello (informal)','Привет! Как дела?','pri-vyet'),
        ('здравствуйте (zdravstvuyte)','hello (formal)','Здравствуйте!','zdra-stvuy-tye'),
        ('до свидания (do svidaniya)','goodbye (formal)','До свидания!','do svi-da-ni-ya'),
        ('пока (poka)','bye (informal)','Пока! Увидимся.','pa-ka'),
        ('извините (izvinite)','sorry / excuse me','Извините, не понял.','iz-vi-ni-tye'),
        ('доброе утро (dobroye utro)','good morning','Доброе утро!','dob-ra-ye ut-ra'),
        ('доброй ночи (dobroy nochi)','good night','Доброй ночи!','dob-ray no-chi'),
        ('я (ya)','I','Я студент.','ya'),
        ('ты (ty)','you (informal)','Ты говоришь по-русски?','ty'),
        ('он / она (on/ona)','he / she','Она моя подруга.','on/a-na'),
        ('мы (my)','we','Мы идём домой.','my'),
        ('они (oni)','they','Они опаздывают.','a-ni'),
        ('хорошо (khorosho)','good / okay','Всё хорошо.','kha-ra-sho'),
        ('плохо (plokho)','bad','Это плохо.','plo-kha'),
        ('большой (bolshoy)','big','Большой город.','bal-shoy'),
        ('маленький (malenky)','small','Маленькая кошка.','ma-lyen-ki'),
        ('новый (novy)','new','Новый телефон.','no-vy'),
        ('старый (stary)','old','Старый дом.','sta-ry'),
        ('вода (voda)','water','Дайте воды, пожалуйста.','va-da'),
        ('еда (yeda)','food','Еда очень вкусная.','ye-da'),
        ('дом (dom)','home / house','Я иду домой.','dom'),
        ('деньги (dengi)','money','У меня нет денег.','dyen-gi'),
        ('друг / подруга (drug/podruga)','friend (m/f)','Она моя лучшая подруга.','drug/pad-ru-ga'),
        ('семья (semya)','family','Моя семья большая.','sem-ya'),
        ('сегодня (segodnya)','today','Сегодня понедельник.','si-vod-nya'),
        ('завтра (zavtra)','tomorrow','Увидимся завтра.','zav-tra'),
        ('вчера (vchera)','yesterday','Вчера я был дома.','fche-ra'),
        ('здесь (zdes)','here','Я здесь.','zdyes'),
        ('там (tam)','there','Смотри там!','tam'),
        ('сейчас (seychas)','now','Я сейчас занят.','si-chas'),
        ('время (vremya)','time','Который час?','vrye-mya'),
        ('день (den)','day','Хорошего дня!','dyen'),
        ('ночь (noch)','night','Спокойной ночи.','noch'),
        ('работа (rabota)','work','Я на работе.','ra-bo-ta'),
        ('помогите (pomogite)','help!','Помогите! Это срочно.','pa-ma-gi-tye'),
        ('не понимаю (ne ponimayu)','I don\'t understand','Извините, я не понимаю.','ni pa-ni-ma-yu'),
    ])

    # ── Russian: 100 Most Common Conversational Words ─────────────────────────
    cid = ensure_course('Russian: 100 Most Common Conversational Words',
        'The 100 most essential Russian words for real everyday conversation, with Cyrillic and pronunciation.',
        'ru', True, 'beginner', 'vocabulary', 3500)
    add_vocab(cid, [
        ('да (da)','yes','Да, понял.','da'),
        ('нет (nyet)','no','Нет, спасибо.','nyet'),
        ('пожалуйста (pozhaluysta)','please / you\'re welcome','Пожалуйста!','po-zha-luy-sta'),
        ('спасибо (spasibo)','thank you','Спасибо большое!','spa-see-ba'),
        ('привет (privet)','hello','Привет!','pri-vyet'),
        ('до свидания (do svidaniya)','goodbye','До свидания!','do svi-da-ni-ya'),
        ('извините (izvinite)','sorry / excuse me','Извините меня.','iz-vi-ni-tye'),
        ('я (ya)','I','Я хочу есть.','ya'),
        ('ты (ty)','you (informal)','Ты понимаешь?','ty'),
        ('он / она (on/ona)','he / she','Он дома.','on/a-na'),
        ('мы (my)','we','Мы идём.','my'),
        ('вы (vy)','you (formal/plural)','Вы говорите по-английски?','vy'),
        ('они (oni)','they','Они придут.','a-ni'),
        ('быть (byt)','to be','Я буду дома.','byt'),
        ('иметь (imet)','to have','У меня есть книга.','i-myet'),
        ('идти (idti)','to go (on foot)','Я иду домой.','id-ti'),
        ('ехать (yekhat)','to go (by transport)','Едем на метро.','ye-khat'),
        ('делать (delat)','to do / make','Что ты делаешь?','dye-lat'),
        ('говорить (govorit)','to speak / say','Говорите медленнее.','ga-va-reet'),
        ('знать (znat)','to know','Я не знаю.','znat'),
        ('хотеть (khotet)','to want','Я хочу кофе.','kha-tyet'),
        ('мочь (moch)','can / to be able','Можете помочь?','moch'),
        ('видеть (videt)','to see','Я вижу тебя.','vi-dyet'),
        ('есть (yest)','to eat','Хочу есть.','yest'),
        ('пить (pit)','to drink','Пить воду.','pit'),
        ('понимать (ponimat)','to understand','Я понимаю.','pa-ni-mat'),
        ('думать (dumat)','to think','Я думаю, что да.','du-mat'),
        ('помочь (pomoch)','to help','Помогите, пожалуйста.','pa-moch'),
        ('хороший (khoroshy)','good','Хороший день.','kha-ro-shy'),
        ('плохой (plokoy)','bad','Плохое настроение.','pla-koy'),
        ('большой (bolshoy)','big','Большая проблема.','bal-shoy'),
        ('маленький (malenky)','small','Маленький шаг.','ma-lyen-ki'),
        ('новый (novy)','new','Новая работа.','no-vy'),
        ('старый (stary)','old','Старый друг.','sta-ry'),
        ('быстро (bystro)','fast / quickly','Говорите быстро.','bys-tra'),
        ('медленно (medlenno)','slowly','Говорите медленнее.','myed-lye-na'),
        ('правильно (pravilno)','correct / right','Правильно!','pra-vil-na'),
        ('неправильно (nepravilno)','wrong / incorrect','Это неправильно.','ni-pra-vil-na'),
        ('что (chto)','what','Что это?','shto'),
        ('кто (kto)','who','Кто ты?','kto'),
        ('где (gde)','where','Где туалет?','gdye'),
        ('когда (kogda)','when','Когда ты придёшь?','kag-da'),
        ('почему (pochemu)','why','Почему ты грустишь?','pa-chi-mu'),
        ('как (kak)','how','Как дела?','kak'),
        ('сколько (skolko)','how much / many','Сколько стоит?','skol-ka'),
        ('здесь (zdes)','here','Я здесь.','zdyes'),
        ('там (tam)','there','Смотри там.','tam'),
        ('сейчас (seychas)','now','Иди сейчас.','si-chas'),
        ('потом (potom)','later / then','Потом поговорим.','pa-tom'),
        ('сегодня (segodnya)','today','Сегодня хорошая погода.','si-vod-nya'),
        ('завтра (zavtra)','tomorrow','До завтра!','zav-tra'),
        ('вчера (vchera)','yesterday','Вчера было холодно.','fche-ra'),
        ('всегда (vsegda)','always','Он всегда опаздывает.','fsig-da'),
        ('никогда (nikogda)','never','Никогда не говори никогда.','ni-kag-da'),
        ('иногда (inogda)','sometimes','Иногда я устаю.','i-nag-da'),
        ('часто (chasto)','often','Я часто хожу туда.','chas-ta'),
        ('очень (ochen)','very','Очень вкусно!','o-chin'),
        ('тоже (tozhe)','also / too','Я тоже хочу.','to-zhe'),
        ('ещё (yeshchyo)','still / yet / more','Ещё раз, пожалуйста.','yi-sho'),
        ('уже (uzhe)','already','Я уже закончил.','u-zhe'),
        ('только (tolko)','only / just','Только один раз.','tol-ka'),
        ('и (i)','and','Ты и я.','i'),
        ('или (ili)','or','Чай или кофе?','i-li'),
        ('но (no)','but','Красиво, но дорого.','no'),
        ('потому что (potomu chto)','because','Я устал, потому что работал.','pa-ta-mu shto'),
        ('если (yesli)','if','Если хочешь...','yes-li'),
        ('что (chto)','that (conjunction)','Думаю, что да.','shto'),
        ('с (s)','with','Иди со мной.','s'),
        ('без (bez)','without','Без тебя скучно.','byez'),
        ('для (dlya)','for','Это для тебя.','dlya'),
        ('из (iz)','from / of','Я из России.','iz'),
        ('в (v)','in / into','Я в Москве.','v'),
        ('на (na)','on / at','На столе.','na'),
        ('время (vremya)','time','Нет времени.','vrye-mya'),
        ('день (den)','day','Хорошего дня!','dyen'),
        ('ночь (noch)','night','Спокойной ночи.','noch'),
        ('неделя (nedelya)','week','На следующей неделе.','ni-dye-lya'),
        ('год (god)','year','С Новым годом!','god'),
        ('дом (dom)','home / house','Иду домой.','dom'),
        ('деньги (dengi)','money','Нет денег.','dyen-gi'),
        ('работа (rabota)','work','На работе.','ra-bo-ta'),
        ('друг / подруга (drug)','friend','Мой лучший друг.','drug'),
        ('семья (semya)','family','Моя семья.','sem-ya'),
        ('вода (voda)','water','Стакан воды.','va-da'),
        ('еда (yeda)','food','Еда готова.','ye-da'),
        ('помощь (pomoshch)','help','Мне нужна помощь.','po-mashch'),
        ('не понимаю (ne ponimayu)','I don\'t understand','Я не понимаю.','ni pa-ni-ma-yu'),
        ('не знаю (ne znayu)','I don\'t know','Я не знаю.','ni zna-yu'),
        ('повторите (povtorite)','please repeat','Повторите, пожалуйста.','paf-ta-ri-tye'),
        ('по-английски (po-angliyski)','in English','Говорите по-английски?','pa ang-liy-ski'),
        ('конечно (konechno)','of course','Конечно!','ka-nyech-na'),
        ('может быть (mozhet byt)','maybe','Может быть завтра.','mo-zhet byt'),
        ('хорошо (khorosho)','okay / alright','Хорошо, договорились.','kha-ra-sho'),
        ('пожалуйста (pozhaluysta)','please','Скажите, пожалуйста.','po-zha-luy-sta'),
        ('всё (vsyo)','everything / all done','Всё понятно.','fsyo'),
        ('ничего (nichego)','nothing / no problem','Ничего страшного.','ni-chi-vo'),
        ('вот (vot)','here is / here you go','Вот ваш заказ.','vot'),
        ('приятно (priyatno)','nice / pleasant','Приятно познакомиться.','pri-yat-na'),
    ])

    # ── Ukrainian: Basics ─────────────────────────────────────────────────────
    cid = ensure_course('Ukrainian Basics: Essential Words',
        'Learn 40 essential Ukrainian words with Cyrillic script and pronunciation. Perfect for beginners.',
        'uk', True, 'beginner', 'vocabulary', 2800)
    add_vocab(cid, [
        ('так (tak)','yes','Так, звісно.','tak'),
        ('ні (ni)','no','Ні, дякую.','ni'),
        ('будь ласка (bud laska)','please / you\'re welcome','Будь ласка, допоможіть.','bud las-ka'),
        ('дякую (dyakuyu)','thank you','Щиро дякую!','dya-ku-yu'),
        ('привіт (pryvit)','hello (informal)','Привіт! Як справи?','pry-vit'),
        ('добрий день (dobry den)','good day / hello','Добрий день!','dob-ry den'),
        ('до побачення (do pobachennya)','goodbye','До побачення!','do po-ba-chen-nya'),
        ('вибачте (vybachte)','sorry / excuse me','Вибачте, я не зрозумів.','vy-bach-te'),
        ('на добраніч (na dobranich)','good night','На добраніч!','na dob-ra-nich'),
        ('я (ya)','I','Я студент.','ya'),
        ('ти (ty)','you (informal)','Ти говориш по-українськи?','ty'),
        ('він / вона (vin/vona)','he / she','Вона моя подруга.','vin/vo-na'),
        ('ми (my)','we','Ми йдемо додому.','my'),
        ('вони (vony)','they','Вони запізнюються.','vo-ny'),
        ('добре (dobre)','good / okay','Все добре.','dob-re'),
        ('погано (pohano)','bad','Це погано.','po-ha-no'),
        ('великий (velyky)','big','Велике місто.','ve-ly-ky'),
        ('маленький (malenky)','small','Маленька кішка.','ma-len-ky'),
        ('новий (novy)','new','Новий телефон.','no-vy'),
        ('старий (stary)','old','Старий будинок.','sta-ry'),
        ('вода (voda)','water','Дайте води, будь ласка.','vo-da'),
        ('їжа (yizha)','food','Їжа дуже смачна.','yi-zha'),
        ('дім (dim)','home / house','Я йду додому.','dim'),
        ('гроші (groshi)','money','У мене нема грошей.','gro-shi'),
        ('друг / подруга (druh/podruga)','friend (m/f)','Вона моя краща подруга.','druh/pod-ru-ha'),
        ('родина (rodyna)','family','Моя родина велика.','ro-dy-na'),
        ('сьогодні (syohodni)','today','Сьогодні понеділок.','syo-hod-ni'),
        ('завтра (zavtra)','tomorrow','До завтра!','zav-tra'),
        ('вчора (vchora)','yesterday','Вчора я був вдома.','vcho-ra'),
        ('тут (tut)','here','Я тут.','tut'),
        ('там (tam)','there','Подивись там!','tam'),
        ('зараз (zaraz)','now','Я зараз зайнятий.','za-raz'),
        ('час (chas)','time','Котра година?','chas'),
        ('день (den)','day','Гарного дня!','den'),
        ('ніч (nich)','night','На добраніч.','nich'),
        ('робота (robota)','work','Я на роботі.','ro-bo-ta'),
        ('допоможіть (dopomozit)','help!','Допоможіть! Це терміново.','do-po-mo-zhit'),
        ('не розумію (ne rozumiyu)','I don\'t understand','Вибачте, я не розумію.','ne ro-zu-mi-yu'),
        ('дуже (duzhe)','very','Дуже дякую.','du-zhe'),
        ('красиво (krasivo)','beautiful','Яка краса!','kra-sy-vo'),
    ])

    # ── Ukrainian: 100 Most Common Conversational Words ───────────────────────
    cid = ensure_course('Ukrainian: 100 Most Common Conversational Words',
        'The 100 most essential Ukrainian words for everyday conversation, with Cyrillic and pronunciation.',
        'uk', True, 'beginner', 'vocabulary', 2400)
    add_vocab(cid, [
        ('так (tak)','yes','Так, правильно.','tak'),
        ('ні (ni)','no','Ні, дякую.','ni'),
        ('будь ласка (bud laska)','please / you\'re welcome','Будь ласка!','bud las-ka'),
        ('дякую (dyakuyu)','thank you','Дуже дякую!','dya-ku-yu'),
        ('привіт (pryvit)','hello','Привіт!','pry-vit'),
        ('до побачення (do pobachennya)','goodbye','До побачення!','do po-ba-chen-nya'),
        ('вибачте (vybachte)','sorry / excuse me','Вибачте мене.','vy-bach-te'),
        ('я (ya)','I','Я хочу їсти.','ya'),
        ('ти (ty)','you (informal)','Ти розумієш?','ty'),
        ('він / вона (vin/vona)','he / she','Він вдома.','vin/vo-na'),
        ('ми (my)','we','Ми йдемо.','my'),
        ('ви (vy)','you (formal/plural)','Ви говорите по-англійськи?','vy'),
        ('вони (vony)','they','Вони прийдуть.','vo-ny'),
        ('бути (buty)','to be','Я буду вдома.','bu-ty'),
        ('мати (maty)','to have','У мене є книга.','ma-ty'),
        ('іти (ity)','to go (on foot)','Я йду додому.','i-ty'),
        ('їхати (yikhaty)','to go (by transport)','Їдемо на метро.','yi-kha-ty'),
        ('робити (robyty)','to do / make','Що ти робиш?','ro-by-ty'),
        ('говорити (hovoryty)','to speak / say','Говоріть повільніше.','ho-vo-ry-ty'),
        ('знати (znaty)','to know','Я не знаю.','zna-ty'),
        ('хотіти (khotiты)','to want','Я хочу каву.','kho-ti-ty'),
        ('могти (mohty)','can / to be able','Можете допомогти?','moh-ty'),
        ('бачити (bachyty)','to see','Я бачу тебе.','ba-chy-ty'),
        ('їсти (yisty)','to eat','Хочу їсти.','yis-ty'),
        ('пити (pyty)','to drink','Пити воду.','py-ty'),
        ('розуміти (rozumity)','to understand','Я розумію.','ro-zu-mi-ty'),
        ('думати (dumaty)','to think','Я думаю, що так.','du-ma-ty'),
        ('допомогти (dopomohty)','to help','Допоможіть, будь ласка.','do-po-moh-ty'),
        ('добрий (dobry)','good','Добрий день.','dob-ry'),
        ('поганий (pohany)','bad','Погане настроєння.','po-ha-ny'),
        ('великий (velyky)','big','Велике місто.','ve-ly-ky'),
        ('маленький (malenky)','small','Маленький крок.','ma-len-ky'),
        ('новий (novy)','new','Нова робота.','no-vy'),
        ('старий (stary)','old','Старий друг.','sta-ry'),
        ('швидко (shvydko)','fast / quickly','Говоріть швидше.','shvyd-ko'),
        ('повільно (povilno)','slowly','Говоріть повільніше.','po-vil-no'),
        ('правильно (pravylno)','correct / right','Правильно!','pra-vyl-no'),
        ('неправильно (nepravylno)','wrong','Це неправильно.','ne-pra-vyl-no'),
        ('що (shcho)','what','Що це?','shcho'),
        ('хто (khto)','who','Хто ти?','khto'),
        ('де (de)','where','Де туалет?','de'),
        ('коли (koly)','when','Коли ти прийдеш?','ko-ly'),
        ('чому (chomu)','why','Чому ти плачеш?','cho-mu'),
        ('як (yak)','how','Як справи?','yak'),
        ('скільки (skilky)','how much / many','Скільки коштує?','skil-ky'),
        ('тут (tut)','here','Я тут.','tut'),
        ('там (tam)','there','Дивись там.','tam'),
        ('зараз (zaraz)','now','Іди зараз.','za-raz'),
        ('потім (potim)','later / then','Поговоримо потім.','po-tim'),
        ('сьогодні (syohodni)','today','Сьогодні гарна погода.','syo-hod-ni'),
        ('завтра (zavtra)','tomorrow','До завтра!','zav-tra'),
        ('вчора (vchora)','yesterday','Вчора було холодно.','vcho-ra'),
        ('завжди (zavzhdy)','always','Він завжди запізнюється.','zav-zhdy'),
        ('ніколи (nikoly)','never','Ніколи не здавайся.','ni-ko-ly'),
        ('іноді (inodi)','sometimes','Іноді я втомлююся.','i-no-di'),
        ('часто (chasto)','often','Я часто туди ходжу.','chas-to'),
        ('дуже (duzhe)','very','Дуже смачно!','du-zhe'),
        ('також (takozh)','also / too','Я також хочу.','ta-kozh'),
        ('ще (shche)','still / yet / more','Ще раз, будь ласка.','shche'),
        ('вже (vzhe)','already','Я вже закінчив.','vzhe'),
        ('тільки (tilky)','only / just','Тільки один раз.','til-ky'),
        ('і (i)','and','Ти і я.','i'),
        ('або (abo)','or','Чай або кава?','a-bo'),
        ('але (ale)','but','Красиво, але дорого.','a-le'),
        ('тому що (tomu shcho)','because','Я втомився, тому що працював.','to-mu shcho'),
        ('якщо (yakshcho)','if','Якщо хочеш...','yak-shcho'),
        ('що (shcho)','that (conjunction)','Думаю, що так.','shcho'),
        ('з (z)','with','Іди зі мною.','z'),
        ('без (bez)','without','Без тебе нудно.','bez'),
        ('для (dlya)','for','Це для тебе.','dlya'),
        ('з (z)','from / of','Я з України.','z'),
        ('в / у (v/u)','in / into','Я в Києві.','v/u'),
        ('на (na)','on / at','На столі.','na'),
        ('час (chas)','time','Немає часу.','chas'),
        ('день (den)','day','Гарного дня!','den'),
        ('ніч (nich)','night','На добраніч.','nich'),
        ('тиждень (tyzhden)','week','Наступного тижня.','tyzh-den'),
        ('рік (rik)','year','З Новим роком!','rik'),
        ('дім (dim)','home / house','Йду додому.','dim'),
        ('гроші (groshi)','money','Немає грошей.','gro-shi'),
        ('робота (robota)','work','На роботі.','ro-bo-ta'),
        ('друг / подруга (druh)','friend','Мій найкращий друг.','druh'),
        ('родина (rodyna)','family','Моя родина.','ro-dy-na'),
        ('вода (voda)','water','Склянка води.','vo-da'),
        ('їжа (yizha)','food','Їжа готова.','yi-zha'),
        ('допомога (dopomoga)','help','Мені потрібна допомога.','do-po-mo-ha'),
        ('не розумію (ne rozumiyu)','I don\'t understand','Я не розумію.','ne ro-zu-mi-yu'),
        ('не знаю (ne znayu)','I don\'t know','Я не знаю.','ne zna-yu'),
        ('повторіть (povtority)','please repeat','Повторіть, будь ласка.','pov-to-rit'),
        ('по-англійськи (po-angliysky)','in English','Говоріть по-англійськи?','po-ang-liy-sky'),
        ('звісно (zvísno)','of course','Звісно!','zvis-no'),
        ('може (mozhe)','maybe','Може завтра.','mo-zhe'),
        ('добре (dobre)','okay / alright','Добре, домовились.','dob-re'),
        ('все (vse)','everything / all','Все зрозуміло.','vse'),
        ('нічого (nichoho)','nothing / no problem','Нічого страшного.','ni-cho-ho'),
        ('ось (osh)','here is / here you go','Ось ваше замовлення.','osh'),
        ('приємно (pryyemno)','nice / pleasant','Приємно познайомитись.','pry-yem-no'),
        ('красиво (krasyvo)','beautiful','Яка краса!','kra-sy-vo'),
        ('Україна (Ukrayina)','Ukraine','Я люблю Україну.','u-kra-yi-na'),
    ])

    # ── Polish: Basics ────────────────────────────────────────────────────────
    cid = ensure_course('Polish Basics: Essential Words',
        'Learn 40 essential Polish words with pronunciation. Your first steps into the Polish language.',
        'pl', True, 'beginner', 'vocabulary', 3200)
    add_vocab(cid, [
        ('tak','yes','Tak, oczywiście.','tak'),
        ('nie','no','Nie, dziękuję.','nye'),
        ('proszę','please / you\'re welcome','Proszę, pomóż mi.','pro-she'),
        ('dziękuję','thank you','Bardzo dziękuję!','dyen-ku-ye'),
        ('cześć','hello / hi (informal)','Cześć! Co słychać?','cheshch'),
        ('dzień dobry','good morning / good day','Dzień dobry!','dyen dob-ry'),
        ('do widzenia','goodbye','Do widzenia!','do vi-dze-nya'),
        ('przepraszam','sorry / excuse me','Przepraszam, nie rozumiem.','pshe-pra-sham'),
        ('dobranoc','good night','Dobranoc!','dob-ra-nots'),
        ('ja','I','Ja jestem studentem.','ya'),
        ('ty','you (informal)','Czy ty mówisz po polsku?','ty'),
        ('on / ona','he / she','Ona jest moją przyjaciółką.','on/o-na'),
        ('my','we','My idziemy do domu.','my'),
        ('oni / one','they (m/f)','Oni spóźniają się.','o-ni/o-ne'),
        ('dobrze','good / okay','Wszystko dobrze.','dob-zhe'),
        ('źle','bad','To jest źle.','zhle'),
        ('duży','big','Duże miasto.','du-zhy'),
        ('mały','small','Mały kot.','ma-wy'),
        ('nowy','new','Nowy telefon.','no-vy'),
        ('stary','old','Stary dom.','sta-ry'),
        ('woda','water','Proszę o wodę.','vo-da'),
        ('jedzenie','food','Jedzenie jest pyszne.','ye-dze-nye'),
        ('dom','home / house','Idę do domu.','dom'),
        ('pieniądze','money','Nie mam pieniędzy.','pye-nyond-ze'),
        ('przyjaciel / przyjaciółka','friend (m/f)','To moja przyjaciółka.','pshi-ya-chyel'),
        ('rodzina','family','Moja rodzina jest duża.','ro-dzi-na'),
        ('dzisiaj','today','Dzisiaj jest poniedziałek.','dzi-shay'),
        ('jutro','tomorrow','Do jutra!','yut-ro'),
        ('wczoraj','yesterday','Wczoraj byłem w domu.','fcho-ray'),
        ('tutaj','here','Jestem tutaj.','tu-tay'),
        ('tam','there','Patrz tam!','tam'),
        ('teraz','now','Jestem teraz zajęty.','te-raz'),
        ('czas','time','Która godzina?','chas'),
        ('dzień','day','Miłego dnia!','dyen'),
        ('noc','night','Dobranoc.','nots'),
        ('praca','work','Jestem w pracy.','pra-tsa'),
        ('pomoc','help','Potrzebuję pomocy!','po-mots'),
        ('nie rozumiem','I don\'t understand','Przepraszam, nie rozumiem.','nye ro-zu-myem'),
        ('bardzo','very','Bardzo dziękuję.','bar-dzo'),
        ('pięknie','beautiful / great','To jest piękne!','pyen-kye'),
    ])

    # ── Polish: 100 Most Common Conversational Words ──────────────────────────
    cid = ensure_course('Polish: 100 Most Common Conversational Words',
        'The 100 most essential Polish words for real everyday conversation.',
        'pl', True, 'beginner', 'vocabulary', 2700)
    add_vocab(cid, [
        ('tak','yes','Tak, masz rację.','tak'),
        ('nie','no','Nie, dziękuję.','nye'),
        ('proszę','please / you\'re welcome','Proszę!','pro-she'),
        ('dziękuję','thank you','Bardzo dziękuję!','dyen-ku-ye'),
        ('cześć','hello','Cześć!','cheshch'),
        ('do widzenia','goodbye','Do widzenia!','do vi-dze-nya'),
        ('przepraszam','sorry / excuse me','Przepraszam.','pshe-pra-sham'),
        ('ja','I','Ja chcę jeść.','ya'),
        ('ty','you (informal)','Ty rozumiesz?','ty'),
        ('on / ona','he / she','On jest w domu.','on/o-na'),
        ('my','we','My idziemy.','my'),
        ('wy','you (formal/plural)','Czy wy mówicie po angielsku?','vy'),
        ('oni','they','Oni przyjdą.','o-ni'),
        ('być','to be','Będę w domu.','bych'),
        ('mieć','to have','Mam książkę.','myech'),
        ('iść','to go (on foot)','Idę do domu.','ishch'),
        ('jechać','to go (by transport)','Jedziemy metrem.','ye-khach'),
        ('robić','to do / make','Co robisz?','ro-bich'),
        ('mówić','to speak / say','Mów wolniej.','mu-vich'),
        ('wiedzieć','to know (fact)','Nie wiem.','vye-dyech'),
        ('chcieć','to want','Chcę kawy.','khchech'),
        ('móc','can / to be able','Czy możesz pomóc?','muts'),
        ('widzieć','to see','Widzę cię.','vi-dyech'),
        ('jeść','to eat','Chcę jeść.','yeshch'),
        ('pić','to drink','Pić wodę.','pich'),
        ('rozumieć','to understand','Rozumiem.','ro-zu-myech'),
        ('myśleć','to think','Myślę, że tak.','mys-lech'),
        ('pomóc','to help','Proszę o pomoc.','po-muts'),
        ('dobry','good','Dobry dzień.','dob-ry'),
        ('zły','bad','Zły nastrój.','zwy'),
        ('duży','big','Duże miasto.','du-zhy'),
        ('mały','small','Mały krok.','ma-wy'),
        ('nowy','new','Nowa praca.','no-vy'),
        ('stary','old','Stary przyjaciel.','sta-ry'),
        ('szybko','fast / quickly','Mów szybciej.','shyp-ko'),
        ('wolno','slowly','Mów wolniej.','vol-no'),
        ('dobrze','correct / right / okay','Dobrze!','dob-zhe'),
        ('źle','wrong / badly','To jest źle.','zhle'),
        ('co','what','Co to jest?','tso'),
        ('kto','who','Kto ty jesteś?','kto'),
        ('gdzie','where','Gdzie jest toaleta?','gdze'),
        ('kiedy','when','Kiedy przyjdziesz?','kye-dy'),
        ('dlaczego','why','Dlaczego płaczesz?','dla-che-go'),
        ('jak','how','Jak się masz?','yak'),
        ('ile','how much / many','Ile to kosztuje?','i-le'),
        ('tutaj','here','Jestem tutaj.','tu-tay'),
        ('tam','there','Patrz tam.','tam'),
        ('teraz','now','Chodź teraz.','te-raz'),
        ('potem','later / then','Porozmawiamy potem.','po-tem'),
        ('dzisiaj','today','Dzisiaj jest ładna pogoda.','dzi-shay'),
        ('jutro','tomorrow','Do jutra!','yut-ro'),
        ('wczoraj','yesterday','Wczoraj było zimno.','fcho-ray'),
        ('zawsze','always','On zawsze się spóźnia.','zav-she'),
        ('nigdy','never','Nigdy się nie poddawaj.','nig-dy'),
        ('czasami','sometimes','Czasami jestem zmęczony.','cha-sa-mi'),
        ('często','often','Często tu przychodzę.','chen-sto'),
        ('bardzo','very','Bardzo smaczne!','bar-dzo'),
        ('też','also / too','Ja też chcę.','tesh'),
        ('jeszcze','still / yet / more','Jeszcze raz, proszę.','yesh-che'),
        ('już','already','Już skończyłem.','yoozh'),
        ('tylko','only / just','Tylko raz.','tyl-ko'),
        ('i','and','Ty i ja.','i'),
        ('lub / albo','or','Herbata lub kawa?','lub/al-bo'),
        ('ale','but','Piękne, ale drogie.','a-le'),
        ('ponieważ','because','Jestem zmęczony, ponieważ pracowałem.','po-nye-vash'),
        ('jeśli / jeżeli','if','Jeśli chcesz...','yes-li'),
        ('że','that (conjunction)','Myślę, że tak.','zhe'),
        ('z','with','Chodź ze mną.','z'),
        ('bez','without','Bez ciebie nudno.','bez'),
        ('dla','for','To jest dla ciebie.','dla'),
        ('z','from / of','Jestem z Polski.','z'),
        ('w','in / into','Jestem w Warszawie.','v'),
        ('na','on / at','Na stole.','na'),
        ('czas','time','Nie ma czasu.','chas'),
        ('dzień','day','Miłego dnia!','dyen'),
        ('noc','night','Dobranoc.','nots'),
        ('tydzień','week','W przyszłym tygodniu.','ty-dzen'),
        ('rok','year','Szczęśliwego Nowego Roku!','rok'),
        ('dom','home / house','Idę do domu.','dom'),
        ('pieniądze','money','Nie ma pieniędzy.','pye-nyond-ze'),
        ('praca','work','W pracy.','pra-tsa'),
        ('przyjaciel','friend','Mój najlepszy przyjaciel.','pshi-ya-chyel'),
        ('rodzina','family','Moja rodzina.','ro-dzi-na'),
        ('woda','water','Szklanka wody.','vo-da'),
        ('jedzenie','food','Jedzenie gotowe.','ye-dze-nye'),
        ('pomoc','help','Potrzebuję pomocy.','po-mots'),
        ('nie rozumiem','I don\'t understand','Nie rozumiem.','nye ro-zu-myem'),
        ('nie wiem','I don\'t know','Nie wiem.','nye vyem'),
        ('proszę powtórzyć','please repeat','Proszę powtórzyć.','pro-she pov-tu-zhich'),
        ('po angielsku','in English','Mówisz po angielsku?','po ang-yel-sku'),
        ('oczywiście','of course','Oczywiście!','o-chi-vish-chye'),
        ('może','maybe','Może jutro.','mo-zhe'),
        ('w porządku','okay / alright','W porządku, zgoda.','f po-zhond-ku'),
        ('wszystko','everything / all','Wszystko jasne.','fshyst-ko'),
        ('nic','nothing / no problem','Nic się nie stało.','nits'),
        ('proszę','here you go','Proszę, oto zamówienie.','pro-she'),
        ('miło','nice / pleasant','Miło cię poznać.','mi-wo'),
        ('Polska','Poland','Kocham Polskę.','pol-ska'),
    ])

    conn.commit()
    conn.close()
    print("Database updated with new languages and courses.")


# ── Query helpers (used by app.py) ────────────────────────────────────────────

def get_course_avg_rating(conn, course_id):
    row = conn.execute(
        "SELECT ROUND(AVG(rating::numeric), 1) as avg_rating, COUNT(*) as count FROM course_ratings WHERE course_id=?"
        if BACKEND == 'postgres' else
        "SELECT ROUND(AVG(rating), 1) as avg_rating, COUNT(*) as count FROM course_ratings WHERE course_id=?",
        (course_id,)
    ).fetchone()
    return {'avg': row['avg_rating'] or 0, 'count': row['count']}


def get_user_rating(conn, course_id, user_id):
    if not user_id:
        return None
    row = conn.execute(
        "SELECT rating FROM course_ratings WHERE course_id=? AND user_id=?",
        (course_id, user_id)
    ).fetchone()
    return row['rating'] if row else None


def is_enrolled(conn, course_id, user_id):
    if not user_id:
        return False
    return conn.execute(
        "SELECT 1 FROM enrollments WHERE course_id=? AND user_id=?",
        (course_id, user_id)
    ).fetchone() is not None
