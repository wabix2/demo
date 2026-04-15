from flask import Flask, request, jsonify, render_template_string, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import json
import random
import os
import datetime
import difflib
import sqlite3
import traceback
import sys
from deep_translator import GoogleTranslator

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# ---------------- Logging ----------------
import logging
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
logger = logging.getLogger(__name__)

# ---------------- Flask-Login Setup ----------------
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "Please log in to access AfriVoice AI."

# ---------------- Database Setup (using /tmp for Render) ----------------
DATABASE = os.environ.get('DATABASE_PATH', '/tmp/afrivoice.db')

def init_db():
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        response TEXT NOT NULL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_memory (
        user_id INTEGER PRIMARY KEY,
        last_topic TEXT,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )''')
    conn.commit()
    conn.close()
    logger.info("Database initialized at %s", DATABASE)

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# ---------------- User Class ----------------
class User(UserMixin):
    def __init__(self, id, username, email, full_name):
        self.id = id
        self.username = username
        self.email = email
        self.full_name = full_name

@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, username, email, full_name FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return User(row['id'], row['username'], row['email'], row['full_name'])
    return None

# ---------------- Load Knowledge Base ----------------
def load_json(file):
    if os.path.exists(file):
        with open(file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def flatten_knowledge_base(raw):
    flat = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, list):
                for sub_item in item:
                    if isinstance(sub_item, dict):
                        flat.update(sub_item)
            elif isinstance(item, dict):
                flat.update(item)
    elif isinstance(raw, dict):
        flat = raw
    return flat

try:
    kb_raw = load_json("knowledge_base.json")
    kb = flatten_knowledge_base(kb_raw)
    logger.info("Knowledge base loaded with %d topics", len(kb))
except Exception as e:
    logger.error("Failed to load knowledge base: %s", e)
    kb = {}

# ---------------- Language Detection ----------------
def detect_lang(text):
    for ch in text:
        if '\u1200' <= ch <= '\u137F':
            return "am"
    oromo_indicators = ["akkam", "naga", "fayya", "galatooma", "barbaada", "jira", "qaba",
                        "hojii", "qonna", "biyya", "lafa", "bishaan", "nyaata", "beekta",
                        "danda'a", "facaafanna", "gabbifanna", "sassaabna", "qoranna", "ittifanna",
                        "oomishtummaa", "soorata", "dhukkuba", "qorichaa"]
    text_lower = text.lower()
    for word in oromo_indicators:
        if word in text_lower:
            return "om"
    return "en"

# ---------------- Translation ----------------
def translate_to_english(text):
    try:
        src = 'am' if detect_lang(text) == 'am' else 'om' if detect_lang(text) == 'om' else 'auto'
        translated = GoogleTranslator(source=src, target='en').translate(text)
        return translated, src if src != 'auto' else 'en'
    except Exception as e:
        logger.warning("Translation to English failed: %s", e)
        return text, 'en'

def translate_from_english(text, target_lang):
    if target_lang == 'en' or target_lang == 'auto':
        return text
    try:
        return GoogleTranslator(source='en', target=target_lang).translate(text)
    except Exception as e:
        logger.warning("Translation from English failed: %s", e)
        return text

# ---------------- Smart Topic Matching ----------------
def find_best_topic(query):
    query_lower = query.lower()
    query_words = set(query_lower.split())
    best_score = 0
    best_data = None
    for topic, data in kb.items():
        keywords = set(data.get("keywords", []))
        topic_words = set(topic.lower().split())
        all_keywords = keywords | topic_words
        matches = 0
        for qw in query_words:
            for kw in all_keywords:
                if qw == kw.lower() or difflib.SequenceMatcher(None, qw, kw.lower()).ratio() >= 0.6:
                    matches += 1
                    break
        if matches > best_score:
            best_score = matches
            best_data = data
    return best_data, best_score

# ---------------- Context Memory ----------------
def get_user_context(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT last_topic FROM user_memory WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row['last_topic'] if row else None

def set_user_context(user_id, topic):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO user_memory (user_id, last_topic) VALUES (?, ?)", (user_id, topic))
    conn.commit()
    conn.close()

# ---------------- Save Conversation ----------------
def save_conversation(user_id, message, response):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT INTO conversations (user_id, message, response) VALUES (?, ?, ?)",
                  (user_id, message, response))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("Failed to save conversation: %s", e)

def get_recent_conversations(user_id, limit=10):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT message, response, timestamp FROM conversations WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
              (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# ---------------- Response Generation ----------------
def generate_response(query, user_id=None):
    try:
        english_query, original_lang = translate_to_english(query)
    except:
        english_query, original_lang = query, 'en'
    
    follow_up_phrases = ["more", "tell me more", "what else", "another", "any other", "ሌላ", "ተጨማሪ", "kan biraa"]
    is_follow_up = any(phrase in query.lower() for phrase in follow_up_phrases)
    
    if is_follow_up and user_id:
        last_topic = get_user_context(user_id)
        if last_topic and last_topic in kb:
            data = kb[last_topic]
            follow_ups = data.get("follow_ups", {}).get("en", [])
            if follow_ups:
                response = random.choice(follow_ups)
                set_user_context(user_id, last_topic)
                return translate_from_english(response, original_lang)
    
    data, score = find_best_topic(english_query)
    
    if data and score > 0:
        if user_id:
            for name, d in kb.items():
                if d == data:
                    set_user_context(user_id, name)
                    break
        
        answers = data.get("answers", {})
        if original_lang in answers:
            response = random.choice(answers[original_lang])
        else:
            eng_answer = random.choice(answers.get("en", ["I understand but lack language support."]))
            response = translate_from_english(eng_answer, original_lang)
    else:
        if original_lang == "am":
            response = "ይቅርታ፣ ስለዚህ ጥያቄ መረጃ የለኝም።"
        elif original_lang == "om":
            response = "Dhiifama, waa'ee gaaffii kanaa odeeffannoo hin qabnu."
        else:
            response = "I don't have information on that yet."
    return response

# ---------------- Routes ----------------
@app.route('/')
def index():
    if current_user.is_authenticated:
        return render_template_string(MAIN_PAGE_HTML)
    return redirect(url_for('login'))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        full_name = request.form.get('full_name')
        
        conn = get_db()
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (username, email, password_hash, full_name) VALUES (?, ?, ?, ?)",
                      (username, email, generate_password_hash(password), full_name))
            conn.commit()
            user_id = c.lastrowid
            c.execute("INSERT INTO user_memory (user_id, last_topic) VALUES (?, ?)", (user_id, None))
            conn.commit()
            conn.close()
            flash('Account created successfully! Please log in.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Username or email already exists.', 'danger')
        except Exception as e:
            logger.error("Signup error: %s", e)
            flash('An error occurred. Please try again.', 'danger')
        conn.close()
    return render_template_string(SIGNUP_PAGE_HTML)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id, username, email, full_name, password_hash FROM users WHERE username = ? OR email = ?",
                  (username, username))
        row = c.fetchone()
        conn.close()
        if row and check_password_hash(row['password_hash'], password):
            user = User(row['id'], row['username'], row['email'], row['full_name'])
            login_user(user, remember=True)
            return redirect(url_for('index'))
        flash('Invalid username or password.', 'danger')
    return render_template_string(LOGIN_PAGE_HTML)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/chat', methods=['POST'])
@login_required
def chat():
    user_msg = request.json.get("message", "")
    response = generate_response(user_msg, current_user.id)
    save_conversation(current_user.id, user_msg, response)
    return jsonify({"response": response})

@app.route('/history')
@login_required
def history():
    conversations = get_recent_conversations(current_user.id, 20)
    return jsonify(conversations)

# ---------------- UI Templates (same as before, omitted for brevity) ----------------
# (Include the full HTML templates from the previous answer)

LOGIN_PAGE_HTML = ''' ... '''  # Use the same templates as before
SIGNUP_PAGE_HTML = ''' ... '''
MAIN_PAGE_HTML = ''' ... '''

# ---------------- Error Handlers ----------------
@app.errorhandler(500)
def internal_error(e):
    logger.error("Internal Server Error: %s", traceback.format_exc())
    return "Internal Server Error. Please check logs or try again later.", 500

@app.errorhandler(Exception)
def unhandled_exception(e):
    logger.error("Unhandled Exception: %s", traceback.format_exc())
    return "Internal Server Error. Please check logs.", 500

# ---------------- Initialize and Run ----------------
if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
else:
    # For gunicorn
    init_db()