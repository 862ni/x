import os
import json
import asyncio
import tempfile

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

TOKEN = os.environ["BOT_TOKEN"]

MAX_IN = 20 * 1024 * 1024      # حد تليقرام للتنزيل: 20 ميجا
MAX_OUT = 50 * 1024 * 1024     # حد تليقرام للإرسال: 50 ميجا
TARGET_OUT = 46 * 1024 * 1024  # حجم الهدف للملف النهائي

AUDIO_KBPS = 192
MAX_VIDEO_KBPS = 16000   # أعلى جودة نستخدمها
MIN_OK_KBPS = 6000       # أقل من كذا نحذرك
MIN_ALLOWED_KBPS = 3000  # أقل من كذا نرفض الفيديو (طويل مرة)

# معالجة فيديو واحد في الوقت نفسه
lock = asyncio.Semaphore(1)


async def run(cmd, timeout):
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    return proc.returncode, out


def parse_fps(text):
    try:
        a, b = text.split("/")
        value = float(a) / float(b)
    except Exception:
        return 30
    if value <= 0:
        return 30
    return max(24, min(60, round(value)))


async def probe(path):
    code, out = await run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=avg_frame_rate:format=duration",
            "-of", "json", path,
        ],
        60,
    )
    if code != 0:
        return None
    try:
        data = json.loads(out)
        duration = float(data["format"]["duration"])
        fps = parse_fps(data["streams"][0]["avg_frame_rate"])
    except Exception:
        return None
    if duration <= 0:
        return None
    return duration, fps


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً! 👋\n"
        "أرسل لي الفيديو كـ *ملف* (File) عشان تليقرام ما يضغطه، "
        "وأرجعه لك MP4 جاهز لتيك توك بأعلى جودة ممكنة.\n\n"
        "الحد الأقصى للفيديو: 20 ميجا.",
        parse_mode="Markdown",
    )


async def convert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    media = msg.video or msg.document
    if media is None:
        return

    if msg.document and not (msg.document.mime_type or "").startswith("video/"):
        await msg.reply_text("أرسل لي فيديو فقط 🎬")
        return

    if media.file_size and media.file_size > MAX_IN:
        await msg.reply_text("الفي
