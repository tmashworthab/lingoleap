import sqlite3
import os
import bcrypt

DB_PATH = os.path.join(os.path.dirname(__file__), 'lingoleap.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.executescript("""
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
    """)
    conn.commit()

    # Migrations: add new columns to existing databases without breaking them
    for sql in [
        "ALTER TABLE users ADD COLUMN google_id TEXT",
        "ALTER TABLE users ADD COLUMN avatar_url TEXT",
    ]:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            pass  # Column already exists — skip

    conn.close()


def seed_db():
    conn = get_db()
    c = conn.cursor()

    # Skip if already seeded
    if c.execute("SELECT COUNT(*) FROM languages").fetchone()[0] > 0:
        conn.close()
        return

    # Languages
    languages = [
        ('German',     'de', '🇩🇪', '#000000', '#FFCE00',
         'German is spoken by over 100 million people across Germany, Austria, Switzerland and more.'),
        ('Lithuanian', 'lt', '🇱🇹', '#006A44', '#C1272D',
         'Lithuanian is one of the oldest living Indo-European languages, spoken by ~3 million people.'),
        ('Nepali',     'ne', '🇳🇵', '#003893', '#DC143C',
         'Nepali is spoken by over 17 million people and is the official language of Nepal.'),
    ]
    c.executemany(
        "INSERT INTO languages (name, code, flag_emoji, color_from, color_to, description) VALUES (?,?,?,?,?,?)",
        languages
    )

    # Mock community creators
    avatar_colors = ['#7C3AED', '#DB2777', '#D97706', '#059669', '#DC2626', '#2563EB', '#D97706', '#7C3AED', '#059669', '#DB2777']
    mock_users = [
        ('GermanNerd2024',  'german@example.com',  'mock1', '#7C3AED'),
        ('FoodieInBerlin',  'foodie@example.com',  'mock2', '#DB2777'),
        ('BalticExplorer',  'baltic@example.com',  'mock3', '#D97706'),
        ('HimalayanHiker',  'hiker@example.com',   'mock4', '#059669'),
        ('BerlinStreetKid', 'berlin@example.com',  'mock5', '#DC2626'),
        ('CorpLinguist',    'corp@example.com',    'mock6', '#2563EB'),
        ('BalticSinger',    'singer@example.com',  'mock7', '#D97706'),
        ('KathmanduKid',    'ktm@example.com',     'mock8', '#7C3AED'),
        ('ArtTeacher',      'art@example.com',     'mock9', '#DB2777'),
        ('LanguageLover',   'lang@example.com',    'mock0', '#059669'),
    ]
    hashed = bcrypt.hashpw(b'placeholder123', bcrypt.gensalt()).decode()
    for (username, email, _, color) in mock_users:
        c.execute(
            "INSERT INTO users (username, email, password_hash, avatar_color, xp) VALUES (?,?,?,?,?)",
            (username, email, hashed, color, 0)
        )

    lang_de = c.execute("SELECT id FROM languages WHERE code='de'").fetchone()['id']
    lang_lt = c.execute("SELECT id FROM languages WHERE code='lt'").fetchone()['id']
    lang_ne = c.execute("SELECT id FROM languages WHERE code='ne'").fetchone()['id']

    # ── OFFICIAL COURSES ─────────────────────────────────────────────────────

    # German Basics
    c.execute(
        "INSERT INTO courses (title, description, language_id, is_official, difficulty, category, enrollment_count) VALUES (?,?,?,1,'beginner','vocabulary',8420)",
        ('German Basics: Essential Words', 'Start your German journey with the 40 most important everyday words. Perfect for absolute beginners.', lang_de)
    )
    german_basics_id = c.lastrowid
    german_basics_vocab = [
        ('ich', 'I', 'Ich bin müde.', 'ikh'),
        ('du', 'you (informal)', 'Du bist toll.', 'doo'),
        ('er / sie / es', 'he / she / it', 'Er ist nett.', 'air / zee / es'),
        ('wir', 'we', 'Wir lernen Deutsch.', 'veer'),
        ('ja', 'yes', 'Ja, das stimmt!', 'yah'),
        ('nein', 'no', 'Nein, das ist falsch.', 'nine'),
        ('bitte', 'please / you\'re welcome', 'Bitte schön!', 'bit-uh'),
        ('danke', 'thank you', 'Danke schön!', 'dan-kuh'),
        ('hallo', 'hello', 'Hallo! Wie geht\'s?', 'ha-lo'),
        ('tschüss', 'goodbye (informal)', 'Tschüss! Bis morgen!', 'chüss'),
        ('auf Wiedersehen', 'goodbye (formal)', 'Auf Wiedersehen!', 'owf-vee-der-zayn'),
        ('gut', 'good', 'Das ist gut.', 'goot'),
        ('schlecht', 'bad', 'Das Wetter ist schlecht.', 'shlesht'),
        ('groß', 'big', 'Das Haus ist groß.', 'groas'),
        ('klein', 'small', 'Die Katze ist klein.', 'kline'),
        ('der Mann', 'the man', 'Der Mann liest.', 'dair man'),
        ('die Frau', 'the woman', 'Die Frau lacht.', 'dee frow'),
        ('das Kind', 'the child', 'Das Kind spielt.', 'das kint'),
        ('der Hund', 'the dog', 'Der Hund bellt.', 'dair hoont'),
        ('die Katze', 'the cat', 'Die Katze schläft.', 'dee kat-zuh'),
        ('das Haus', 'the house', 'Das Haus ist groß.', 'das hows'),
        ('das Auto', 'the car', 'Das Auto ist neu.', 'das ow-to'),
        ('das Buch', 'the book', 'Das Buch ist interessant.', 'das bookh'),
        ('das Wasser', 'the water', 'Ich trinke Wasser.', 'das vas-er'),
        ('das Essen', 'the food', 'Das Essen schmeckt gut.', 'das es-en'),
        ('die Schule', 'the school', 'Die Schule beginnt um 8.', 'dee shoo-luh'),
        ('die Straße', 'the street', 'Die Straße ist lang.', 'dee shtra-suh'),
        ('die Stadt', 'the city', 'Berlin ist eine schöne Stadt.', 'dee shtat'),
        ('heute', 'today', 'Heute ist Montag.', 'hoy-tuh'),
        ('morgen', 'tomorrow', 'Morgen gehe ich schwimmen.', 'mor-gen'),
        ('gestern', 'yesterday', 'Gestern war ich müde.', 'ges-tern'),
        ('der Tag', 'the day', 'Schönen Tag!', 'dair tahg'),
        ('die Nacht', 'the night', 'Gute Nacht!', 'dee nakht'),
        ('die Zeit', 'the time', 'Die Zeit vergeht schnell.', 'dee tsait'),
        ('das Jahr', 'the year', 'Das Jahr geht schnell.', 'das yahr'),
        ('jetzt', 'now', 'Ich bin jetzt müde.', 'yetst'),
        ('hier', 'here', 'Ich bin hier.', 'heer'),
        ('dort', 'there', 'Das Buch liegt dort.', 'dort'),
        ('viel', 'a lot / many', 'Ich habe viel Arbeit.', 'feel'),
        ('wenig', 'a little / few', 'Ich habe wenig Zeit.', 'vay-nikh'),
    ]
    for i, (w, t, ex, pr) in enumerate(german_basics_vocab):
        c.execute("INSERT INTO vocab_items (course_id, word, translation, example_sentence, pronunciation, position) VALUES (?,?,?,?,?,?)",
                  (german_basics_id, w, t, ex, pr, i))
    c.execute("UPDATE courses SET item_count=? WHERE id=?", (len(german_basics_vocab), german_basics_id))

    # German Verbs
    c.execute(
        "INSERT INTO courses (title, description, language_id, is_official, difficulty, category, enrollment_count) VALUES (?,?,?,1,'beginner','vocabulary',6130)",
        ('German Verbs: Top 30 Action Words', 'Master the 30 most essential German verbs to start forming real sentences.', lang_de)
    )
    german_verbs_id = c.lastrowid
    german_verbs_vocab = [
        ('sein', 'to be', 'Ich bin glücklich.', 'zine'),
        ('haben', 'to have', 'Ich habe ein Buch.', 'hah-ben'),
        ('gehen', 'to go', 'Ich gehe nach Hause.', 'gay-en'),
        ('kommen', 'to come', 'Kommst du auch?', 'kom-en'),
        ('sehen', 'to see', 'Ich sehe den Hund.', 'zay-en'),
        ('hören', 'to hear', 'Ich höre Musik.', 'hö-ren'),
        ('sprechen', 'to speak', 'Er spricht Deutsch.', 'shpreshen'),
        ('sagen', 'to say', 'Was sagst du?', 'zah-gen'),
        ('machen', 'to make / do', 'Was machst du?', 'makh-en'),
        ('essen', 'to eat', 'Ich esse Pizza.', 'es-en'),
        ('trinken', 'to drink', 'Sie trinkt Kaffee.', 'trin-ken'),
        ('schlafen', 'to sleep', 'Das Baby schläft.', 'shlah-fen'),
        ('kaufen', 'to buy', 'Ich kaufe Brot.', 'kow-fen'),
        ('arbeiten', 'to work', 'Er arbeitet viel.', 'ar-bite-en'),
        ('spielen', 'to play', 'Die Kinder spielen.', 'shpee-len'),
        ('lernen', 'to learn', 'Wir lernen Deutsch.', 'lair-nen'),
        ('lesen', 'to read', 'Sie liest gern.', 'lay-zen'),
        ('schreiben', 'to write', 'Ich schreibe einen Brief.', 'shry-ben'),
        ('fahren', 'to drive / travel', 'Wir fahren nach Berlin.', 'fah-ren'),
        ('laufen', 'to run / walk', 'Er läuft jeden Tag.', 'low-fen'),
        ('wohnen', 'to live / reside', 'Ich wohne in München.', 'voh-nen'),
        ('lieben', 'to love', 'Ich liebe dich.', 'lee-ben'),
        ('kennen', 'to know (someone)', 'Kennst du ihn?', 'ken-en'),
        ('wissen', 'to know (a fact)', 'Ich weiß es nicht.', 'vis-en'),
        ('wollen', 'to want', 'Ich will Kaffee.', 'vol-en'),
        ('können', 'to be able to', 'Ich kann schwimmen.', 'kö-nen'),
        ('müssen', 'to have to / must', 'Ich muss gehen.', 'müs-en'),
        ('helfen', 'to help', 'Kannst du mir helfen?', 'hel-fen'),
        ('fragen', 'to ask', 'Darf ich fragen?', 'frah-gen'),
        ('antworten', 'to answer', 'Er antwortet nicht.', 'ant-vor-ten'),
    ]
    for i, (w, t, ex, pr) in enumerate(german_verbs_vocab):
        c.execute("INSERT INTO vocab_items (course_id, word, translation, example_sentence, pronunciation, position) VALUES (?,?,?,?,?,?)",
                  (german_verbs_id, w, t, ex, pr, i))
    c.execute("UPDATE courses SET item_count=? WHERE id=?", (len(german_verbs_vocab), german_verbs_id))

    # German Numbers
    c.execute(
        "INSERT INTO courses (title, description, language_id, is_official, difficulty, category, enrollment_count) VALUES (?,?,?,1,'beginner','vocabulary',4890)",
        ('German Numbers & Counting', 'Count from 1 to 1000 in German. Includes time expressions and useful phrases.', lang_de)
    )
    german_nums_id = c.lastrowid
    german_nums_vocab = [
        ('null', '0 (zero)', 'Die Temperatur ist null Grad.', 'nool'),
        ('eins', '1 (one)', 'Ich habe eins.', 'ines'),
        ('zwei', '2 (two)', 'Ich habe zwei Äpfel.', 'tsvai'),
        ('drei', '3 (three)', 'Drei Kinder spielen.', 'dry'),
        ('vier', '4 (four)', 'Es ist vier Uhr.', 'feer'),
        ('fünf', '5 (five)', 'Fünf Minuten bitte.', 'fünf'),
        ('sechs', '6 (six)', 'Sechs Eier, bitte.', 'zeks'),
        ('sieben', '7 (seven)', 'Sieben Tage hat eine Woche.', 'zee-ben'),
        ('acht', '8 (eight)', 'Ich arbeite acht Stunden.', 'akht'),
        ('neun', '9 (nine)', 'Neun Monate.', 'noyn'),
        ('zehn', '10 (ten)', 'Ich bin zehn Minuten zu spät.', 'tsayn'),
        ('zwanzig', '20 (twenty)', 'Sie ist zwanzig Jahre alt.', 'tsvan-tsikh'),
        ('dreißig', '30 (thirty)', 'Es sind dreißig Grad.', 'dry-sikh'),
        ('vierzig', '40 (forty)', 'Er ist vierzig Jahre alt.', 'feer-tsikh'),
        ('fünfzig', '50 (fifty)', 'Fünfzig Euro, bitte.', 'fünf-tsikh'),
        ('hundert', '100 (one hundred)', 'Hundert Prozent!', 'hun-dert'),
        ('tausend', '1000 (one thousand)', 'Ein tausend Euro.', 'tow-zend'),
        ('erste/r/s', 'first', 'Das ist mein erstes Mal.', 'air-stuh'),
        ('zweite/r/s', 'second', 'Das zweite Kind.', 'tsvai-tuh'),
        ('letzte/r/s', 'last', 'Das letzte Stück.', 'lets-tuh'),
    ]
    for i, (w, t, ex, pr) in enumerate(german_nums_vocab):
        c.execute("INSERT INTO vocab_items (course_id, word, translation, example_sentence, pronunciation, position) VALUES (?,?,?,?,?,?)",
                  (german_nums_id, w, t, ex, pr, i))
    c.execute("UPDATE courses SET item_count=? WHERE id=?", (len(german_nums_vocab), german_nums_id))

    # Lithuanian Basics
    c.execute(
        "INSERT INTO courses (title, description, language_id, is_official, difficulty, category, enrollment_count) VALUES (?,?,?,1,'beginner','vocabulary',3210)",
        ('Lithuanian Basics: Essential Words', 'Your first 35 Lithuanian words — greetings, everyday objects, and key expressions.', lang_lt)
    )
    lt_basics_id = c.lastrowid
    lt_basics_vocab = [
        ('taip', 'yes', 'Taip, tai tiesa.', 'tayp'),
        ('ne', 'no', 'Ne, ačiū.', 'neh'),
        ('labas', 'hello / hi', 'Labas! Kaip sekasi?', 'lah-bas'),
        ('ačiū', 'thank you', 'Labai ačiū!', 'ah-choo'),
        ('prašau', 'please', 'Prašau, padėk man.', 'pra-show'),
        ('atsiprašau', 'sorry / excuse me', 'Atsiprašau, aš nesuprantu.', 'at-si-pra-show'),
        ('viso gero', 'goodbye', 'Viso gero! Iki!', 'vee-so geh-ro'),
        ('iki', 'see you / bye', 'Iki pasimatymo!', 'ih-kee'),
        ('vyras', 'man', 'Tas vyras yra mokytojas.', 'vee-ras'),
        ('moteris', 'woman', 'Ta moteris dainuoja.', 'mo-teh-ris'),
        ('vaikas', 'child', 'Vaikas žaidžia parke.', 'vai-kas'),
        ('šuo', 'dog', 'Šuo loja.', 'shuo'),
        ('katė', 'cat', 'Katė miega.', 'ka-teh'),
        ('namas', 'house', 'Mūsų namas yra didelis.', 'na-mas'),
        ('automobilis', 'car', 'Automobilis yra raudonas.', 'au-to-mo-bee-lis'),
        ('knyga', 'book', 'Ši knyga yra įdomi.', 'k-nee-ga'),
        ('vanduo', 'water', 'Ar galiu gauti vandens?', 'van-duo'),
        ('maistas', 'food', 'Maistas yra skanus.', 'mais-tas'),
        ('mokykla', 'school', 'Mokykla prasideda 8 val.', 'mo-kik-la'),
        ('gatvė', 'street', 'Gatvė yra ilga.', 'gat-veh'),
        ('miestas', 'city', 'Vilnius yra gražus miestas.', 'mies-tas'),
        ('šalis', 'country', 'Lietuva yra graži šalis.', 'sha-lis'),
        ('laikas', 'time', 'Laikas bėga greitai.', 'lai-kas'),
        ('diena', 'day', 'Geros dienos!', 'dee-eh-na'),
        ('naktis', 'night', 'Labos nakties!', 'nak-tis'),
        ('savaitė', 'week', 'Šią savaitę aš dirbu.', 'sa-vai-teh'),
        ('metai', 'year', 'Šie metai yra geri.', 'meh-tai'),
        ('šiandien', 'today', 'Šiandien yra pirmadienis.', 'shian-dien'),
        ('rytoj', 'tomorrow', 'Rytoj eisiu į parduotuvę.', 'ree-toy'),
        ('vakar', 'yesterday', 'Vakar aš buvau pavargęs.', 'va-kar'),
        ('labas rytas', 'good morning', 'Labas rytas! Kaip sekasi?', 'la-bas ree-tas'),
        ('labas vakaras', 'good evening', 'Labas vakaras!', 'la-bas va-ka-ras'),
        ('labanakt', 'good night', 'Labanakt! Malonių sapnų.', 'la-ba-nakt'),
        ('kaip sekasi?', 'how are you?', 'Labas! Kaip sekasi?', 'kaip seh-ka-si'),
        ('ačiū, gerai', 'fine, thank you', 'Ačiū, gerai. O tau?', 'ah-choo geh-rai'),
    ]
    for i, (w, t, ex, pr) in enumerate(lt_basics_vocab):
        c.execute("INSERT INTO vocab_items (course_id, word, translation, example_sentence, pronunciation, position) VALUES (?,?,?,?,?,?)",
                  (lt_basics_id, w, t, ex, pr, i))
    c.execute("UPDATE courses SET item_count=? WHERE id=?", (len(lt_basics_vocab), lt_basics_id))

    # Lithuanian Colors & Numbers
    c.execute(
        "INSERT INTO courses (title, description, language_id, is_official, difficulty, category, enrollment_count) VALUES (?,?,?,1,'beginner','vocabulary',1870)",
        ('Lithuanian: Colors & Numbers', 'Learn all the basic colors and numbers 1-20 in Lithuanian.', lang_lt)
    )
    lt_colors_id = c.lastrowid
    lt_colors_vocab = [
        ('vienas', '1 (one)', 'Aš turiu vieną katiną.', 'vieh-nas'),
        ('du / dvi', '2 (two)', 'Du vyrai.', 'doo / dvee'),
        ('trys', '3 (three)', 'Trys dienos.', 'trees'),
        ('keturi', '4 (four)', 'Keturi metų laikai.', 'keh-too-ree'),
        ('penki', '5 (five)', 'Penki pirštai.', 'pen-kee'),
        ('šeši', '6 (six)', 'Šeši mėnesiai.', 'sheh-shee'),
        ('septyni', '7 (seven)', 'Savaitė turi septyni dienas.', 'sep-tee-nee'),
        ('aštuoni', '8 (eight)', 'Aštuoni žmonės.', 'ash-two-nee'),
        ('devyni', '9 (nine)', 'Devyni gyvūnai.', 'deh-vee-nee'),
        ('dešimt', '10 (ten)', 'Dešimt minučių.', 'deh-shimt'),
        ('raudona', 'red', 'Raudona rožė.', 'rau-do-na'),
        ('žalia', 'green', 'Žalia žolė.', 'zha-lya'),
        ('mėlyna', 'blue', 'Mėlynas dangus.', 'meh-lee-na'),
        ('geltona', 'yellow', 'Geltona saulė.', 'gel-to-na'),
        ('balta', 'white', 'Balta sniegas.', 'bal-ta'),
        ('juoda', 'black', 'Juodas katinas.', 'juo-da'),
        ('rožinė', 'pink', 'Rožinė suknelė.', 'ro-zhih-neh'),
        ('oranžinė', 'orange', 'Oranžinis apelsinas.', 'o-ran-zhih-neh'),
        ('violetinė', 'purple', 'Violetinė gėlė.', 'vyo-leh-tih-neh'),
        ('pilka', 'grey', 'Pilkas oras.', 'pil-ka'),
    ]
    for i, (w, t, ex, pr) in enumerate(lt_colors_vocab):
        c.execute("INSERT INTO vocab_items (course_id, word, translation, example_sentence, pronunciation, position) VALUES (?,?,?,?,?,?)",
                  (lt_colors_id, w, t, ex, pr, i))
    c.execute("UPDATE courses SET item_count=? WHERE id=?", (len(lt_colors_vocab), lt_colors_id))

    # Nepali Basics
    c.execute(
        "INSERT INTO courses (title, description, language_id, is_official, difficulty, category, enrollment_count) VALUES (?,?,?,1,'beginner','vocabulary',2940)",
        ('Nepali Basics: Essential Words', 'Start your Nepali adventure! Learn 35 essential words with both Devanagari script and romanization.', lang_ne)
    )
    ne_basics_id = c.lastrowid
    ne_basics_vocab = [
        ('हो', 'yes', 'हो, यो सहि हो।', 'ho'),
        ('होइन', 'no', 'होइन, मलाई चाहिँदैन।', 'hoina'),
        ('नमस्ते', 'hello / namaste', 'नमस्ते! कस्तो छ?', 'namaste'),
        ('धन्यवाद', 'thank you', 'तपाईंलाई धन्यवाद।', 'dhanyabad'),
        ('कृपया', 'please', 'कृपया मलाई मद्दत गर्नुहोस्।', 'kripaya'),
        ('माफ गर्नुहोस्', 'sorry / excuse me', 'माफ गर्नुहोस्, म बुझिनँ।', 'maaf garnuhos'),
        ('अलविदा', 'goodbye', 'अलविदा! फेरि भेटौँला।', 'alvida'),
        ('मान्छे', 'person / people', 'धेरै मान्छे छन्।', 'manche'),
        ('पुरुष', 'man', 'त्यो पुरुष शिक्षक हो।', 'purush'),
        ('महिला', 'woman', 'त्यो महिला गाउँछिन्।', 'mahila'),
        ('बच्चा', 'child', 'बच्चा पार्कमा खेल्छ।', 'bachcha'),
        ('कुकुर', 'dog', 'कुकुर भुक्छ।', 'kukur'),
        ('बिरालो', 'cat', 'बिरालो सुत्छ।', 'biralo'),
        ('घर', 'house', 'हाम्रो घर ठूलो छ।', 'ghar'),
        ('गाडी', 'car', 'गाडी रातो छ।', 'gadi'),
        ('किताब', 'book', 'यो किताब रोचक छ।', 'kitab'),
        ('पानी', 'water', 'मलाई पानी चाहियो।', 'pani'),
        ('खाना', 'food', 'खाना मिठो छ।', 'khana'),
        ('विद्यालय', 'school', 'विद्यालय ८ बजे सुरु हुन्छ।', 'vidyalay'),
        ('सडक', 'road / street', 'सडक लामो छ।', 'sadak'),
        ('शहर', 'city', 'काठमाण्डौ सुन्दर शहर हो।', 'shahar'),
        ('देश', 'country', 'नेपाल सुन्दर देश हो।', 'desh'),
        ('समय', 'time', 'समय छिट्टै बित्छ।', 'samay'),
        ('दिन', 'day', 'शुभ दिन!', 'din'),
        ('रात', 'night', 'शुभ रात्री!', 'raat'),
        ('हप्ता', 'week', 'यो हप्ता म व्यस्त छु।', 'hapta'),
        ('वर्ष', 'year', 'यो वर्ष राम्रो छ।', 'barsha'),
        ('आज', 'today', 'आज सोमबार हो।', 'aaj'),
        ('भोलि', 'tomorrow', 'भोलि बजार जान्छु।', 'bholi'),
        ('हिजो', 'yesterday', 'हिजो म थाकेको थिएँ।', 'hijo'),
        ('शुभ प्रभात', 'good morning', 'शुभ प्रभात! कस्तो छ?', 'shubha prabhaat'),
        ('शुभ साँझ', 'good evening', 'शुभ साँझ!', 'shubha saajh'),
        ('शुभ रात्री', 'good night', 'शुभ रात्री! राम्रो सपना।', 'shubha raatri'),
        ('कस्तो छ?', 'how are you?', 'नमस्ते! कस्तो छ?', 'kasto cha'),
        ('ठीक छ, धन्यवाद', 'fine, thank you', 'ठीक छ, धन्यवाद। तपाईंलाई?', 'thik cha, dhanyabad'),
    ]
    for i, (w, t, ex, pr) in enumerate(ne_basics_vocab):
        c.execute("INSERT INTO vocab_items (course_id, word, translation, example_sentence, pronunciation, position) VALUES (?,?,?,?,?,?)",
                  (ne_basics_id, w, t, ex, pr, i))
    c.execute("UPDATE courses SET item_count=? WHERE id=?", (len(ne_basics_vocab), ne_basics_id))

    # Nepali Travel
    c.execute(
        "INSERT INTO courses (title, description, language_id, is_official, difficulty, category, enrollment_count) VALUES (?,?,?,1,'beginner','conversation',2010)",
        ('Nepali Travel Phrases', 'Essential phrases for travelling in Nepal — hotels, directions, shopping, and emergencies.', lang_ne)
    )
    ne_travel_id = c.lastrowid
    ne_travel_vocab = [
        ('होटल कहाँ छ?', 'Where is the hotel?', 'माफ गर्नुहोस्, होटल कहाँ छ?', 'hotel kahaan cha'),
        ('कति पर्छ?', 'How much does it cost?', 'यो कति पर्छ?', 'kati parcha'),
        ('मलाई चाहियो', 'I need / I want', 'मलाई पानी चाहियो।', 'malai chaahiyo'),
        ('मद्दत गर्नुहोस्!', 'Help!', 'मद्दत गर्नुहोस्! आपतकाल!', 'maddat garnuhos'),
        ('अस्पताल', 'hospital', 'नजिकको अस्पताल कहाँ छ?', 'aspatal'),
        ('बस स्टप', 'bus stop', 'बस स्टप कहाँ छ?', 'bas stop'),
        ('बायाँ', 'left', 'बायाँ मोड्नुहोस्।', 'baayaa'),
        ('दायाँ', 'right', 'दायाँ मोड्नुहोस्।', 'daayaa'),
        ('सोझो', 'straight ahead', 'सोझो जानुहोस्।', 'sojho'),
        ('खाना खानुहोस्', 'please eat', 'खाना खानुहोस्, तयार छ।', 'khana khaanuhos'),
        ('पानी दिनुहोस्', 'please give water', 'एक गिलास पानी दिनुहोस्।', 'paani dinuhos'),
        ('बिल ल्याउनुहोस्', 'bring the bill', 'बिल ल्याउनुहोस् कृपया।', 'bil lyaaunuhos'),
        ('राम्रो', 'good / nice', 'यो खाना राम्रो छ।', 'ramro'),
        ('ठूलो', 'big / large', 'ठूलो कोठा चाहियो।', 'thulo'),
        ('सानो', 'small', 'एउटा सानो कोठा छ?', 'saano'),
        ('महँगो', 'expensive', 'यो धेरै महँगो छ।', 'mahango'),
        ('सस्तो', 'cheap', 'केही सस्तो छ?', 'sasto'),
        ('बजार', 'market', 'बजार कहाँ छ?', 'bajaar'),
        ('म नेपाली बुझ्दिनँ', 'I don\'t understand Nepali', 'माफ गर्नुहोस्, म नेपाली बुझ्दिनँ।', 'ma nepali bujhdinaa'),
        ('अंग्रेजी बोल्नुहुन्छ?', 'Do you speak English?', 'तपाईं अंग्रेजी बोल्नुहुन्छ?', 'angreji bolnuhuncha'),
    ]
    for i, (w, t, ex, pr) in enumerate(ne_travel_vocab):
        c.execute("INSERT INTO vocab_items (course_id, word, translation, example_sentence, pronunciation, position) VALUES (?,?,?,?,?,?)",
                  (ne_travel_id, w, t, ex, pr, i))
    c.execute("UPDATE courses SET item_count=? WHERE id=?", (len(ne_travel_vocab), ne_travel_id))

    # ── COMMUNITY COURSES ─────────────────────────────────────────────────────

    def get_mock_user(username):
        return c.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()['id']

    # German Food (FoodieInBerlin)
    c.execute(
        "INSERT INTO courses (title, description, language_id, creator_id, is_official, difficulty, category, enrollment_count) VALUES (?,?,?,?,0,'beginner','vocabulary',3860)",
        ('German Food & Drinks', 'Hungry in Germany? This vocab list covers everything you need to order food, understand menus, and talk about your favourite meals.', lang_de, get_mock_user('FoodieInBerlin'))
    )
    de_food_id = c.lastrowid
    de_food_vocab = [
        ('das Brot', 'bread', 'Ich esse Brot zum Frühstück.', 'das broht'),
        ('die Butter', 'butter', 'Butter aufs Brot.', 'dee boo-ter'),
        ('der Käse', 'cheese', 'Schweizer Käse ist lecker.', 'dair kay-zuh'),
        ('die Wurst', 'sausage', 'Bratwurst ist typisch deutsch.', 'dee voorst'),
        ('das Fleisch', 'meat', 'Ich esse kein Fleisch.', 'das flysh'),
        ('der Fisch', 'fish', 'Fisch ist gesund.', 'dair fish'),
        ('das Gemüse', 'vegetables', 'Ich esse viel Gemüse.', 'das guh-mü-zuh'),
        ('das Obst', 'fruit', 'Obst ist gesund.', 'das opst'),
        ('der Apfel', 'apple', 'Ein Apfel am Tag...', 'dair ap-fel'),
        ('die Kartoffel', 'potato', 'Kartoffeln sind typisch.', 'dee kar-tof-el'),
        ('die Milch', 'milk', 'Kaffee mit Milch, bitte.', 'dee milkh'),
        ('der Kaffee', 'coffee', 'Ich trinke morgens Kaffee.', 'dair kaf-ay'),
        ('der Tee', 'tea', 'Kamillentee schmeckt gut.', 'dair tay'),
        ('das Bier', 'beer', 'Ein Bier, bitte!', 'das beer'),
        ('der Wein', 'wine', 'Rotwein oder Weißwein?', 'dair vine'),
        ('der Saft', 'juice', 'Apfelsaft, bitte.', 'dair zaft'),
        ('das Eis', 'ice cream', 'Ich hätte gern ein Eis.', 'das ice'),
        ('der Kuchen', 'cake', 'Der Kuchen ist lecker.', 'dair kookhen'),
        ('das Frühstück', 'breakfast', 'Frühstück ist wichtig.', 'das frü-shtük'),
        ('das Mittagessen', 'lunch', 'Was gibt es zum Mittagessen?', 'das mit-tahg-es-en'),
        ('das Abendessen', 'dinner / supper', 'Abendessen um sieben Uhr.', 'das ah-bent-es-en'),
        ('lecker', 'delicious / tasty', 'Das schmeckt lecker!', 'lek-er'),
        ('scharf', 'spicy / hot', 'Das ist zu scharf für mich.', 'sharp'),
        ('süß', 'sweet', 'Der Kuchen ist sehr süß.', 'züss'),
        ('sauer', 'sour', 'Die Zitrone ist sauer.', 'zow-er'),
    ]
    for i, (w, t, ex, pr) in enumerate(de_food_vocab):
        c.execute("INSERT INTO vocab_items (course_id, word, translation, example_sentence, pronunciation, position) VALUES (?,?,?,?,?,?)",
                  (de_food_id, w, t, ex, pr, i))
    c.execute("UPDATE courses SET item_count=? WHERE id=?", (len(de_food_vocab), de_food_id))

    # German Business (CorpLinguist)
    c.execute(
        "INSERT INTO courses (title, description, language_id, creator_id, is_official, difficulty, category, enrollment_count) VALUES (?,?,?,?,0,'intermediate','conversation',2760)",
        ('German Business Phrases', 'Nail your German business meetings, emails, and presentations with these essential professional phrases.', lang_de, get_mock_user('CorpLinguist'))
    )
    de_biz_id = c.lastrowid
    de_biz_vocab = [
        ('die Besprechung', 'meeting', 'Die Besprechung beginnt um 9.', 'dee buh-shprech-ung'),
        ('der Termin', 'appointment', 'Ich habe einen Termin.', 'dair ter-meen'),
        ('die Präsentation', 'presentation', 'Ihre Präsentation war toll.', 'dee prä-zen-tat-syon'),
        ('der Bericht', 'report', 'Der Bericht ist fertig.', 'dair buh-rikht'),
        ('die Rechnung', 'invoice', 'Bitte senden Sie die Rechnung.', 'dee rekh-nung'),
        ('der Vertrag', 'contract', 'Haben Sie den Vertrag gelesen?', 'dair fer-trahg'),
        ('die Frist', 'deadline', 'Die Frist ist Freitag.', 'dee frist'),
        ('das Budget', 'budget', 'Das Budget ist zu niedrig.', 'das bü-djay'),
        ('der Umsatz', 'revenue / turnover', 'Der Umsatz stieg um 10%.', 'dair oom-zats'),
        ('die Strategie', 'strategy', 'Was ist unsere Strategie?', 'dee stra-teh-gee'),
        ('Mit freundlichen Grüßen', 'Kind regards (email closing)', 'Mit freundlichen Grüßen, Max.', 'mit froyn-li-khen grü-sen'),
        ('Sehr geehrte Damen und Herren', 'Dear Sir or Madam', 'Sehr geehrte Damen und Herren...', 'zair guh-ert-uh'),
        ('Könnten Sie bitte...?', 'Could you please...?', 'Könnten Sie bitte anrufen?', 'kö-nen zee bit-uh'),
        ('Ich freue mich auf', 'I look forward to', 'Ich freue mich auf unsere Zusammenarbeit.', 'ikh froi-uh mikh owf'),
        ('die Zusammenarbeit', 'collaboration', 'Die Zusammenarbeit war großartig.', 'dee tsoo-za-men-ar-bite'),
    ]
    for i, (w, t, ex, pr) in enumerate(de_biz_vocab):
        c.execute("INSERT INTO vocab_items (course_id, word, translation, example_sentence, pronunciation, position) VALUES (?,?,?,?,?,?)",
                  (de_biz_id, w, t, ex, pr, i))
    c.execute("UPDATE courses SET item_count=? WHERE id=?", (len(de_biz_vocab), de_biz_id))

    # 1000 Most Common German Words (GermanNerd2024) - first 20 as sample
    c.execute(
        "INSERT INTO courses (title, description, language_id, creator_id, is_official, difficulty, category, enrollment_count) VALUES (?,?,?,?,0,'intermediate','vocabulary',7340)",
        ('1000 Most Common German Words', 'The ultimate German vocabulary list. Research shows that knowing the top 1000 words covers 85% of everyday speech. This is a must-have course!', lang_de, get_mock_user('GermanNerd2024'))
    )
    de_1000_id = c.lastrowid
    de_1000_vocab = [
        ('der', 'the (masculine)', '', ''),
        ('die', 'the (feminine/plural)', '', ''),
        ('das', 'the (neuter)', '', ''),
        ('und', 'and', 'Du und ich.', 'oont'),
        ('in', 'in', 'Ich wohne in Berlin.', 'in'),
        ('von', 'of / from', 'Das Buch von Goethe.', 'fon'),
        ('mit', 'with', 'Mit dir.', 'mit'),
        ('an', 'at / on', 'Ich bin an der Schule.', 'an'),
        ('auf', 'on / onto', 'Auf dem Tisch.', 'owf'),
        ('für', 'for', 'Das ist für dich.', 'für'),
        ('nicht', 'not', 'Das ist nicht wahr.', 'nikht'),
        ('aber', 'but', 'Schön, aber teuer.', 'ah-ber'),
        ('oder', 'or', 'Tee oder Kaffee?', 'oh-der'),
        ('wenn', 'if / when', 'Wenn es regnet...', 'ven'),
        ('noch', 'still / yet / more', 'Noch ein Bier?', 'nokh'),
        ('als', 'as / than / when', 'Größer als ich.', 'als'),
        ('nur', 'only / just', 'Nur ein bisschen.', 'noor'),
        ('auch', 'also / too', 'Ich auch!', 'owkh'),
        ('schon', 'already', 'Ich bin schon fertig.', 'shone'),
        ('so', 'so / such', 'So ein schöner Tag!', 'zo'),
    ]
    for i, (w, t, ex, pr) in enumerate(de_1000_vocab):
        c.execute("INSERT INTO vocab_items (course_id, word, translation, example_sentence, pronunciation, position) VALUES (?,?,?,?,?,?)",
                  (de_1000_id, w, t, ex, pr, i))
    c.execute("UPDATE courses SET item_count=? WHERE id=?", (len(de_1000_vocab), de_1000_id))

    # Nepali for Trekkers (HimalayanHiker)
    c.execute(
        "INSERT INTO courses (title, description, language_id, creator_id, is_official, difficulty, category, enrollment_count) VALUES (?,?,?,?,0,'beginner','conversation',3480)",
        ('Nepali for Trekkers', 'Heading to the Himalayas? Learn the essential Nepali vocabulary for trekking — trails, altitude, lodges, and making friends along the way!', lang_ne, get_mock_user('HimalayanHiker'))
    )
    ne_trek_id = c.lastrowid
    ne_trek_vocab = [
        ('हिमाल', 'mountain / Himalaya', 'हिमाल धेरै अग्लो छ।', 'himal'),
        ('बाटो', 'path / trail', 'बाटो कहाँ छ?', 'baato'),
        ('लज', 'lodge (teahouse)', 'नजिकको लज कहाँ छ?', 'laj'),
        ('दाल भात', 'lentil soup and rice (staple meal)', 'दाल भात खानु छ?', 'daal bhaat'),
        ('थकाइ लाग्यो', 'I am tired', 'धेरै हिँडेँ, थकाइ लाग्यो।', 'thakai lagyo'),
        ('चिसो', 'cold', 'आज धेरै चिसो छ।', 'chiso'),
        ('तातो', 'hot / warm', 'चिया तातो छ।', 'taato'),
        ('पानी', 'water', 'पिउने पानी छ?', 'pani'),
        ('ब्यागपाक', 'backpack', 'मेरो ब्यागपाक गह्रुँगो छ।', 'byaagpak'),
        ('जुत्ता', 'shoes / boots', 'ट्रेकिङ जुत्ता चाहियो।', 'juttaa'),
        ('मौसम', 'weather', 'भोलि मौसम कस्तो हुन्छ?', 'mausam'),
        ('हावा', 'wind', 'धेरै हावा छ।', 'haawaa'),
        ('हिउँ', 'snow', 'माथि हिउँ छ।', 'hiuu'),
        ('ढोका', 'door', 'ढोका बन्द गर्नुहोस्।', 'dhoka'),
        ('बेड', 'bed', 'एउटा बेड चाहियो।', 'bed'),
        ('टर्च', 'torch / flashlight', 'टर्च छ तपाईंसँग?', 'torch'),
        ('म बिरामी छु', 'I am sick', 'मलाई माफ गर्नुहोस्, म बिरामी छु।', 'ma biraami chu'),
        ('डाक्टर', 'doctor', 'डाक्टर कहाँ छ?', 'daaktar'),
        ('उकालो', 'uphill', 'अझै धेरै उकालो छ?', 'ukaalo'),
        ('ओरालो', 'downhill', 'ओरालो सजिलो छ।', 'oraalo'),
    ]
    for i, (w, t, ex, pr) in enumerate(ne_trek_vocab):
        c.execute("INSERT INTO vocab_items (course_id, word, translation, example_sentence, pronunciation, position) VALUES (?,?,?,?,?,?)",
                  (ne_trek_id, w, t, ex, pr, i))
    c.execute("UPDATE courses SET item_count=? WHERE id=?", (len(ne_trek_vocab), ne_trek_id))

    # Basic Nepali Greetings (KathmanduKid)
    c.execute(
        "INSERT INTO courses (title, description, language_id, creator_id, is_official, difficulty, category, enrollment_count) VALUES (?,?,?,?,0,'beginner','conversation',2190)",
        ('Basic Nepali Greetings & Small Talk', 'Make Nepali friends instantly! This short course covers all the greetings and small talk phrases you need to break the ice.', lang_ne, get_mock_user('KathmanduKid'))
    )
    ne_greet_id = c.lastrowid
    ne_greet_vocab = [
        ('नमस्ते / नमस्कार', 'hello / greetings', 'नमस्ते! तपाईं कस्तो हुनुहुन्छ?', 'namaste / namaskar'),
        ('तपाईंको नाम के हो?', 'What is your name?', 'तपाईंको नाम के हो, कृपया?', 'tapaaiko naam ke ho'),
        ('मेरो नाम ... हो', 'My name is ...', 'मेरो नाम Sarah हो।', 'mero naam ... ho'),
        ('तपाईं कहाँबाट हुनुहुन्छ?', 'Where are you from?', 'तपाईं कहाँबाट हुनुहुन्छ?', 'tapaai kahaabata hunuhuncha'),
        ('म ... बाट हुँ', 'I am from ...', 'म बेलायत बाट हुँ।', 'ma ... bata hu'),
        ('खुसी लाग्यो भेटेर', 'Nice to meet you', 'खुसी लाग्यो भेटेर!', 'khushi lagyo bheter'),
        ('कति वर्ष हुनुभयो?', 'How old are you?', 'तपाईंलाई कति वर्ष हुनुभयो?', 'kati barsha hunubhayo'),
        ('मलाई ... वर्ष भयो', 'I am ... years old', 'मलाई तीस वर्ष भयो।', 'malai ... barsha bhayo'),
        ('तपाईं के काम गर्नुहुन्छ?', 'What do you do?', 'तपाईं के काम गर्नुहुन्छ?', 'tapaai ke kaam garnuhuncha'),
        ('म विद्यार्थी हुँ', 'I am a student', 'म विद्यार्थी हुँ।', 'ma vidyarthi hu'),
        ('साथी', 'friend', 'तिमी मेरो राम्रो साथी हौ।', 'saathi'),
        ('फेरि भेटौँला', 'See you again', 'ठीक छ, फेरि भेटौँला!', 'pheri bhetaula'),
    ]
    for i, (w, t, ex, pr) in enumerate(ne_greet_vocab):
        c.execute("INSERT INTO vocab_items (course_id, word, translation, example_sentence, pronunciation, position) VALUES (?,?,?,?,?,?)",
                  (ne_greet_id, w, t, ex, pr, i))
    c.execute("UPDATE courses SET item_count=? WHERE id=?", (len(ne_greet_vocab), ne_greet_id))

    # Lithuanian Folk Songs (BalticSinger)
    c.execute(
        "INSERT INTO courses (title, description, language_id, creator_id, is_official, difficulty, category, enrollment_count) VALUES (?,?,?,?,0,'intermediate','vocabulary',520)",
        ('Lithuanian Folk Song Vocabulary', 'Explore the rich tradition of Lithuanian folk songs (dainos). Learn the poetic vocabulary that appears in these beautiful songs.', lang_lt, get_mock_user('BalticSinger'))
    )
    lt_folk_id = c.lastrowid
    lt_folk_vocab = [
        ('daina', 'song / folk song', 'Lietuvių dainos yra gražios.', 'dai-na'),
        ('dainuoti', 'to sing', 'Ji moka dainuoti.', 'dai-nuo-ti'),
        ('upė', 'river', 'Upė teka per mišką.', 'oo-peh'),
        ('miškas', 'forest', 'Miškas yra gilus.', 'mish-kas'),
        ('laukas', 'field', 'Rugių laukas žydi.', 'lau-kas'),
        ('saulė', 'sun', 'Saulė šviečia.', 'sau-leh'),
        ('mėnulis', 'moon', 'Mėnulis šviečia naktį.', 'meh-noo-lis'),
        ('žvaigždė', 'star', 'Žvaigždės žiba naktį.', 'zhvaig-zdeh'),
        ('gėlė', 'flower', 'Gėlė kvepėjo.', 'geh-leh'),
        ('bernelis', 'young man (poetic)', 'Bernelis joja per lauką.', 'ber-neh-lis'),
        ('mergelė', 'young woman (poetic)', 'Mergelė dainuoja.', 'mer-geh-leh'),
        ('Lietuva', 'Lithuania', 'Lietuva, tėvyne mūsų.', 'lee-eh-too-va'),
        ('tėvynė', 'homeland / fatherland', 'Myliu savo tėvynę.', 'teh-vee-neh'),
        ('laisvė', 'freedom', 'Laisvė yra brangiausia.', 'lais-veh'),
        ('meilė', 'love', 'Meilė stipresnė už viską.', 'mei-leh'),
    ]
    for i, (w, t, ex, pr) in enumerate(lt_folk_vocab):
        c.execute("INSERT INTO vocab_items (course_id, word, translation, example_sentence, pronunciation, position) VALUES (?,?,?,?,?,?)",
                  (lt_folk_id, w, t, ex, pr, i))
    c.execute("UPDATE courses SET item_count=? WHERE id=?", (len(lt_folk_vocab), lt_folk_id))

    # German Slang (BerlinStreetKid)
    c.execute(
        "INSERT INTO courses (title, description, language_id, creator_id, is_official, difficulty, category, enrollment_count) VALUES (?,?,?,?,0,'intermediate','vocabulary',4230)",
        ('German Slang & Street Language', 'Sound like a local! This course covers Berlin street slang, youth language, and casual expressions you won\'t find in textbooks.', lang_de, get_mock_user('BerlinStreetKid'))
    )
    de_slang_id = c.lastrowid
    de_slang_vocab = [
        ('krass', 'crazy / intense / cool', 'Das ist total krass!', 'krass'),
        ('geil', 'awesome / brilliant', 'Das Konzert war so geil!', 'gile'),
        ('chillen', 'to chill / relax', 'Wir chillen heute Abend.', 'chil-en'),
        ('digga', 'dude / mate (Berlin slang)', 'Ey digga, was geht?', 'dig-a'),
        ('Alter!', 'Man! / Wow! (exclamation)', 'Alter, hast du das gesehen?!', 'al-ter'),
        ('Bock haben', 'to feel like doing something', 'Ich hab Bock auf Pizza.', 'bok hah-ben'),
        ('kein Bock', 'not in the mood / can\'t be bothered', 'Ich hab heute kein Bock.', 'kine bok'),
        ('Kiez', 'neighbourhood (Berlin)', 'Ich kenn meinen Kiez gut.', 'keets'),
        ('lässig', 'cool / laid-back', 'Der Typ ist echt lässig.', 'les-ikh'),
        ('voll', 'totally / really (intensifier)', 'Das ist voll gut!', 'fol'),
        ('auf jeden Fall', 'definitely / for sure', 'Auf jeden Fall komme ich!', 'owf yay-den fal'),
        ('Quatsch!', 'Nonsense! / Rubbish!', 'Das ist doch totaler Quatsch!', 'kvatsh'),
        ('Mist!', 'Damn! / Shoot!', 'Mist, ich hab den Bus verpasst!', 'mist'),
        ('abchecken', 'to check out / scope out', 'Lass uns den Laden abchecken.', 'ap-chek-en'),
        ('echt', 'really / genuinely', 'Das meinst du echt?', 'ekht'),
    ]
    for i, (w, t, ex, pr) in enumerate(de_slang_vocab):
        c.execute("INSERT INTO vocab_items (course_id, word, translation, example_sentence, pronunciation, position) VALUES (?,?,?,?,?,?)",
                  (de_slang_id, w, t, ex, pr, i))
    c.execute("UPDATE courses SET item_count=? WHERE id=?", (len(de_slang_vocab), de_slang_id))

    # Lithuanian Phrases for Travelers (BalticExplorer)
    c.execute(
        "INSERT INTO courses (title, description, language_id, creator_id, is_official, difficulty, category, enrollment_count) VALUES (?,?,?,?,0,'beginner','conversation',1640)",
        ('Lithuanian Phrases for Travelers', 'Visiting Vilnius or the Baltic coast? These phrases will help you navigate Lithuania like a pro!', lang_lt, get_mock_user('BalticExplorer'))
    )
    lt_travel_id = c.lastrowid
    lt_travel_vocab = [
        ('Kur yra...?', 'Where is...?', 'Kur yra viešbutis?', 'koor ee-ra'),
        ('Kiek kainuoja?', 'How much does it cost?', 'Kiek kainuoja šis bilietas?', 'kiek kai-nuo-ya'),
        ('Man reikia...', 'I need...', 'Man reikia pagalbos.', 'man rei-kya'),
        ('Pagalba!', 'Help!', 'Pagalba! Greitoji!', 'pa-gal-ba'),
        ('viešbutis', 'hotel', 'Geras viešbutis mieste.', 'viesh-boo-tis'),
        ('restoranas', 'restaurant', 'Ar žinote gerą restoraną?', 'res-to-ra-nas'),
        ('stotelė', 'bus stop', 'Kur yra autobusų stotelė?', 'sto-teh-leh'),
        ('kairė', 'left', 'Pasukite į kairę.', 'kai-reh'),
        ('dešinė', 'right', 'Pasukite į dešinę.', 'deh-shi-neh'),
        ('tiesiai', 'straight ahead', 'Eikite tiesiai.', 'tee-eh-siai'),
        ('Aš nesuprantu', 'I don\'t understand', 'Atsiprašau, aš nesuprantu.', 'ash nes-oo-pran-too'),
        ('Kalbate angliškai?', 'Do you speak English?', 'Atsiprašau, kalbate angliškai?', 'kal-ba-teh ang-lish-kai'),
        ('sąskaita', 'bill / invoice', 'Prašau sąskaitą.', 'sask-ai-ta'),
        ('tualetas', 'toilet / bathroom', 'Kur yra tualetas?', 'tua-leh-tas'),
        ('vaistinė', 'pharmacy', 'Kur yra vaistinė?', 'vais-ti-neh'),
    ]
    for i, (w, t, ex, pr) in enumerate(lt_travel_vocab):
        c.execute("INSERT INTO vocab_items (course_id, word, translation, example_sentence, pronunciation, position) VALUES (?,?,?,?,?,?)",
                  (lt_travel_id, w, t, ex, pr, i))
    c.execute("UPDATE courses SET item_count=? WHERE id=?", (len(lt_travel_vocab), lt_travel_id))

    # ── SEED RATINGS ─────────────────────────────────────────────────────────
    # Fake ratings from mock users to populate avg ratings
    rating_data = [
        # (course_id, user_id_username, rating)
        (de_food_id, 'GermanNerd2024', 5),
        (de_food_id, 'BerlinStreetKid', 5),
        (de_food_id, 'CorpLinguist', 4),
        (de_food_id, 'LanguageLover', 5),
        (de_biz_id, 'GermanNerd2024', 4),
        (de_biz_id, 'FoodieInBerlin', 5),
        (de_biz_id, 'LanguageLover', 4),
        (de_1000_id, 'FoodieInBerlin', 5),
        (de_1000_id, 'BerlinStreetKid', 5),
        (de_1000_id, 'CorpLinguist', 5),
        (de_1000_id, 'BalticExplorer', 4),
        (ne_trek_id, 'KathmanduKid', 5),
        (ne_trek_id, 'LanguageLover', 5),
        (ne_trek_id, 'GermanNerd2024', 4),
        (ne_greet_id, 'HimalayanHiker', 5),
        (ne_greet_id, 'LanguageLover', 5),
        (ne_greet_id, 'BalticExplorer', 4),
        (lt_folk_id, 'BalticExplorer', 4),
        (lt_folk_id, 'LanguageLover', 5),
        (de_slang_id, 'FoodieInBerlin', 5),
        (de_slang_id, 'GermanNerd2024', 4),
        (de_slang_id, 'LanguageLover', 5),
        (de_slang_id, 'KathmanduKid', 5),
        (lt_travel_id, 'BalticSinger', 5),
        (lt_travel_id, 'LanguageLover', 4),
    ]
    for (cid, uname, r) in rating_data:
        uid = get_mock_user(uname)
        c.execute(
            "INSERT OR IGNORE INTO course_ratings (course_id, user_id, rating) VALUES (?,?,?)",
            (cid, uid, r)
        )

    conn.commit()
    conn.close()
    print("Database seeded successfully.")


def get_course_avg_rating(conn, course_id):
    row = conn.execute(
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
