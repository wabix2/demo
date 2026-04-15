from flask import Flask, request, jsonify, render_template_string, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import json
import random
import os
import datetime
import difflib
import sqlite3
import sys
import traceback
from deep_translator import GoogleTranslator

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# ---------------- Flask-Login Setup ----------------
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "Please log in to access AfriVoice AI."

# ---------------- Database Setup (Render compatible) ----------------
DATABASE = '/tmp/afrivoice.db'

def init_db():
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

kb_raw = load_json("knowledge_base.json")
kb = flatten_knowledge_base(kb_raw)

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
        print(f"ERROR in translate_to_english: {e}", file=sys.stderr)
        return text, 'en'

def translate_from_english(text, target_lang):
    if target_lang == 'en' or target_lang == 'auto':
        return text
    try:
        return GoogleTranslator(source='en', target=target_lang).translate(text)
    except:
        return text

# ---------------- Topic Matching ----------------
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
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO conversations (user_id, message, response) VALUES (?, ?, ?)",
              (user_id, message, response))
    conn.commit()
    conn.close()

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
    english_query, original_lang = translate_to_english(query)
    
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

# ---------------- UI Templates ----------------
LOGIN_PAGE_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Login - AfriVoice AI</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gradient-to-br from-emerald-50 via-teal-50 to-green-100 min-h-screen flex items-center justify-center">
  <div class="max-w-md w-full mx-4">
    <div class="bg-white rounded-2xl shadow-xl p-8">
      <div class="text-center mb-6">
        <div class="bg-emerald-700 w-16 h-16 mx-auto rounded-xl flex items-center justify-center shadow-lg">
          <svg xmlns="http://www.w3.org/2000/svg" class="h-8 w-8 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
          </svg>
        </div>
        <h2 class="mt-4 text-2xl font-bold text-gray-800">Welcome Back</h2>
        <p class="text-gray-500">Sign in to AfriVoice AI</p>
      </div>
      {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
          {% for category, message in messages %}
            <div class="mb-4 p-3 rounded-lg {% if category == 'success' %}bg-green-100 text-green-700{% else %}bg-red-100 text-red-700{% endif %}">
              {{ message }}
            </div>
          {% endfor %}
        {% endif %}
      {% endwith %}
      <form method="POST" class="space-y-5">
        <div>
          <label class="block text-sm font-medium text-gray-700 mb-1">Username or Email</label>
          <input type="text" name="username" required class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
        </div>
        <div>
          <label class="block text-sm font-medium text-gray-700 mb-1">Password</label>
          <input type="password" name="password" required class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
        </div>
        <button type="submit" class="w-full bg-emerald-700 text-white py-3 rounded-lg font-semibold hover:bg-emerald-800 transition shadow-md">Sign In</button>
      </form>
      <p class="mt-6 text-center text-gray-600">Don't have an account? <a href="/signup" class="text-emerald-700 font-medium hover:underline">Create one</a></p>
    </div>
  </div>
</body>
</html>
'''

SIGNUP_PAGE_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sign Up - AfriVoice AI</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gradient-to-br from-emerald-50 via-teal-50 to-green-100 min-h-screen flex items-center justify-center">
  <div class="max-w-md w-full mx-4">
    <div class="bg-white rounded-2xl shadow-xl p-8">
      <div class="text-center mb-6">
        <div class="bg-emerald-700 w-16 h-16 mx-auto rounded-xl flex items-center justify-center shadow-lg">
          <svg xmlns="http://www.w3.org/2000/svg" class="h-8 w-8 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
          </svg>
        </div>
        <h2 class="mt-4 text-2xl font-bold text-gray-800">Create Account</h2>
        <p class="text-gray-500">Join AfriVoice AI today</p>
      </div>
      {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
          {% for category, message in messages %}
            <div class="mb-4 p-3 rounded-lg {% if category == 'success' %}bg-green-100 text-green-700{% else %}bg-red-100 text-red-700{% endif %}">
              {{ message }}
            </div>
          {% endfor %}
        {% endif %}
      {% endwith %}
      <form method="POST" class="space-y-4">
        <div>
          <label class="block text-sm font-medium text-gray-700 mb-1">Full Name</label>
          <input type="text" name="full_name" required class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
        </div>
        <div>
          <label class="block text-sm font-medium text-gray-700 mb-1">Username</label>
          <input type="text" name="username" required class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
        </div>
        <div>
          <label class="block text-sm font-medium text-gray-700 mb-1">Email</label>
          <input type="email" name="email" required class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
        </div>
        <div>
          <label class="block text-sm font-medium text-gray-700 mb-1">Password</label>
          <input type="password" name="password" required class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
        </div>
        <button type="submit" class="w-full bg-emerald-700 text-white py-3 rounded-lg font-semibold hover:bg-emerald-800 transition shadow-md">Sign Up</button>
      </form>
      <p class="mt-6 text-center text-gray-600">Already have an account? <a href="/login" class="text-emerald-700 font-medium hover:underline">Sign in</a></p>
    </div>
  </div>
</body>
</html>
'''

MAIN_PAGE_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AfriVoice AI - Your Personal Assistant</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    .typing::after { content: '▋'; animation: blink 1s infinite; }
    @keyframes blink { 50% { opacity: 0; } }
  </style>
</head>
<body class="bg-gradient-to-b from-emerald-50 to-teal-100 min-h-screen flex">
  <div class="w-80 bg-white/90 backdrop-blur-sm border-r border-emerald-200 p-4 flex-col hidden md:flex">
    <div class="flex items-center gap-3 mb-6">
      <div class="bg-emerald-700 p-2 rounded-xl shadow-md">
        <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
        </svg>
      </div>
      <div>
        <h2 class="text-lg font-bold text-gray-800">AfriVoice AI</h2>
        <p class="text-xs text-emerald-700">{{ current_user.full_name or current_user.username }}</p>
      </div>
    </div>
    <div class="flex-1 overflow-y-auto">
      <h3 class="text-sm font-semibold text-gray-500 mb-2">Recent Conversations</h3>
      <div id="history-list" class="space-y-1"></div>
    </div>
    <div class="pt-4 border-t border-gray-200">
      <a href="/logout" class="flex items-center gap-2 text-gray-600 hover:text-emerald-700 transition">
        <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
        </svg>
        <span>Logout</span>
      </a>
    </div>
  </div>

  <div class="flex-1 flex flex-col">
    <header class="bg-white/80 backdrop-blur-sm p-4 border-b border-emerald-200">
      <h1 class="text-xl font-semibold text-gray-800">AfriVoice AI <span class="text-xs bg-emerald-100 text-emerald-800 px-2 py-1 rounded-full ml-2">Your Personal Assistant</span></h1>
    </header>

    <div id="chat-box" class="flex-1 overflow-y-auto p-4 space-y-3">
      <div class="flex justify-start">
        <div class="bg-emerald-100 text-emerald-900 px-4 py-2 rounded-2xl rounded-bl-none max-w-[80%]">
          👋 Hello {{ current_user.full_name or current_user.username }}! I'm AfriVoice AI, your personal assistant. Ask me anything about farming, health, or education in English, Amharic, or Afaan Oromo.
        </div>
      </div>
    </div>

    <form id="chat-form" class="p-4 border-t border-emerald-200 bg-white/50 backdrop-blur-sm">
      <div class="flex gap-2">
        <input type="text" id="message" placeholder="Type your question..." 
               class="flex-1 border border-emerald-300 rounded-full px-5 py-3 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-transparent shadow-sm">
        <button type="submit" class="bg-emerald-700 text-white px-6 py-3 rounded-full hover:bg-emerald-800 transition shadow-md font-medium">Send</button>
      </div>
      <p class="text-xs text-gray-500 mt-2 text-center">Supports Amharic · Afaan Oromo · English · Remembers context</p>
    </form>
  </div>

  <script>
    const box = document.getElementById('chat-box');
    const form = document.getElementById('chat-form');
    const input = document.getElementById('message');

    function addMessage(text, sender) {
      const wrapper = document.createElement('div');
      wrapper.className = `flex ${sender === 'user' ? 'justify-end' : 'justify-start'}`;
      const bubble = document.createElement('div');
      bubble.className = `px-4 py-2 rounded-2xl max-w-[80%] shadow-sm ${
        sender === 'user' 
          ? 'bg-emerald-600 text-white rounded-br-none' 
          : 'bg-white text-gray-800 border border-gray-200 rounded-bl-none'
      }`;
      bubble.textContent = text;
      wrapper.appendChild(bubble);
      box.appendChild(wrapper);
      box.scrollTop = box.scrollHeight;
    }

    async function loadHistory() {
      try {
        const res = await fetch('/history');
        const data = await res.json();
        const list = document.getElementById('history-list');
        if(list){
          list.innerHTML = '';
          data.slice(0, 10).forEach(item => {
            const div = document.createElement('div');
            div.className = 'p-2 hover:bg-gray-100 rounded-lg cursor-pointer text-sm truncate';
            div.textContent = item.message;
            div.onclick = () => input.value = item.message;
            list.appendChild(div);
          });
        }
      } catch(e) {}
    }

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const msg = input.value.trim();
      if (!msg) return;
      
      addMessage(msg, 'user');
      input.value = '';
      
      const typingDiv = document.createElement('div');
      typingDiv.className = 'flex justify-start';
      typingDiv.innerHTML = '<div class="bg-gray-200 text-gray-600 px-4 py-2 rounded-2xl rounded-bl-none typing">Thinking...</div>';
      box.appendChild(typingDiv);
      box.scrollTop = box.scrollHeight;

      try {
        const res = await fetch('/chat', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({message: msg})
        });
        const data = await res.json();
        typingDiv.remove();
        addMessage(data.response, 'bot');
        loadHistory();
      } catch (err) {
        typingDiv.remove();
        addMessage('Error: Could not reach server.', 'bot');
      }
    });

    loadHistory();
  </script>
</body>
</html>
'''

# ---------------- Initialize Database at Module Level ----------------
print("Starting AfriVoice AI...", file=sys.stderr)
try:
    init_db()
    print("Database initialized successfully.", file=sys.stderr)
except Exception as e:
    print(f"Database init error: {e}", file=sys.stderr)
    traceback.print_exc()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)