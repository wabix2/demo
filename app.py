from flask import Flask, request, jsonify, render_template_string
import json
import random
import os
import datetime
import difflib
from deep_translator import GoogleTranslator

app = Flask(__name__)

# ---------------- LOAD DATA ----------------
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
memory = load_json("memory.json")

# ---------------- LANGUAGE DETECTION ----------------
def detect_lang(text):
    for ch in text:
        if '\u1200' <= ch <= '\u137F':
            return "am"
    return "en"

# ---------------- SYNONYM MAPPING ----------------
TOPIC_SYNONYMS = {
    "soil fertility": "Soil fertility testing",
    "soil test": "Soil fertility testing",
    "honey harvest": "Honey Harvesting",
    "coffee disease": "Prevention of Coffee Berry Disease",
    "cbd": "Prevention of Coffee Berry Disease",
    "sheep fattening": "Sheep Fattening",
    "teff sowing": "Teff Sowing",
    "teff seed": "Teff seed selection",
    "newcastle": "Preventing Newcastle disease in chickens",
    "trachoma": "Preventing eye infections (Trachoma)",
    "moringa": "Using Moringa leaves for nutrition",
    "rainwater": "Rainwater harvesting for home gardens",
    "aphids": "Natural ways to kill aphids on vegetables",
    "crop rotation": "Crop rotation to prevent soil exhaustion",
    "hermetic bags": "Storing grains in airtight (hermetic) bags",
    "sweet potato": "Sweet potato vine planting",
    "cassava mosaic": "Cassava mosaic disease prevention",
    "manure": "Using animal manure for fertilizer",
    "mulching": "Mulching techniques to keep soil moist",
    "soil health": "Identifying healthy vs. sick soil",
    "goat housing": "Housing requirements for goats",
    "cross breeding cows": "Benefits of cross-breeding local cows",
    "egg production": "Feeding poultry for better egg production",
    "rabies": "Signs of Rabies in farm dogs and livestock",
    "dead livestock": "Proper disposal of dead livestock to prevent disease",
    "iron rich foods": "Iron-rich foods for pregnant women",
    "clean water infant": "Importance of clean water for infant formula",
    "malaria children": "Recognizing signs of Malaria in children",
    "hand washing food": "Hand washing for food preparation safety",
    "choking": "Basic first aid for choking",
    "grain cleaning": "Cleaning and grading grains for market",
    "solar drying": "Solar drying for fruits and vegetables",
    "bookkeeping": "Basic bookkeeping for small farms",
    "egg transport": "Safe transport of eggs to market",
    "seed storage weevils": "Protecting stored seeds from weevils",
}

# ---------------- FUZZY MATCHING ----------------
def smart_match(query):
    query_lower = query.lower()
    
    # 1. Check synonym mapping
    for phrase, topic_name in TOPIC_SYNONYMS.items():
        if phrase in query_lower:
            if topic_name in kb:
                return kb[topic_name], 10
    
    # 2. Keyword matching
    query_words = set(query_lower.split())
    best_score = 0
    best_data = None

    for topic, data in kb.items():
        topic_words = set(topic.lower().split())
        keywords = set(data.get("keywords", []))
        all_keywords = topic_words | keywords

        matches = 0
        for qw in query_words:
            for kw in all_keywords:
                if qw == kw.lower() or difflib.SequenceMatcher(None, qw, kw.lower()).ratio() >= 0.75:
                    matches += 1
                    break

        if matches > best_score:
            best_score = matches
            best_data = data

    return best_data, best_score

# ---------------- TRANSLATION ----------------
def translate_to_english(text):
    try:
        src = 'am' if detect_lang(text) == 'am' else 'auto'
        translated = GoogleTranslator(source=src, target='en').translate(text)
        return translated, src if src != 'auto' else 'en'
    except Exception as e:
        print(f"Translation error: {e}")
        return text, 'en'

def translate_from_english(text, target_lang):
    if target_lang == 'en' or target_lang == 'auto':
        return text
    try:
        return GoogleTranslator(source='en', target=target_lang).translate(text)
    except:
        return text

# ---------------- MEMORY LEARNING ----------------
def learn(user, bot):
    key = user.lower()
    if key not in memory:
        memory[key] = {"answer": bot, "count": 1, "time": str(datetime.datetime.now())}
    else:
        memory[key]["count"] += 1
    with open("memory.json", "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=2, ensure_ascii=False)

# ---------------- SMART RESPONSE ENGINE ----------------
def generate_response(query):
    english_query, original_lang = translate_to_english(query)
    if query.lower() in memory:
        return memory[query.lower()]["answer"]

    data, score = smart_match(english_query)
    if data and score > 0:
        answers = data.get("answers", {})
        if original_lang in answers:
            response = random.choice(answers[original_lang])
        else:
            eng_answer = random.choice(answers.get("en", ["I understand but lack language support."]))
            response = translate_from_english(eng_answer, original_lang)
    else:
        if original_lang == "am":
            response = "ይቅርታ፣ ስለዚህ ጥያቄ መረጃ የለኝም። ስለ ጤፍ፣ በግ ማደለብ፣ ማር መሰብሰብ ወይም ጤና መጠየቅ ይችላሉ።"
        else:
            response = "I don't have information on that yet. Try asking about teff sowing, sheep fattening, honey harvesting, or health topics."
    return response

# ---------------- UI ----------------
HTML_TEMPLATE = """
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
<body class="bg-gradient-to-b from-emerald-50 to-teal-100 min-h-screen">
  <div class="max-w-3xl mx-auto p-4">
    <div class="bg-white/90 backdrop-blur-sm rounded-2xl shadow-xl p-6 border border-emerald-200">
      <div class="flex items-center gap-3 mb-4">
        <div class="bg-emerald-700 p-2 rounded-xl shadow-md">
          <svg xmlns="http://www.w3.org/2000/svg" class="h-8 w-8 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
          </svg>
        </div>
        <div>
          <h1 class="text-3xl font-bold text-gray-800">AfriVoice AI</h1>
          <p class="text-sm text-emerald-700 font-medium">Your Personal Assistant</p>
        </div>
      </div>
      <div id="chat-box" class="bg-gray-50/80 rounded-xl p-4 h-96 overflow-y-auto mb-4 border border-emerald-100 space-y-3 shadow-inner">
        <div class="flex justify-start">
          <div class="bg-emerald-100 text-emerald-900 px-4 py-2 rounded-2xl rounded-bl-none max-w-[80%]">
            👋 Hello! I'm AfriVoice AI, your personal assistant for farming, livestock, health, and education. Ask me anything in English or Amharic.
            <br><span class="text-sm">ሰላም! ስለ እርሻ፣ እንስሳት፣ ጤና እና ትምህርት በአማርኛ ወይም በእንግሊዝኛ ይጠይቁ።</span>
          </div>
        </div>
      </div>
      <form id="chat-form" class="flex gap-2">
        <input type="text" id="message" placeholder="Type your question..." 
               class="flex-1 border border-emerald-300 rounded-full px-5 py-3 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-transparent shadow-sm">
        <button type="submit" class="bg-emerald-700 text-white px-6 py-3 rounded-full hover:bg-emerald-800 transition shadow-md font-medium">Send</button>
      </form>
      <p class="text-xs text-gray-500 mt-3 text-center">🌱 Amharic & English • Spelling Tolerant • Learns from you</p>
    </div>
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
      } catch (err) {
        typingDiv.remove();
        addMessage('Error: Could not reach server. Please try again.', 'bot');
      }
    });
  </script>
</body>
</html>
"""

@app.route("/")
def home():
    return render_template_string(HTML_TEMPLATE)

@app.route("/chat", methods=["POST"])
def chat():
    user_msg = request.json.get("message", "")
    response = generate_response(user_msg)
    learn(user_msg, response)
    return jsonify({"response": response})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)