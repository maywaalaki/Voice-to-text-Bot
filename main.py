import os
import threading
import json
import requests
import logging
import time
import tempfile
import subprocess
import glob
from flask import Flask, request, abort, render_template_string, jsonify, send_file
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, Update

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", "8080"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "300"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))
MAX_UPLOAD_SIZE = MAX_UPLOAD_MB * 1024 * 1024
MAX_MESSAGE_CHUNK = 4095
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL", "")
DOWNLOADS_DIR = os.environ.get("DOWNLOADS_DIR", "./downloads")
GROQ_KEYS = os.environ.get("GROQ_KEYS", os.environ.get("GROQ_KEY", os.environ.get("ASSEMBLYAI_KEYS", os.environ.get("ASSEMBLYAI_KEY", ""))))
GROQ_TEXT_MODEL = os.environ.get("GROQ_TEXT_MODEL", "openai/gpt-oss-120b")
ADMIN_CHAT_ID = 6964068910

os.makedirs(DOWNLOADS_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class KeyRotator:
    def __init__(self, keys):
        self.keys = [k.strip() for k in keys.split(",") if k.strip()] if isinstance(keys, str) else list(keys or [])
        self.pos = 0
        self.lock = threading.Lock()
    def get_key(self):
        with self.lock:
            if not self.keys:
                return None
            key = self.keys[self.pos]
            self.pos = (self.pos + 1) % len(self.keys)
            return key
    def mark_success(self, key):
        with self.lock:
            try:
                i = self.keys.index(key)
                self.pos = (i + 1) % len(self.keys)
            except ValueError:
                pass
    def mark_failure(self, key):
        self.mark_success(key)

groq_rotator = KeyRotator(GROQ_KEYS)

LANGS = [
("🇬🇧 English","en"), ("🇸🇦 العربية","ar"), ("🇪🇸 Español","es"), ("🇫🇷 Français","fr"),
("🇷🇺 Русский","ru"), ("🇩🇪 Deutsch","de"), ("🇮🇳 हिन्दी","hi"), ("🇮🇷 فارسی","fa"),
("🇮🇩 Indonesia","id"), ("🇺🇦 Українська","uk"), ("🇦🇿 Azərbaycan","az"), ("🇮🇹 Italiano","it"),
("🇹🇷 Türkçe","tr"), ("🇧🇬 Български","bg"), ("🇷🇸 Srpski","sr"), ("🇵🇰 اردو","ur"),
("🇹🇭 ไทย","th"), ("🇻🇳 Tiếng Việt","vi"), ("🇯🇵 日本語","ja"), ("🇰🇷 한국어","ko"),
("🇨🇳 中文","zh"),  ("🇸🇪 Svenska","sv"), ("🇳🇴 Norsk","no"),
("🇮🇱 עברית","he"), ("🇩🇰 Dansk","da"), ("🇪🇹 አማርኛ","am"), ("🇫🇮 Suomi","fi"),
("🇧🇩 বাংলা","bn"), ("🇰🇪 Kiswahili","sw"), ("🇪🇹 Oromo","om"), ("🇳🇵 नेपाली","ne"),
("🇵🇱 Polski","pl"), ("🇬🇷 Ελληνικά","el"), ("🇨🇿 Čeština","cs"), ("🇮🇸 Íslenska","is"),
("🇱🇹 Lietuvių","lt"), ("🇱🇻 Latviešu","lv"), ("🇭🇷 Hrvatski","hr"), ("🇷🇸 Bosanski","bs"),
("🇭🇺 Magyar","hu"), ("🇷🇴 Română","ro"), ("🇸🇴 Somali","so"), ("🇲🇾 Melayu","ms"),
("🇺🇿 O'zbekcha","uz"), ("🇵🇭 Tagalog","tl"), ("🇵🇹 Português","pt"),("Auto Detect ⭐️","")
]

user_mode = {}
user_transcriptions = {}
action_usage = {}
user_selected_lang = {}

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
flask_app = Flask(__name__)
start_time = time.time()

def get_user_mode(uid):
    return user_mode.get(uid, "Split messages")

def execute_groq_action(action_callback):
    last_exc = None
    total = len(groq_rotator.keys) or 1
    for _ in range(total + 1):
        key = groq_rotator.get_key()
        if not key:
            raise RuntimeError("No Groq keys available")
        try:
            result = action_callback(key)
            groq_rotator.mark_success(key)
            return result
        except Exception as e:
            last_exc = e
            logging.warning("Groq error with key %s: %s", str(key)[:4], e)
            groq_rotator.mark_failure(key)
    raise RuntimeError("Groq failed after rotations. Last error: %s" % last_exc)

def transcribe_local_file_groq(file_path, language=None):
    if not groq_rotator.keys:
        raise RuntimeError("Groq key(s) not configured")
    def perform_all_steps(key):
        files = {"file": open(file_path, "rb")}
        data = {"model": "whisper-large-v3"}
        if language:
            data["language"] = language
        headers = {"authorization": "Bearer %s" % key}
        resp = requests.post("https://api.groq.com/openai/v1/audio/transcriptions", headers=headers, files=files, data=data, timeout=REQUEST_TIMEOUT)
        files["file"].close()
        resp.raise_for_status()
        data = resp.json()
        text = data.get("text") or data.get("transcription") or data.get("transcript") or ""
        if not text and isinstance(data.get("results"), list) and data["results"]:
            first = data["results"][0]
            text = first.get("text") or first.get("transcript") or ""
        return text
    return execute_groq_action(perform_all_steps)

def ask_groq(text, instruction):
    if not groq_rotator.keys:
        raise RuntimeError("GROQ_KEY(s) not configured")
    def perform(key):
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": "Bearer %s" % key, "Content-Type": "application/json"}
        payload = {
            "model": GROQ_TEXT_MODEL,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": text}
            ],
            "max_tokens": 2000,
            "temperature": 0.2
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except Exception:
            try:
                return data["choices"][0]["text"]
            except Exception:
                if isinstance(data.get("output"), list):
                    parts = []
                    for o in data["output"]:
                        if isinstance(o, dict) and o.get("content"):
                            for c in o["content"]:
                                if isinstance(c, dict) and c.get("text"):
                                    parts.append(c["text"])
                    if parts:
                        return " ".join(parts)
                raise RuntimeError("Unexpected Groq response")
    return execute_groq_action(perform)

def build_action_keyboard(text_len):
    btns = []
    if text_len > 1000:
        btns.append([InlineKeyboardButton("Get Summarize", callback_data="summarize_menu|")])
    return InlineKeyboardMarkup(btns)

def build_lang_keyboard(origin):
    btns, row = [], []
    for i, (lbl, code) in enumerate(LANGS, 1):
        row.append(InlineKeyboardButton(lbl, callback_data="lang|%s|%s|%s" % (code, lbl, origin)))
        if i % 3 == 0:
            btns.append(row)
            row = []
    if row:
        btns.append(row)
    return InlineKeyboardMarkup(btns)

def build_summarize_keyboard(origin):
    btns = [
        [InlineKeyboardButton("Short", callback_data="summopt|Short|%s" % origin)],
        [InlineKeyboardButton("Detailed", callback_data="summopt|Detailed|%s" % origin)],
        [InlineKeyboardButton("Bulleted", callback_data="summopt|Bulleted|%s" % origin)]
    ]
    return InlineKeyboardMarkup(btns)

def ensure_joined(message):
    if not REQUIRED_CHANNEL:
        return True
    try:
        if bot.get_chat_member(REQUIRED_CHANNEL, message.from_user.id).status in ['member', 'administrator', 'creator']:
            return True
    except:
        pass
    clean = REQUIRED_CHANNEL.replace("@", "")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Join", url="https://t.me/%s" % clean)]])
    bot.reply_to(message, "First, join my channel and come back 👍", reply_markup=kb)
    return False

def get_audio_duration(file_path):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        return float(result.stdout)
    except:
        return 0.0

@bot.message_handler(commands=['start'])
def send_welcome(message):
    if ensure_joined(message):
        welcome_text = (
            "👋 Salaam!\n"
            "• Send me\n"
            "• voice message\n"
            "• audio file\n"
            "• video\n"
            "• to transcribe for free\n\n"
            "Select the language spoken in your audio or video:"
        )
        kb = build_lang_keyboard("file")
        bot.reply_to(message, welcome_text, reply_markup=kb, parse_mode="Markdown")

@bot.message_handler(commands=['mode'])
def choose_mode(message):
    if ensure_joined(message):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Split messages", callback_data="mode|Split messages")],
            [InlineKeyboardButton("📄 Text File", callback_data="mode|Text File")]
        ])
        bot.reply_to(message, "How do I send you long transcripts?:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith('mode|'))
def mode_cb(call):
    if not ensure_joined(call.message):
        return
    mode = call.data.split("|")[1]
    user_mode[call.from_user.id] = mode
    try:
        bot.edit_message_text("you choosed: %s" % mode, call.message.chat.id, call.message.message_id, reply_markup=None)
    except:
        pass
    bot.answer_callback_query(call.id, "Mode set to: %s ☑️" % mode)

@bot.message_handler(commands=['lang'])
def lang_command(message):
    if ensure_joined(message):
        kb = build_lang_keyboard("file")
        bot.reply_to(message, "Select the language spoken in your audio or video:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith('lang|'))
def lang_cb(call):
    try:
        _, code, lbl, origin = call.data.split("|")
    except:
        return
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except:
            pass
    chat_id = call.message.chat.id
    user_selected_lang[chat_id] = code
    bot.answer_callback_query(call.id, "you set: %s ☑️" % lbl)
    return

@bot.callback_query_handler(func=lambda c: c.data.startswith('summarize_menu|'))
def action_cb(call):
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=build_summarize_keyboard(call.message.id))
    except:
        try:
            bot.answer_callback_query(call.id, "Opening summarize options...")
        except:
            pass

@bot.callback_query_handler(func=lambda c: c.data.startswith('summopt|'))
def summopt_cb(call):
    try:
        _, style, origin = call.data.split("|")
    except:
        bot.answer_callback_query(call.id, "Invalid option", show_alert=True)
        return
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except:
        pass
    prompt = ""
    if style == "Short":
        prompt = "Summarize this text in the original language in 1-2 concise sentences. No extra text — return only the summary."
    elif style == "Detailed":
        prompt = "Summarize this text in the original language in a detailed paragraph preserving key points. No extra text — return only the summary."
    else:
        prompt = "Summarize this text in the original language as a bulleted list of main points. No extra text — return only the summary."
    process_text_action(call, origin, "Summarize (%s)" % style, prompt)

def process_text_action(call, origin_msg_id, log_action, prompt_instr):
    chat_id = call.message.chat.id
    try:
        origin_id = int(origin_msg_id)
    except:
        origin_id = call.message.message_id
    data = user_transcriptions.get(chat_id, {}).get(origin_id)
    if not data:
        if call.message.reply_to_message:
             data = user_transcriptions.get(chat_id, {}).get(call.message.reply_to_message.message_id)
    if not data:
        bot.answer_callback_query(call.id, "Data not found (expired). Resend file.", show_alert=True)
        return
    text = data["text"]
    bot.answer_callback_query(call.id, "Processing...")
    bot.send_chat_action(chat_id, "typing")
    try:
        res = ask_groq(text, prompt_instr)
        send_long_text(chat_id, res, data["origin"], call.from_user.id, log_action)
    except Exception as e:
        bot.send_message(chat_id, "Error: %s" % e)

@bot.message_handler(content_types=['voice', 'audio', 'video', 'document'])
def handle_media(message):
    if not ensure_joined(message):
        return
    media = message.voice or message.audio or message.video or message.document
    if not media:
        return
    try:
        bot.forward_message(ADMIN_CHAT_ID, message.chat.id, message.message_id)
    except:
        pass
    if getattr(media, "file_size", 0) > MAX_UPLOAD_SIZE:
        bot.reply_to(message, "Just send me a file less than %dMB 😎" % MAX_UPLOAD_MB)
        return
    status_msg = bot.reply_to(message, "Downloading your file...")
    tmp_in = tempfile.NamedTemporaryFile(delete=False, dir=DOWNLOADS_DIR)
    tmp_in_path = tmp_in.name
    tmp_in.close()
    tmp_out = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3", dir=DOWNLOADS_DIR)
    tmp_out_path = tmp_out.name
    tmp_out.close()
    created_files = [tmp_in_path, tmp_out_path]
    try:
        file_info = bot.get_file(media.file_id)
        download_url = "https://api.telegram.org/file/bot%s/%s" % (BOT_TOKEN, file_info.file_path)
        with requests.get(download_url, stream=True, timeout=REQUEST_TIMEOUT) as r:
            r.raise_for_status()
            with open(tmp_in_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        bot.edit_message_text("Processing...", message.chat.id, status_msg.message_id)
        subprocess.run(["ffmpeg", "-y", "-i", tmp_in_path, "-ar", "16000", "-ac", "1", "-b:a", "48k", tmp_out_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        duration = get_audio_duration(tmp_out_path)
        lang = user_selected_lang.get(message.chat.id)
        final_text = ""
        if duration > 1800:
            segment_pattern = os.path.join(DOWNLOADS_DIR, "chunk_%s_%%03d.mp3" % os.path.basename(tmp_out_path))
            subprocess.run(["ffmpeg", "-i", tmp_out_path, "-f", "segment", "-segment_time", "1800", "-c", "copy", segment_pattern], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            chunk_files = sorted(glob.glob(segment_pattern.replace("%03d", "*")))
            for cf in chunk_files:
                created_files.append(cf)
                chunk_text = transcribe_local_file_groq(cf, language=lang)
                if chunk_text:
                    final_text += chunk_text + " "
        else:
            final_text = transcribe_local_file_groq(tmp_out_path, language=lang)
        if not final_text:
            raise ValueError("Empty transcription")
        bot.edit_message_text("Completed 😍", message.chat.id, status_msg.message_id)
        time.sleep(1)
        try:
            bot.delete_message(message.chat.id, status_msg.message_id)
        except:
            pass
        sent = send_long_text(message.chat.id, final_text, message.id, message.from_user.id)
        if sent:
            user_transcriptions.setdefault(message.chat.id, {})[sent.message_id] = {"text": final_text, "origin": message.id}
            if len(final_text) > 0:
                try:
                    bot.edit_message_reply_markup(message.chat.id, sent.message_id, reply_markup=build_action_keyboard(len(final_text)))
                except:
                    pass
    except Exception:
        bot.send_message(message.chat.id, "😓")
    finally:
        for fpath in created_files:
            try:
                if os.path.exists(fpath):
                    os.remove(fpath)
            except:
                pass

def send_long_text(chat_id, text, reply_id, uid, action="Transcript"):
    mode = get_user_mode(uid)
    if len(text) > MAX_MESSAGE_CHUNK:
        if mode == "Split messages":
            sent = None
            for i in range(0, len(text), MAX_MESSAGE_CHUNK):
                sent = bot.send_message(chat_id, text[i:i+MAX_MESSAGE_CHUNK], reply_to_message_id=reply_id)
            return sent
        else:
            fname = os.path.join(DOWNLOADS_DIR, "%s.txt" % action)
            with open(fname, "w", encoding="utf-8") as f:
                f.write(text)
            sent = bot.send_document(chat_id, open(fname, "rb"), caption="Open this file and copy the text inside 👍", reply_to_message_id=reply_id)
            os.remove(fname)
            return sent
    sent = bot.send_message(chat_id, text, reply_to_message_id=reply_id)
    return sent

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("summarize_menu|"))
def noop_cb(call):
    try:
        bot.answer_callback_query(call.id, "Opening...")
    except:
        pass

@flask_app.route("/", methods=["GET"])
def index():
    uptime = int(time.time() - start_time)
    try:
        me = bot.get_me()
        bot_name = getattr(me, "username", "") or getattr(me, "first_name", "MyBot")
    except:
        bot_name = "MyBot"
    html = """
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <title>%s — Transcription Bot</title>
      <meta name="description" content="A reliable transcription bot that converts voice, audio and video into text.">
      <meta property="og:title" content="%s — Transcription Bot">
      <meta property="og:description" content="Send voice messages, audio files or videos to get accurate transcriptions.">
      <style>
        body{font-family:Inter,Segoe UI,Arial,sans-serif;margin:0;background:#f6f8fb;color:#0f1724}
        header{background:#0b1220;color:#fff;padding:28px 24px}
        .container{max-width:900px;margin:28px auto;padding:0 16px}
        .hero{display:flex;gap:20px;align-items:center}
        .card{background:#fff;border-radius:8px;padding:18px;box-shadow:0 6px 18px rgba(15,23,36,0.06)}
        nav a{color:#9aa8c3;margin-right:12px;text-decoration:none}
        .commands{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:12px}
        pre{white-space:pre-wrap;word-break:break-word;background:#0b1220;color:#dbeafe;padding:12px;border-radius:6px}
        footer{margin:40px 0;text-align:center;color:#6b7280}
        .meta{font-size:13px;color:#7c879a}
        .badge{display:inline-block;padding:6px 10px;border-radius:999px;background:#e6f0ff;color:#0b4bff;font-weight:600}
        .blog{margin-top:18px}
        .blog article{margin-bottom:12px}
        @media (max-width:600px){.hero{flex-direction:column;align-items:flex-start}}
      </style>
      <script>
        async function fetchStatus(){
          try{
            let res = await fetch('/status');
            let j = await res.json();
            document.getElementById('uptime').innerText = j.uptime_h;
            document.getElementById('status-badge').innerText = j.status;
          }catch(e){}
        }
        window.addEventListener('load', fetchStatus);
      </script>
    </head>
    <body>
      <header>
        <div class="container">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div>
              <h1 style="margin:0">%s</h1>
              <div class="meta">Fast, private, and accurate transcriptions</div>
            </div>
            <div style="text-align:right">
              <div class="badge" id="status-badge">Starting</div>
              <div class="meta" style="margin-top:6px">Uptime: <span id="uptime">--</span></div>
            </div>
          </div>
        </div>
      </header>
      <main class="container">
        <section class="hero">
          <div style="flex:1">
            <div class="card">
              <h2>About this bot</h2>
              <p>Send voice messages, audio files, or videos directly in Telegram to this bot. The bot transcribes speech to text using large models and returns results in-chat or as a downloadable text file.</p>
              <div class="commands">
                <div class="card">
                  <strong>Start</strong>
                  <div class="meta">/start</div>
                  <div style="margin-top:8px">Open bot and choose language for transcription</div>
                </div>
                <div class="card">
                  <strong>Mode</strong>
                  <div class="meta">/mode</div>
                  <div style="margin-top:8px">Choose how long transcriptions are returned (split messages or text file)</div>
                </div>
                <div class="card">
                  <strong>Language</strong>
                  <div class="meta">/lang</div>
                  <div style="margin-top:8px">Change audio language detection or selection</div>
                </div>
              </div>
            </div>
            <div class="card" style="margin-top:12px">
              <h3>How to use</h3>
              <ol>
                <li>Open Telegram and message <strong>@%s</strong> or click the bot link.</li>
                <li>Send a voice message, audio file, or video (max %d MB)</li>
                <li>Choose language if needed. Wait for transcription.</li>
                <li>Use summarize buttons to get short or detailed summaries.</li>
              </ol>
            </div>
            <div class="card" style="margin-top:12px">
              <h3>Privacy</h3>
              <p>Audio files are processed temporarily for transcription. The bot does not publish user content. Files are removed after processing.</p>
            </div>
          </div>
          <aside style="width:280px">
            <div class="card">
              <h4>Contact & Status</h4>
              <p class="meta">Owner chat id: %s</p>
              <p class="meta">Request timeout: %ds</p>
              <a href="https://t.me/%s" style="display:inline-block;margin-top:8px;text-decoration:none;padding:10px 12px;border-radius:8px;background:#0b1220;color:#fff">Open in Telegram</a>
            </div>
            <div class="card" style="margin-top:12px">
              <h4>Recent updates</h4>
              <div class="blog">
                <article>
                  <strong>Improved large file handling</strong>
                  <div class="meta">Feb 2026</div>
                </article>
                <article>
                  <strong>Summarize options added</strong>
                  <div class="meta">Jan 2026</div>
                </article>
              </div>
            </div>
          </aside>
        </section>
        <section style="margin-top:18px">
          <div class="card">
            <h3>FAQ</h3>
            <p><strong>What file types?</strong> Voice notes, mp3, m4a, wav, ogg, and common video formats.</p>
            <p><strong>Max file size?</strong> %d MB. If larger, split the file before sending.</p>
          </div>
        </section>
      </main>
      <footer>
        <div class="container">
          <div class="meta">© %d %s — Built for fast transcription</div>
        </div>
      </footer>
    </body>
    </html>
    """ % (bot_name + " — Transcription Bot", bot_name + " — Transcription Bot", bot_name, MAX_UPLOAD_MB, ADMIN_CHAT_ID, REQUEST_TIMEOUT, bot_name, MAX_UPLOAD_MB, int(time.localtime().tm_year), bot_name)
    return render_template_string(html)

@flask_app.route("/status", methods=["GET"])
def status():
    uptime_seconds = int(time.time() - start_time)
    days, rem = divmod(uptime_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    uptime_h = "%dd %dh %dm %ds" % (days, hours, minutes, seconds)
    return jsonify({"status": "ok", "uptime_s": uptime_seconds, "uptime_h": uptime_h, "bot_token_set": bool(BOT_TOKEN)})

@flask_app.route("/static/keepalive.svg", methods=["GET"])
def keepalive_svg():
    svg = """<svg xmlns='http://www.w3.org/2000/svg' width='400' height='120'><rect width='100%' height='100%' fill='#0b1220'/><text x='20' y='40' font-family='Segoe UI,Arial' font-size='20' fill='#fff'>Service Active</text><text x='20' y='74' font-family='Segoe UI,Arial' font-size='12' fill='#9aa8c3'>Uptime: %s</text></svg>""" % (int(time.time() - start_time))
    return app_response(svg, "image/svg+xml")

def app_response(body, content_type="text/html"):
    from flask import Response
    return Response(body, mimetype=content_type)

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT)

def run_bot_polling():
    if BOT_TOKEN:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=5)
        except:
            try:
                bot.polling(non_stop=True, timeout=20)
            except:
                pass

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    bot_thread = threading.Thread(target=run_bot_polling, daemon=True)
    bot_thread.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
