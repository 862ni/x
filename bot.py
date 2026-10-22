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
        await msg.reply_text("الفيديو كبير 😅 لازم يكون أقل من 20 ميجا.")
        return

    status = await msg.reply_text("جاري الترميز... ⏳ ممكن ياخذ عدة دقايق.")

    async with lock:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "input")
            dst = os.path.join(tmp, "tiktok_ready.mp4")

            tg_file = await media.get_file()
            await tg_file.download_to_drive(src)

            info = await probe(src)
            if info is None:
                await status.edit_text("ما قدرت أقرأ الفيديو 😔 جرب فيديو ثاني.")
                return
            duration, fps = info

            # نحسب أعلى جودة تناسب حد 50 ميجا حق تليقرام
            total_kbps = TARGET_OUT * 8 / duration / 1000
            video_kbps = int(min(MAX_VIDEO_KBPS, total_kbps - AUDIO_KBPS))
            if video_kbps < MIN_ALLOWED_KBPS:
                await status.edit_text(
                    "الفيديو طويل مرة، ما أقدر أحافظ على الجودة ضمن حد 50 ميجا 😔 "
                    "جرب فيديو أقصر."
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
                "-preset", "veryfast", "-crf", "15",
                "-maxrate", f"{video_kbps}k", "-bufsize", f"{video_kbps * 2}k",
                "-g", str(fps * 2),
                "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-ar", "44100", "-ac", "2",
                "-movflags", "+faststart",
                dst,
            ]

            try:
                code, _ = await run(cmd, 1500)
            except asyncio.TimeoutError:
                await status.edit_text("الترميز أخذ وقت طويل وألغيته 😔 جرب فيديو أقصر.")
                return

            if code != 0 or not os.path.exists(dst):
                await status.edit_text("صار خطأ أثناء الترميز 😔 جرب فيديو ثاني.")
                return

            if os.path.getsize(dst) > MAX_OUT:
                await status.edit_text("الملف النهائي كبير على تليقرام، جرب فيديو أقصر.")
                return

            caption = "جاهز لتيك توك ✅ (1080x1920)"
            if video_kbps < MIN_OK_KBPS:
                caption += "\nتنبيه: الفيديو طويل شوي فالجودة أقل من المثالي."

            with open(dst, "rb") as f:
                await msg.reply_document(
                    document=f,
                    filename="tiktok_ready.mp4",
                    caption=caption,
                    read_timeout=300,
                    write_timeout=300,
                )
            await status.delete()


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, convert))

    base_url = os.environ.get("RENDER_EXTERNAL_URL")
    if base_url:
        port = int(os.environ.get("PORT", "10000"))
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=TOKEN,
            webhook_url=f"{base_url}/{TOKEN}",
        )
    else:
        app.run_polling()


if __name__ == "__main__":
    main()
