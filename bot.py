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

MAX_IN = 20 * 1024 * 1024
MAX_OUT = 50 * 1024 * 1024
TARGET_OUT = 46 * 1024 * 1024

AUDIO_KBPS = 192
MAX_VIDEO_KBPS = 16000
MIN_OK_KBPS = 6000
MIN_ALLOWED_KBPS = 3000


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
        "\u0623\u0647\u0644\u0627\u064b! \U0001f44b\n"
        "\u0623\u0631\u0633\u0644 \u0644\u064a \u0627\u0644\u0641\u064a\u062f\u064a\u0648 \u0643\u0640 *\u0645\u0644\u0641* (File) \u0639\u0634\u0627\u0646 \u062a\u0644\u064a\u0642\u0631\u0627\u0645 \u0645\u0627 \u064a\u0636\u063a\u0637\u0647\u060c "
        "\u0648\u0623\u0631\u062c\u0639\u0647 \u0644\u0643 MP4 \u062c\u0627\u0647\u0632 \u0644\u062a\u064a\u0643 \u062a\u0648\u0643 \u0628\u0623\u0639\u0644\u0649 \u062c\u0648\u062f\u0629 \u0645\u0645\u0643\u0646\u0629.\n\n"
        "\u0627\u0644\u062d\u062f \u0627\u0644\u0623\u0642\u0635\u0649 \u0644\u0644\u0641\u064a\u062f\u064a\u0648: 20 \u0645\u064a\u062c\u0627.",
        parse_mode="Markdown",
    )


async def convert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    media = msg.video or msg.document
    if media is None:
        return

    if msg.document and not (msg.document.mime_type or "").startswith("video/"):
        await msg.reply_text("\u0623\u0631\u0633\u0644 \u0644\u064a \u0641\u064a\u062f\u064a\u0648 \u0641\u0642\u0637 \U0001f3ac")
        return

    if media.file_size and media.file_size > MAX_IN:
        await msg.reply_text("\u0627\u0644\u0641\u064a\u062f\u064a\u0648 \u0643\u0628\u064a\u0631 \U0001f605 \u0644\u0627\u0632\u0645 \u064a\u0643\u0648\u0646 \u0623\u0642\u0644 \u0645\u0646 20 \u0645\u064a\u062c\u0627.")
        return

    status = await msg.reply_text("\u062c\u0627\u0631\u064a \u0627\u0644\u062a\u0631\u0645\u064a\u0632... \u23f3 \u0645\u0645\u0643\u0646 \u064a\u0627\u062e\u0630 \u0639\u062f\u0629 \u062f\u0642\u0627\u064a\u0642.")

    async with lock:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "input")
            dst = os.path.join(tmp, "tiktok_ready.mp4")

            tg_file = await media.get_file()
            await tg_file.download_to_drive(src)

            info = await probe(src)
            if info is None:
                await status.edit_text("\u0645\u0627 \u0642\u062f\u0631\u062a \u0623\u0642\u0631\u0623 \u0627\u0644\u0641\u064a\u062f\u064a\u0648 \U0001f614 \u062c\u0631\u0628 \u0641\u064a\u062f\u064a\u0648 \u062b\u0627\u0646\u064a.")
                return
            duration, fps = info


            total_kbps = TARGET_OUT * 8 / duration / 1000
            video_kbps = int(min(MAX_VIDEO_KBPS, total_kbps - AUDIO_KBPS))
            if video_kbps < MIN_ALLOWED_KBPS:
                await status.edit_text(
                    "\u0627\u0644\u0641\u064a\u062f\u064a\u0648 \u0637\u0648\u064a\u0644 \u0645\u0631\u0629\u060c \u0645\u0627 \u0623\u0642\u062f\u0631 \u0623\u062d\u0627\u0641\u0638 \u0639\u0644\u0649 \u0627\u0644\u062c\u0648\u062f\u0629 \u0636\u0645\u0646 \u062d\u062f 50 \u0645\u064a\u062c\u0627 \U0001f614 "
                    "\u062c\u0631\u0628 \u0641\u064a\u062f\u064a\u0648 \u0623\u0642\u0635\u0631."
                )
                return

            vf = (
                "scale=1080:1920:force_original_aspect_ratio=decrease:flags=lanczos,"
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,"
                "setsar=1,format=yuv420p"
            )

            cmd = [
                "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
                "-i", src,
                "-vf", vf,
                "-r", str(fps),
                "-c:v", "libx264", "-profile:v", "high", "-level", "4.2",
                "-preset", "veryfast", "-crf", "
