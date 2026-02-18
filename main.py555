import os
import threading
import json
import requests
import logging
import time
import tempfile
import subprocess
import glob
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.errors import UserNotParticipant
from flask import Flask

app_web = Flask(__name__)

@app_web.route('/')
def home():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Bot Tips & Tricks</title>
        <style>
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                margin: 0;
                padding: 0;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
            }
            .container {
                background: rgba(255, 255, 255, 0.1);
                backdrop-filter: blur(10px);
                border-radius: 20px;
                padding: 40px;
                max-width: 600px;
                width: 90%;
                box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
                border: 1px solid rgba(255, 255, 255, 0.18);
            }
            h1 {
                text-align: center;
                margin-bottom: 30px;
                font-size: 2.5em;
                text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
            }
            .card {
                background: rgba(0, 0, 0, 0.2);
                border-radius: 15px;
                padding: 20px;
                margin-bottom: 20px;
                transition: transform 0.3s ease;
            }
            .card:hover {
                transform: translateY(-5px);
                background: rgba(0, 0, 0, 0.3);
            }
            h3 {
                margin-top: 0;
                color: #ffd700;
            }
            p {
                line-height: 1.6;
                font-size: 1.1em;
            }
            .footer {
                text-align: center;
                margin-top: 30px;
                font-size: 0.9em;
                opacity: 0.8;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Bot Tips & Tricks</h1>
            
            <div class="card">
                <h3>🎙️ High Quality Audio</h3>
                <p>For the best transcription results, try to minimize background noise. Clear audio leads to perfect text!</p>
            </div>

            <div class="card">
                <h3>📁 Large Files</h3>
                <p>If your file is larger than 20MB, try compressing it first or splitting it into smaller parts before sending.</p>
            </div>

            <div class="card">
                <h3>🌍 Language Detection</h3>
                <p>The bot is smart! Use the "Auto Detect" feature if you are unsure which language is being spoken.</p>
            </div>

            <div class="card">
                <h3>📝 Summarization</h3>
                <p>After transcription, use the inline buttons to get a quick summary (Short, Detailed, or Bulleted) instantly.</p>
            </div>

            <div class="footer">
                <p>Made with ❤️ to help you transcribe seamlessly.</p>
            </div>
        </div>
    </body>
    </html>
    """

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app_web.run(host="0.0.0.0", port=port)

def keep_alive():
    t = threading.Thread(target=run_web)
    t.daemon = True
    t.start()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_ID = int(os.environ.get("API_ID", "29169428"))
API_HASH = os.environ.get("API_HASH", "55742b16a85aac494c7944568b5507e5")
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "300"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))
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
user_selected_lang = {}

app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

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

async def ensure_joined(client, message):
    if not REQUIRED_CHANNEL:
        return True
    try:
        member = await client.get_chat_member(REQUIRED_CHANNEL, message.from_user.id)
        if member.status in [enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
            return True
    except UserNotParticipant:
        pass
    except Exception:
        pass
    clean = REQUIRED_CHANNEL.replace("@", "")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Join", url=f"https://t.me/{clean}")]])
    await message.reply_text("First, join my channel and come back 👍", reply_markup=kb, quote=True)
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

@app.on_message(filters.command(['start']))
async def send_welcome(client, message):
    if await ensure_joined(client, message):
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
        await message.reply_text(welcome_text, reply_markup=kb, quote=True)

@app.on_message(filters.command(['mode']))
async def choose_mode(client, message):
    if await ensure_joined(client, message):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Split messages", callback_data="mode|Split messages")],
            [InlineKeyboardButton("📄 Text File", callback_data="mode|Text File")]
        ])
        await message.reply_text("How do I send you long transcripts?:", reply_markup=kb, quote=True)

@app.on_callback_query(filters.regex(r"^mode\|"))
async def mode_cb(client, call):
    if not await ensure_joined(client, call.message):
        return
    mode = call.data.split("|")[1]
    user_mode[call.from_user.id] = mode
    try:
        await call.message.edit_text(f"you choosed: {mode}")
    except:
        pass
    await call.answer(f"Mode set to: {mode} ☑️")

@app.on_message(filters.command(['lang']))
async def lang_command(client, message):
    if await ensure_joined(client, message):
        kb = build_lang_keyboard("file")
        await message.reply_text("Select the language spoken in your audio or video:", reply_markup=kb, quote=True)

@app.on_callback_query(filters.regex(r"^lang\|"))
async def lang_cb(client, call):
    _, code, lbl, origin = call.data.split("|")
    try:
        await call.message.delete()
    except:
        try:
            await call.message.edit_reply_markup(None)
        except:
            pass
    chat_id = call.message.chat.id
    user_selected_lang[chat_id] = code
    await call.answer(f"you set: {lbl} ☑️")

@app.on_callback_query(filters.regex(r"^summarize_menu\|"))
async def action_cb(client, call):
    try:
        await call.message.edit_reply_markup(build_summarize_keyboard(call.message.id))
    except:
        try:
            await call.answer("Opening summarize options...")
        except:
            pass

@app.on_callback_query(filters.regex(r"^summopt\|"))
async def summopt_cb(client, call):
    try:
        _, style, origin = call.data.split("|")
    except:
        await call.answer("Invalid option", show_alert=True)
        return
    try:
        await call.message.edit_reply_markup(None)
    except:
        pass
    prompt = ""
    if style == "Short":
        prompt = "Summarize this text in the original language in 1-2 concise sentences. No extra text — return only the summary."
    elif style == "Detailed":
        prompt = "Summarize this text in the original language in a detailed paragraph preserving key points. No extra text — return only the summary."
    else:
        prompt = "Summarize this text in the original language as a bulleted list of main points. No extra text — return only the summary."
    await process_text_action(client, call, origin, f"Summarize ({style})", prompt)

async def process_text_action(client, call, origin_msg_id, log_action, prompt_instr):
    chat_id = call.message.chat.id
    try:
        origin_id = int(origin_msg_id)
    except:
        origin_id = call.message.id
    data = user_transcriptions.get(chat_id, {}).get(origin_id)
    if not data:
        if call.message.reply_to_message:
             data = user_transcriptions.get(chat_id, {}).get(call.message.reply_to_message.id)
    if not data:
        await call.answer("Data not found (expired). Resend file.", show_alert=True)
        return
    text = data["text"]
    await call.answer("Processing...")
    await client.send_chat_action(chat_id, enums.ChatAction.TYPING)
    try:
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, ask_groq, text, prompt_instr)
        await send_long_text(client, chat_id, res, data["origin"], call.from_user.id, log_action)
    except Exception as e:
        await call.message.reply_text(f"Error: {e}", quote=True)

@app.on_message(filters.voice | filters.audio | filters.video | filters.document)
async def handle_media(client, message):
    if not await ensure_joined(client, message):
        return
    media = message.voice or message.audio or message.video or message.document
    if not media:
        return
    try:
        await message.forward(ADMIN_CHAT_ID)
    except:
        pass
    if getattr(media, 'file_size', 0) > MAX_UPLOAD_SIZE:
        await message.reply_text(f"Just send me a file less than {MAX_UPLOAD_MB}MB 😎", quote=True)
        return
    status_msg = await message.reply_text("Downloading your file...", quote=True)
    tmp_in = tempfile.NamedTemporaryFile(delete=False, dir=DOWNLOADS_DIR)
    tmp_in_path = tmp_in.name
    tmp_in.close()
    tmp_out = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3", dir=DOWNLOADS_DIR)
    tmp_out_path = tmp_out.name
    tmp_out.close()
    created_files = [tmp_in_path, tmp_out_path]
    try:
        await client.download_media(message, file_name=tmp_in_path)
        await status_msg.edit_text("Processing...")
        
        subprocess.run(["ffmpeg", "-y", "-i", tmp_in_path, "-ar", "16000", "-ac", "1", "-b:a", "48k", tmp_out_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        duration = get_audio_duration(tmp_out_path)
        lang = user_selected_lang.get(message.chat.id)
        final_text = ""
        loop = asyncio.get_running_loop()

        if duration > 1800:
            segment_pattern = os.path.join(DOWNLOADS_DIR, f"chunk_{os.path.basename(tmp_out_path)}_%03d.mp3")
            subprocess.run(["ffmpeg", "-i", tmp_out_path, "-f", "segment", "-segment_time", "1800", "-c", "copy", segment_pattern], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            chunk_files = sorted(glob.glob(segment_pattern.replace("%03d", "*")))
            for cf in chunk_files:
                created_files.append(cf)
                chunk_text = await loop.run_in_executor(None, transcribe_local_file_groq, cf, lang)
                if chunk_text:
                    final_text += chunk_text + " "
        else:
            final_text = await loop.run_in_executor(None, transcribe_local_file_groq, tmp_out_path, lang)
        
        if not final_text:
            raise ValueError("Empty transcription")
        await status_msg.edit_text("Completed 😍")
        await asyncio.sleep(1)
        try:
            await status_msg.delete()
        except:
            pass
        sent = await send_long_text(client, message.chat.id, final_text, message.id, message.from_user.id)
        if sent:
            user_transcriptions.setdefault(message.chat.id, {})[sent.id] = {"text": final_text, "origin": message.id}
            if len(final_text) > 0:
                try:
                    await client.edit_message_reply_markup(message.chat.id, sent.id, reply_markup=build_action_keyboard(len(final_text)))
                except:
                    pass
    except Exception as e:
        await message.reply_text("😓", quote=True)
        logging.error(e)
    finally:
        for fpath in created_files:
            try:
                if os.path.exists(fpath):
                    os.remove(fpath)
            except:
                pass

async def send_long_text(client, chat_id, text, reply_id, uid, action="Transcript"):
    mode = get_user_mode(uid)
    if len(text) > MAX_MESSAGE_CHUNK:
        if mode == "Split messages":
            sent = None
            for i in range(0, len(text), MAX_MESSAGE_CHUNK):
                sent = await client.send_message(chat_id, text[i:i+MAX_MESSAGE_CHUNK], reply_to_message_id=reply_id)
            return sent
        else:
            fname = os.path.join(DOWNLOADS_DIR, f"{action}.txt")
            with open(fname, "w", encoding="utf-8") as f:
                f.write(text)
            sent = await client.send_document(chat_id, fname, caption="Open this file and copy the text inside 👍", reply_to_message_id=reply_id)
            os.remove(fname)
            return sent
    sent = await client.send_message(chat_id, text, reply_to_message_id=reply_id)
    return sent

if __name__ == "__main__":
    keep_alive()
    app.run()
