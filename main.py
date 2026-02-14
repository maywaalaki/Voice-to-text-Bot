import os
import threading
import json
import requests
import logging
import time
import tempfile
import subprocess
import glob
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, render_template_string, request, jsonify

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "300"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "20"))
MAX_UPLOAD_SIZE = MAX_UPLOAD_MB * 1024 * 1024
MAX_MESSAGE_CHUNK = 4095
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL", "")
DOWNLOADS_DIR = os.environ.get("DOWNLOADS_DIR", "./downloads")
GROQ_KEYS = os.environ.get("GROQ_KEYS", os.environ.get("GROQ_KEY", os.environ.get("ASSEMBLYAI_KEYS", os.environ.get("ASSEMBLYAI_KEY", ""))))
GROQ_TEXT_MODEL = os.environ.get("GROQ_TEXT_MODEL", "openai/gpt-oss-120b")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "6964068910"))

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
("🇨🇳 中文","zh"), ("🇸🇪 Svenska","sv"), ("🇳🇴 Norsk","no"),
("🇮🇱 עברית","he"), ("🇩🇰 Dansk","da"), ("🇪🇹 አማርኛ","am"), ("🇫🇮 Suomi","fi"),
("🇧🇩 বাংলা","bn"), ("🇰🇪 Kiswahili","sw"), ("🇪🇹 Oromo","om"), ("🇳🇵 नेपाली","ne"),
("🇵🇱 Polski","pl"), ("🇬🇷 Ελληνικά","el"), ("🇨🇿 Čeština","cs"), ("🇮🇸 Íslenska","is"),
("🇱🇹 Lietuvių","lt"), ("🇱🇻 Latviešu","lv"), ("🇭🇷 Hrvatski","hr"), ("🇷🇸 Bosanski","bs"),
("🇭🇺 Magyar","hu"), ("🇷🇴 Română","ro"), ("🇸🇴 Somali","so"), ("🇲🇾 Melayu","ms"),
("🇺🇿 O'zbekcha","uz"), ("🇵🇭 Tagalog","tl"), ("🇵🇹 Português","pt"), ("Auto Detect ⭐️","")
]

user_mode = {}
user_transcriptions = {}
action_usage = {}
user_selected_lang = {}

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)

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
    raise RuntimeError(f"Groq failed after rotations. Last error: {last_exc}")

def transcribe_local_file_groq(file_path, language=None):
    if not groq_rotator.keys:
        raise RuntimeError("Groq key(s) not configured")
    def perform_all_steps(key):
        files = {"file": open(file_path, "rb")}
        data = {"model": "whisper-large-v3"}
        if language:
            data["language"] = language
        headers = {"authorization": f"Bearer {key}"}
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
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
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
        row.append(InlineKeyboardButton(lbl, callback_data=f"lang|{code}|{lbl}|{origin}"))
        if i % 3 == 0:
            btns.append(row)
            row = []
    if row:
        btns.append(row)
    return InlineKeyboardMarkup(btns)

def build_summarize_keyboard(origin):
    btns = [
        [InlineKeyboardButton("Short", callback_data=f"summopt|Short|{origin}")],
        [InlineKeyboardButton("Detailed", callback_data=f"summopt|Detailed|{origin}")],
        [InlineKeyboardButton("Bulleted", callback_data=f"summopt|Bulleted|{origin}")]
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
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Join", url=f"https://t.me/{clean}")]])
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

@app := Flask(__name__)  # assignment expression to keep single-character name used later

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
        bot.edit_message_text(f"you choosed: {mode}", call.message.chat.id, call.message.message_id, reply_markup=None)
    except:
        pass
    bot.answer_callback_query(call.id, f"Mode set to: {mode} ☑️")

@bot.message_handler(commands=['lang'])
def lang_command(message):
    if ensure_joined(message):
        kb = build_lang_keyboard("file")
        bot.reply_to(message, "Select the language spoken in your audio or video:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith('lang|'))
def lang_cb(call):
    _, code, lbl, origin = call.data.split("|")
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except:
            pass
    chat_id = call.message.chat.id
    user_selected_lang[chat_id] = code
    bot.answer_callback_query(call.id, f"you set: {lbl} ☑️")
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
    process_text_action(call, origin, f"Summarize ({style})", prompt)

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
    bot.send_chat_action(chat_id, 'typing')
    try:
        res = ask_groq(text, prompt_instr)
        send_long_text(chat_id, res, data["origin"], call.from_user.id, log_action)
    except Exception as e:
        bot.send_message(chat_id, f"Error: {e}")

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
    if getattr(media, 'file_size', 0) > MAX_UPLOAD_SIZE:
        bot.reply_to(message, f"Just send me a file less than {MAX_UPLOAD_MB}MB 😎")
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
        download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
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
            segment_pattern = os.path.join(DOWNLOADS_DIR, f"chunk_{os.path.basename(tmp_out_path)}_%03d.mp3")
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
            fname = os.path.join(DOWNLOADS_DIR, f"{action}.txt")
            with open(fname, "w", encoding="utf-8") as f:
                f.write(text)
            sent = bot.send_document(chat_id, open(fname, 'rb'), caption="Open this file and copy the text inside 👍", reply_to_message_id=reply_id)
            os.remove(fname)
            return sent
    sent = bot.send_message(chat_id, text, reply_to_message_id=reply_id)
    return sent

TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bot Tips & Tricks</title>
<style>
:root{--bg:#0f1724;--card:#0b1220;--muted:#94a3b8;--accent:#7dd3fc}
*{box-sizing:border-box}
body{margin:0;font-family:Inter,Segoe UI,Roboto,Arial;background:linear-gradient(180deg,#071029 0%,#07132a 100%);color:#e6eef8}
.header{padding:28px 20px;text-align:center}
.header h1{margin:0;font-weight:700;font-size:24px}
.container{max-width:980px;margin:20px auto;padding:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}
.card{background:linear-gradient(180deg,rgba(255,255,255,0.02),rgba(0,0,0,0.06));border-radius:12px;padding:18px;border:1px solid rgba(255,255,255,0.03);box-shadow:0 6px 20px rgba(2,6,23,0.6)}
.card h3{margin:0 0 8px 0;font-size:16px}
.card p{margin:0 0 10px 0;color:var(--muted);font-size:14px;line-height:1.45}
.badge{display:inline-block;padding:6px 10px;border-radius:999px;background:rgba(125,211,252,0.08);color:var(--accent);font-weight:600;font-size:12px;margin-right:8px}
.footer{margin-top:18px;color:var(--muted);font-size:13px;text-align:center}
.kv{display:flex;gap:8px;align-items:center;margin-top:6px}
.kv b{min-width:140px;color:#dbeafe}
.copy{margin-top:12px;display:inline-block;padding:10px 14px;border-radius:8px;background:var(--accent);color:#04263b;font-weight:700;border:none;cursor:pointer}
.small{font-size:13px;color:var(--muted)}
.tip-list{padding-left:16px;margin:6px 0}
.tip-list li{margin:8px 0}
</style>
</head>
<body>
<div class="header">
  <h1>Tips & Tricks for your Transcription Bot</h1>
  <div class="small">Quick guide to get the best results from the bot</div>
</div>
<div class="container">
  <div class="grid">
    <div class="card">
      <div class="badge">Upload</div>
      <h3>Supported files & limits</h3>
      <p>The bot accepts voice, audio, video, and document files. Keep uploads under <strong>{{ max_mb }} MB</strong> for reliable processing.</p>
      <div class="kv"><b>Best formats</b><span class="small">mp3, m4a, wav, ogg, mp4</span></div>
      <div class="kv"><b>Long files</b><span class="small">Files longer than 30 minutes are auto-segmented and transcribed in chunks</span></div>
    </div>
    <div class="card">
      <div class="badge">Language</div>
      <h3>Choose the language</h3>
      <p>Use the /lang command or the language selector at bot start to set the spoken language before sending audio. Auto-detect is available but explicit selection improves accuracy.</p>
      <ul class="tip-list">
        <li>Open bot, tap the language flag, then send your audio.</li>
        <li>If transcription looks wrong, try switching to the exact language variant.</li>
      </ul>
    </div>
    <div class="card">
      <div class="badge">Mode</div>
      <h3>How transcripts are delivered</h3>
      <p>Use /mode to select Split messages or Text File. Split messages break long transcripts for easy reading. Text File gives you a downloadable .txt.</p>
    </div>
    <div class="card">
      <div class="badge">Summaries</div>
      <h3>Use summarize options</h3>
      <p>For long transcripts, open the "Get Summarize" button to choose Short, Detailed, or Bulleted summaries. This is helpful for quick highlights.</p>
    </div>
    <div class="card">
      <div class="badge">Privacy</div>
      <h3>Data handling</h3>
      <p>Files are downloaded temporarily to the server, processed, and removed. Keep sensitive content in mind before uploading.</p>
    </div>
    <div class="card">
      <div class="badge">Troubleshooting</div>
      <h3>Common fixes</h3>
      <ul class="tip-list">
        <li>If transcription returns empty, resend a smaller file or check network</li>
        <li>For noisy audio, use a cleaner recording or run noise reduction before upload</li>
        <li>If a language is mis-detected, explicitly set it with /lang</li>
      </ul>
    </div>
  </div>

  <div style="margin-top:18px;" class="card">
    <h3>Quick actions</h3>
    <p class="small">Copy this pack of quick commands to use in Telegram</p>
    <pre id="quick" style="background:rgba(255,255,255,0.02);padding:12px;border-radius:8px;color:#cfe9ff">/start
/lang
/mode
Send voice / audio / video to transcribe
Use "Get Summarize" on long transcripts</pre>
    <button class="copy" onclick="copyQuick()">Copy commands</button>
    <div class="footer">Hosted by the bot service. Port: <strong id="port">{{ port }}</strong></div>
  </div>
</div>

<script>
function copyQuick(){
  const txt = document.getElementById('quick').innerText;
  navigator.clipboard.writeText(txt).then(()=>{alert('Copied to clipboard')}).catch(()=>{prompt('Copy the text manually', txt)});
}
</script>
</body>
</html>
"""

@app.route("/")
def home():
    return render_template_string(TEMPLATE, max_mb=MAX_UPLOAD_MB, port=request.environ.get("SERVER_PORT", os.environ.get("PORT", "8080")))

@app.route("/health")
def health():
    return jsonify({"status":"ok","uptime":int(time.time())})

if __name__ == "__main__":
    try:
        bot.remove_webhook()
    except:
        pass
    def run_flask():
        port = int(os.environ.get("PORT", "8080"))
        app.run(host="0.0.0.0", port=port, threaded=True)
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)
        except Exception as e:
            logging.exception("Polling failure: %s", e)
            time.sleep(5)
