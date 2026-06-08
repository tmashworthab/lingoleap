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
    xp INTEGER NOT NULL DEFAULT 0,
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
    xp INTEGER NOT NULL DEFAULT 0,
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
"""


def init_db():
    conn = get_db()
    schema = _PG_SCHEMA if BACKEND == 'postgres' else _SQLITE_SCHEMA
    for stmt in schema.split(';'):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
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
        'Start your Nepali adventure! Learn 35 essential words with Devanagari script and romanization.',
        'ne', True, 'beginner', 'vocabulary', 2940)
    add_vocab(cid, [
        ('हो','yes','हो, यो सहि हो।','ho'),
        ('होइन','no','होइन, मलाई चाहिँदैन।','hoina'),
        ('नमस्ते','hello / namaste','नमस्ते! कस्तो छ?','namaste'),
        ('धन्यवाद','thank you','तपाईंलाई धन्यवाद।','dhanyabad'),
        ('कृपया','please','कृपया मलाई मद्दत गर्नुहोस्।','kripaya'),
        ('माफ गर्नुहोस्','sorry / excuse me','माफ गर्नुहोस्, म बुझिनँ।','maaf garnuhos'),
        ('अलविदा','goodbye','अलविदा! फेरि भेटौँला।','alvida'),
        ('बच्चा','child','बच्चा पार्कमा खेल्छ।','bachcha'),
        ('कुकुर','dog','कुकुर भुक्छ।','kukur'),
        ('बिरालो','cat','बिरालो सुत्छ।','biralo'),
        ('घर','house','हाम्रो घर ठूलो छ।','ghar'),
        ('गाडी','car','गाडी रातो छ।','gadi'),
        ('किताब','book','यो किताब रोचक छ।','kitab'),
        ('पानी','water','मलाई पानी चाहियो।','pani'),
        ('खाना','food','खाना मिठो छ।','khana'),
        ('विद्यालय','school','विद्यालय ८ बजे सुरु हुन्छ।','vidyalay'),
        ('सडक','road / street','सडक लामो छ।','sadak'),
        ('शहर','city','काठमाण्डौ सुन्दर शहर हो।','shahar'),
        ('देश','country','नेपाल सुन्दर देश हो।','desh'),
        ('पानी','water','मलाई पानी चाहियो।','pani'),
        ('दिन','day','शुभ दिन!','din'),
        ('रात','night','शुभ रात्री!','raat'),
        ('आज','today','आज सोमबार हो।','aaj'),
        ('भोलि','tomorrow','भोलि बजार जान्छु।','bholi'),
        ('हिजो','yesterday','हिजो म थाकेको थिएँ।','hijo'),
        ('शुभ प्रभात','good morning','शुभ प्रभात! कस्तो छ?','shubha prabhaat'),
        ('शुभ रात्री','good night','शुभ रात्री! राम्रो सपना।','shubha raatri'),
        ('कस्तो छ?','how are you?','नमस्ते! कस्तो छ?','kasto cha'),
        ('ठीक छ, धन्यवाद','fine, thank you','ठीक छ, धन्यवाद।','thik cha dhanyabad'),
        ('राम्रो','good / nice','यो खाना राम्रो छ।','ramro'),
    ])

    # ── Nepali Travel ──────────────────────────────────────────────────────────
    cid = add_course('Nepali Travel Phrases',
        'Essential phrases for travelling in Nepal — hotels, directions, shopping, and emergencies.',
        'ne', True, 'beginner', 'conversation', 2010)
    add_vocab(cid, [
        ('होटल कहाँ छ?','Where is the hotel?','माफ गर्नुहोस्, होटल कहाँ छ?','hotel kahaan cha'),
        ('कति पर्छ?','How much does it cost?','यो कति पर्छ?','kati parcha'),
        ('मलाई चाहियो','I need / I want','मलाई पानी चाहियो।','malai chaahiyo'),
        ('मद्दत गर्नुहोस्!','Help!','मद्दत गर्नुहोस्! आपतकाल!','maddat garnuhos'),
        ('अस्पताल','hospital','नजिकको अस्पताल कहाँ छ?','aspatal'),
        ('बायाँ','left','बायाँ मोड्नुहोस्।','baayaa'),
        ('दायाँ','right','दायाँ मोड्नुहोस्।','daayaa'),
        ('सोझो','straight ahead','सोझो जानुहोस्।','sojho'),
        ('बिल ल्याउनुहोस्','bring the bill','बिल ल्याउनुहोस् कृपया।','bil lyaaunuhos'),
        ('राम्रो','good / nice','यो खाना राम्रो छ।','ramro'),
        ('महँगो','expensive','यो धेरै महँगो छ।','mahango'),
        ('सस्तो','cheap','केही सस्तो छ?','sasto'),
        ('बजार','market','बजार कहाँ छ?','bajaar'),
        ('म नेपाली बुझ्दिनँ','I don\'t understand Nepali','माफ गर्नुहोस्, म नेपाली बुझ्दिनँ।','ma nepali bujhdinaa'),
        ('अंग्रेजी बोल्नुहुन्छ?','Do you speak English?','तपाईं अंग्रेजी बोल्नुहुन्छ?','angreji bolnuhuncha'),
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
        ('हिमाल','mountain / Himalaya','हिमाल धेरै अग्लो छ।','himal'),
        ('बाटो','path / trail','बाटो कहाँ छ?','baato'),
        ('लज','lodge (teahouse)','नजिकको लज कहाँ छ?','laj'),
        ('दाल भात','lentil soup and rice','दाल भात खानु छ?','daal bhaat'),
        ('थकाइ लाग्यो','I am tired','धेरै हिँडेँ, थकाइ लाग्यो।','thakai lagyo'),
        ('चिसो','cold','आज धेरै चिसो छ।','chiso'),
        ('तातो','hot / warm','चिया तातो छ।','taato'),
        ('म बिरामी छु','I am sick','मलाई माफ गर्नुहोस्, म बिरामी छु।','ma biraami chu'),
        ('डाक्टर','doctor','डाक्टर कहाँ छ?','daaktar'),
        ('उकालो','uphill','अझै धेरै उकालो छ?','ukaalo'),
        ('ओरालो','downhill','ओरालो सजिलो छ।','oraalo'),
        ('हावा','wind','धेरै हावा छ।','haawaa'),
        ('हिउँ','snow','माथि हिउँ छ।','hiuu'),
        ('मौसम','weather','भोलि मौसम कस्तो हुन्छ?','mausam'),
        ('टर्च','torch / flashlight','टर्च छ तपाईंसँग?','torch'),
    ])

    cid = add_course('Basic Nepali Greetings & Small Talk',
        'Make Nepali friends instantly! Greetings and small talk phrases to break the ice.',
        'ne', False, 'beginner', 'conversation', 2190, creator='KathmanduKid')
    add_vocab(cid, [
        ('नमस्ते / नमस्कार','hello / greetings','नमस्ते! तपाईं कस्तो हुनुहुन्छ?','namaste/namaskar'),
        ('तपाईंको नाम के हो?','What is your name?','तपाईंको नाम के हो, कृपया?','tapaaiko naam ke ho'),
        ('मेरो नाम ... हो','My name is ...','मेरो नाम Sarah हो।','mero naam ... ho'),
        ('तपाईं कहाँबाट हुनुहुन्छ?','Where are you from?','तपाईं कहाँबाट हुनुहुन्छ?','tapaai kahaabata'),
        ('म ... बाट हुँ','I am from ...','म बेलायत बाट हुँ।','ma ... bata hu'),
        ('खुसी लाग्यो भेटेर','Nice to meet you','खुसी लाग्यो भेटेर!','khushi lagyo bheter'),
        ('म विद्यार्थी हुँ','I am a student','म विद्यार्थी हुँ।','ma vidyarthi hu'),
        ('साथी','friend','तिमी मेरो राम्रो साथी हौ।','saathi'),
        ('फेरि भेटौँला','See you again','ठीक छ, फेरि भेटौँला!','pheri bhetaula'),
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
