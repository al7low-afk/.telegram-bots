import asyncio
import logging
import os
import tempfile
from pathlib import Path
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
import yt_dlp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
log = logging.getLogger("telegram-bots")

TIKTOK_TOKEN = os.getenv("TIKTOK_BOT_TOKEN", "").strip()
AZKAR_TOKEN = os.getenv("AZKAR_BOT_TOKEN", "").strip()

if not TIKTOK_TOKEN:
    raise RuntimeError("Missing Render Environment Variable: TIKTOK_BOT_TOKEN")
if not AZKAR_TOKEN:
    raise RuntimeError("Missing Render Environment Variable: AZKAR_BOT_TOKEN")

tiktok_bot = Bot(TIKTOK_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
azkar_bot = Bot(AZKAR_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
tiktok_dp = Dispatcher()
azkar_dp = Dispatcher()

# -------------------- Web endpoint for Render Web Service --------------------
async def health(request: web.Request) -> web.Response:
    return web.Response(text="Telegram bots service is running.")

async def start_web_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Health web server listening on port %s", port)
    return runner

# -------------------- TikTok downloader bot --------------------
@tiktok_dp.message(Command("start"))
async def tiktok_start(message: Message):
    await message.answer(
        "هلا بك في بوت تحميل تيك توك 🎬\n\n"
        "أرسل رابط فيديو تيك توك، وبحاول أحمّله لك.\n"
        "ملاحظة: بعض الروابط قد لا تعمل إذا منع تيك توك الوصول."
    )

@tiktok_dp.message(Command("help"))
async def tiktok_help(message: Message):
    await message.answer("أرسل رابط TikTok كامل يبدأ بـ https://www.tiktok.com أو https://vt.tiktok.com")

@tiktok_dp.message(F.text)
async def tiktok_download(message: Message):
    url = (message.text or "").strip()
    if url.startswith("/"):
        return
    if "tiktok.com/" not in url.lower():
        await message.answer("⚠️ أرسل رابط تيك توك صحيح.")
        return

    status = await message.answer("⏳ جاري محاولة تحميل الفيديو…")
    tmpdir = tempfile.mkdtemp(prefix="tiktok_")
    try:
        outtmpl = str(Path(tmpdir) / "video.%(ext)s")
        opts = {
            "outtmpl": outtmpl,
            "format": "best[ext=mp4]/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "retries": 2,
            "socket_timeout": 25,
            "max_filesize": 49 * 1024 * 1024,
        }
        loop = asyncio.get_running_loop()
        def download():
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return info
        info = await loop.run_in_executor(None, download)
        candidates = [p for p in Path(tmpdir).glob("*") if p.is_file()]
        if not candidates:
            raise RuntimeError("Downloader returned no media file")
        media_path = max(candidates, key=lambda p: p.stat().st_size)
        if media_path.stat().st_size > 49 * 1024 * 1024:
            await status.edit_text("❌ حجم الفيديو أكبر من حد تيليجرام المسموح للبوت. جرّب رابطًا أقصر.")
            return
        caption = "✅ تم التحميل"
        title = (info or {}).get("title")
        if title:
            caption += "\n" + str(title)[:700]
        with media_path.open("rb") as video:
            await message.answer_video(video=video, caption=caption)
        await status.delete()
    except Exception as exc:
        log.warning("TikTok download failed: %s", exc, exc_info=True)
        await status.edit_text(
            "❌ ما قدرت أحمّل الرابط هالمرة.\n"
            "قد يكون تيك توك منع الوصول أو أن الرابط خاص/غير مدعوم. جرّب رابطًا عامًا آخر."
        )
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

# -------------------- Azkar bot --------------------
AZKAR_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌅 أذكار الصباح"), KeyboardButton(text="🌇 أذكار المساء")],
        [KeyboardButton(text="🕌 أذكار بعد الصلاة"), KeyboardButton(text="😴 أذكار النوم")],
        [KeyboardButton(text="📿 تسبيح"), KeyboardButton(text="ℹ️ مساعدة")],
    ],
    resize_keyboard=True,
    input_field_placeholder="اختر قسم الأذكار",
)

AZKAR_TEXTS = {
    "🌅 أذكار الصباح": (
        "🌅 <b>أذكار الصباح</b>\n\n"
        "• أصبحنا وأصبح الملك لله، والحمد لله، لا إله إلا الله وحده لا شريك له، له الملك وله الحمد وهو على كل شيء قدير.\n\n"
        "• اللهم بك أصبحنا وبك أمسينا وبك نحيا وبك نموت وإليك النشور.\n\n"
        "• رضيت بالله ربًا، وبالإسلام دينًا، وبمحمد ﷺ نبيًا. (ثلاث مرات)\n\n"
        "• سبحان الله وبحمده. (مئة مرة)"
    ),
    "🌇 أذكار المساء": (
        "🌇 <b>أذكار المساء</b>\n\n"
        "• أمسينا وأمسى الملك لله، والحمد لله، لا إله إلا الله وحده لا شريك له، له الملك وله الحمد وهو على كل شيء قدير.\n\n"
        "• اللهم بك أمسينا وبك أصبحنا وبك نحيا وبك نموت وإليك المصير.\n\n"
        "• رضيت بالله ربًا، وبالإسلام دينًا، وبمحمد ﷺ نبيًا. (ثلاث مرات)\n\n"
        "• سبحان الله وبحمده. (مئة مرة)"
    ),
    "🕌 أذكار بعد الصلاة": (
        "🕌 <b>أذكار بعد الصلاة</b>\n\n"
        "• أستغفر الله. (ثلاث مرات)\n"
        "• اللهم أنت السلام ومنك السلام، تباركت يا ذا الجلال والإكرام.\n"
        "• سبحان الله (33 مرة)، الحمد لله (33 مرة)، الله أكبر (33 مرة)، ثم تمام المئة: لا إله إلا الله وحده لا شريك له، له الملك وله الحمد وهو على كل شيء قدير."
    ),
    "😴 أذكار النوم": (
        "😴 <b>أذكار النوم</b>\n\n"
        "• باسمك اللهم أموت وأحيا.\n"
        "• اللهم قني عذابك يوم تبعث عبادك.\n"
        "• قراءة آية الكرسي.\n"
        "• قراءة سورة الإخلاص والفلق والناس، مع النفث في الكفين ومسح ما استطاع من الجسد."
    ),
}

@azkar_dp.message(Command("start"))
async def azkar_start(message: Message):
    await message.answer(
        "السلام عليكم 🌿\nأهلًا بك في بوت الأذكار.\nاختر القسم من القائمة:",
        reply_markup=AZKAR_MENU,
    )

@azkar_dp.message(Command("help"))
async def azkar_help(message: Message):
    await message.answer("اختر قسم الأذكار من الأزرار الظاهرة أسفل الشاشة.", reply_markup=AZKAR_MENU)

@azkar_dp.message(F.text == "ℹ️ مساعدة")
async def azkar_help_button(message: Message):
    await message.answer("استخدم الأزرار لاختيار أذكار الصباح أو المساء أو بعد الصلاة أو النوم.", reply_markup=AZKAR_MENU)

@azkar_dp.message(F.text == "📿 تسبيح")
async def azkar_tasbeeh(message: Message):
    await message.answer("📿 سبحان الله، والحمد لله، ولا إله إلا الله، والله أكبر.\n\nيمكنك تكرار الذكر بالقدر الذي تستطيع.", reply_markup=AZKAR_MENU)

@azkar_dp.message(F.text.in_(list(AZKAR_TEXTS.keys())))
async def azkar_section(message: Message):
    await message.answer(AZKAR_TEXTS[message.text], reply_markup=AZKAR_MENU)

@azkar_dp.message()
async def azkar_fallback(message: Message):
    await message.answer("اختر قسمًا من القائمة بالأسفل 👇", reply_markup=AZKAR_MENU)

async def run_bot(bot: Bot, dp: Dispatcher, label: str):
    try:
        # Each bot must use its own token and must not be running elsewhere with polling.
        await bot.delete_webhook(drop_pending_updates=True)
        log.info("Starting polling: %s", label)
        await dp.start_polling(bot, handle_signals=False, close_bot_session=False)
    except Exception:
        log.exception("Bot stopped unexpectedly: %s", label)
        raise

async def main():
    runner = await start_web_server()
    tasks = [
        asyncio.create_task(run_bot(tiktok_bot, tiktok_dp, "TikTok")),
        asyncio.create_task(run_bot(azkar_bot, azkar_dp, "Azkar")),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runner.cleanup()
        await tiktok_bot.session.close()
        await azkar_bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
