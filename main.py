import asyncio
import logging
import os
import random
import shutil
import tempfile
from pathlib import Path
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    FSInputFile, InputMediaPhoto, InputMediaVideo
)
import yt_dlp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("telegram-bots")

TIKTOK_TOKEN = os.getenv("TIKTOK_BOT_TOKEN", "").strip()
AZKAR_TOKEN = os.getenv("AZKAR_BOT_TOKEN", "").strip()
CHANNEL = os.getenv("REQUIRED_CHANNEL", "@azkar201").strip()

if not TIKTOK_TOKEN:
    raise RuntimeError("متغير Render مفقود: TIKTOK_BOT_TOKEN")
if not AZKAR_TOKEN:
    raise RuntimeError("متغير Render مفقود: AZKAR_BOT_TOKEN")

tiktok_bot = Bot(TIKTOK_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
azkar_bot = Bot(AZKAR_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
tiktok_dp = Dispatcher()
azkar_dp = Dispatcher()

# سيرفر الويب لإرضاء منصة Render
async def health(request: web.Request) -> web.Response:
    return web.Response(text="الخدمة تعمل بنجاح.")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "10000"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("خادم الفحص يعمل على المنفذ %s", port)
    return runner

# ==================== بوت تيك توك الجاهز والمستقر ====================

@tiktok_dp.message(Command("start"))
async def tiktok_start(message: Message):
    await message.answer(
        "مرحبًا بك في بوت تحميل تيك توك 🚀\n\n"
        "أرسل أي رابط تيك توك وسأقوم بتحميله وإرساله لك فوراً بدقة عالية."
    )

def download_tiktok_media(url: str, folder: str):
    ydl_opts = {
        "outtmpl": str(Path(folder) / "%(id)s.%(ext)s"),
        "format": "best",
        "noplaylist": False,
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    
    files = sorted([p for p in Path(folder).iterdir() if p.is_file()])
    return info, files

@tiktok_dp.message(F.text)
async def tiktok_download(message: Message):
    url = (message.text or "").strip()
    if url.startswith("/"):
        return
    if "tiktok.com" not in url.lower():
        await message.answer("⚠️ يرجى إرسال رابط تيك توك صحيح.")
        return

    status = await message.answer("⏳ جارٍ التحميل...")
    tmpdir = tempfile.mkdtemp(prefix="tiktok_")
    
    try:
        loop = asyncio.get_running_loop()
        info, files = await loop.run_in_executor(None, download_tiktok_media, url, tmpdir)
        
        if not files:
            await status.edit_text("❌ لم يتم العثور على ملفات لتحميلها.")
            return

        title = info.get("title", "تم التحميل بنجاح 🚀")
        
        # إرسال الملفات المحملة
        for file_path in files:
            ext = file_path.suffix.lower()
            if ext in (".mp4", ".mov", ".mkv"):
                await message.answer_video(FSInputFile(file_path), caption=title[:1024], supports_streaming=True)
            elif ext in (".jpg", ".jpeg", ".png", ".webp"):
                await message.answer_photo(FSInputFile(file_path), caption=title[:1024])
            else:
                await message.answer_document(FSInputFile(file_path), caption=title[:1024])

        await status.delete()
    except Exception as e:
        log.error("خطأ أثناء التحميل: %s", e)
        try:
            await status.edit_text("❌ حدث خطأ أثناء التحميل، تأكد أن الرابط عام وصحيح.")
        except Exception:
            pass
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ==================== بوت الأذكار ====================

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
    "🌅 أذكار الصباح": "🌅 أذكار الصباح:\nأصبحنا وأصبح الملك لله، والحمد لله، لا إله إلا الله وحده لا شريك له.",
    "🌇 أذكار المساء": "🌇 أذكار المساء:\nأمسينا وأمسى الملك لله، والحمد لله، لا إله إلا الله وحده لا شريك له.",
    "🕌 أذكار بعد الصلاة": "🕌 أذكار بعد الصلاة:\nأستغفر الله (3 مرات)، اللهم أنت السلام ومنك السلام تباركت يا ذا الجلال والإكرام.",
    "😴 أذكار النوم": "😴 أذكار النوم:\nباسمك اللهم أموت وأحيا، وقراءة آية الكرسي.",
}

@azkar_dp.message(Command("start"))
async def azkar_start(message: Message):
    await message.answer("أهلاً بك في بوت الأذكار 🕌\nاختر القسم المناسب:", reply_markup=AZKAR_MENU)

@azkar_dp.message(F.text.in_(list(AZKAR_TEXTS.keys())))
async def azkar_section(message: Message):
    await message.answer(AZKAR_TEXTS[message.text], reply_markup=AZKAR_MENU)

@azkar_dp.message(F.text == "📿 تسبيح")
async def azkar_tasbeeh(message: Message):
    await message.answer("📿 سبحان الله، والحمد لله، ولا إله إلا الله، والله أكبر.", reply_markup=AZKAR_MENU)

@azkar_dp.message()
async def azkar_fallback(message: Message):
    await message.answer("اختر من القائمة أدناه:", reply_markup=AZKAR_MENU)


# ==================== التشغيل الأساسي ====================

async def run_bot(bot: Bot, dp: Dispatcher, label: str):
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("بدء تشغيل بوت %s", label)
    await dp.start_polling(bot, handle_signals=False, close_bot_session=False)

async def main():
    runner = await start_web_server()
    tasks = [
        asyncio.create_task(run_bot(tiktok_bot, tiktok_dp, "تيك توك")),
        asyncio.create_task(run_bot(azkar_bot, azkar_dp, "الأذكار")),
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
