import asyncio
import logging
import os
import random
import shutil
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
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
TIMEZONE = ZoneInfo("Asia/Dubai")

if not TIKTOK_TOKEN:
    raise RuntimeError("متغير Render مفقود: TIKTOK_BOT_TOKEN")
if not AZKAR_TOKEN:
    raise RuntimeError("متغير Render مفقود: AZKAR_BOT_TOKEN")

tiktok_bot = Bot(TIKTOK_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
azkar_bot = Bot(AZKAR_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
tiktok_dp = Dispatcher()
azkar_dp = Dispatcher()

# خادم فحص الصحة لإرضاء منصة Render Web Service
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

# التحقق من اشتراك المستخدم في القناة
async def is_subscribed(user_id: int) -> bool:
    try:
        member = await tiktok_bot.get_chat_member(CHANNEL, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        log.exception("تعذر التحقق من الاشتراك. تأكد من أن بوت التحميل مشرف في القناة.")
        return True  # للسماح بالاستمرار إذا حدث خطأ تقني في فحص القناة

async def require_subscription(message: Message) -> bool:
    if not message.from_user:
        return False
    if await is_subscribed(message.from_user.id):
        return True
    await message.answer(
        "مرحبًا بك في بوت تحميل تيك توك.\n\n"
        f"يرجى الاشتراك في القناة أولًا: https://t.me/{CHANNEL.lstrip('@')}\n"
        "بعد الاشتراك، أرسل /start ثم أرسل رابط المنشور."
    )
    return False

@tiktok_dp.message(Command("start"))
async def tiktok_start(message: Message):
    if not await require_subscription(message):
        return
    await message.answer(
        "مرحبًا بك في بوت تحميل تيك توك 🚀\n\n"
        "أرسل رابط منشور عام من تيك توك وسأقوم بتحميل الفيديو، الصور، أو الصوت بدقة عالية."
    )

@tiktok_dp.message(Command("help"))
async def tiktok_help(message: Message):
    if await require_subscription(message):
        await message.answer(
            "طريقة الاستخدام:\n"
            "١. اشترك في القناة المطلوبة.\n"
            "٢. أرسل رابطًا عامًا من تيك توك (فيديو أو صور أو كُلّي).\n"
            "٣. انتظر لحظات ليكتمل التحميل والإرسال."
        )

# ==================== دوال تحميل تيك توك المحسّنة ====================

def build_ydl_opts(folder: str, *, for_audio_only: bool = False):
    """إعدادات yt-dlp موحّدة"""
    base = {
        "outtmpl": str(Path(folder) / "%(id)s_%(autonumber)03d.%(ext)s"),
        "noplaylist": False,          
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "extractor_retries": 3,
        "ignoreerrors": False,
        "writedescription": False,
        "writeinfojson": False,
    }
    if for_audio_only:
        base.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
    else:
        base["format"] = "bestvideo+bestaudio/best/best[ext=mp4]"
        base["merge_output_format"] = "mp4"
    return base

def download_tiktok(url: str, folder: str):
    """تحميل الفيديو/الصور/الصوت + استخراج الوصف"""
    info = None
    try:
        with yt_dlp.YoutubeDL(build_ydl_opts(folder)) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:
        log.warning("فشل التحميل الأساسي: %s", e)

    audio_file = None
    try:
        audio_folder = Path(folder) / "audio"
        audio_folder.mkdir(exist_ok=True)
        with yt_dlp.YoutubeDL(build_ydl_opts(str(audio_folder), for_audio_only=True)) as ydl:
            ydl.extract_info(url, download=True)
        mp3s = sorted(audio_folder.glob("*.mp3"))
        if mp3s:
            audio_file = mp3s[0]
    except Exception as e:
        log.info("لا يوجد صوت منفصل متاح: %s", e)

    files = sorted([
        p for p in Path(folder).iterdir()
        if p.is_file() and p.suffix.lower() not in (".json", ".description")
    ])
    if audio_file:
        files.append(audio_file)

    description = extract_description(info)
    return info, files, description

def extract_description(info) -> str:
    """استخراج الوصف بأفضل شكل ممكن من أي نوع منشور"""
    if not info:
        return ""
    if info.get("_type") == "playlist" and info.get("entries"):
        for entry in info["entries"]:
            if entry:
                d = entry.get("description") or entry.get("title") or ""
                if d:
                    return str(d).strip()
    for key in ("description", "title", "fulltitle", "uploader"):
        val = info.get(key)
        if val:
            return str(val).strip()
    return ""

def classify_files(files):
    """تصنيف الملفات إلى صور/فيديو/صوت"""
    photos, videos, audios = [], [], []
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size == 0 or size > 49 * 1024 * 1024:
            log.info("تم تجاهل ملف كبير/فارغ: %s", path)
            continue
        ext = path.suffix.lower()
        if ext in (".jpg", ".jpeg", ".png", ".webp"):
            photos.append(path)
        elif ext in (".mp4", ".mov", ".m4v", ".webm", ".mkv"):
            videos.append(path)
        elif ext in (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus"):
            audios.append(path)
    return photos, videos, audios

async def send_media_safely(message: Message, kind: str, path: Path, caption: str | None):
    """إرسال ملف واحد مع معالجة الأخطاء"""
    try:
        if kind == "photo":
            await message.answer_photo(FSInputFile(path), caption=caption)
        elif kind == "video":
            await message.answer_video(
                FSInputFile(path), caption=caption, supports_streaming=True
            )
        elif kind == "audio":
            await message.answer_audio(FSInputFile(path), caption=caption)
    except TelegramBadRequest as e:
        log.warning("فشل إرسال %s (%s): %s", kind, path.name, e)
        try:
            await message.answer_document(FSInputFile(path), caption=caption)
        except Exception as e2:
            log.error("فشل حتى كملف: %s", e2)

async def send_media_group_safely(message: Message, items, caption: str):
    """إرسال مجموعة وسائط مع تجزئة آمنة"""
    photos = [(k, p) for k, p in items if k == "photo"]
    videos = [(k, p) for k, p in items if k == "video"]

    for chunk_kind, chunk_list in (("photo", photos), ("video", videos)):
        for i in range(0, len(chunk_list), 10):
            chunk = chunk_list[i:i + 10]
            if len(chunk) == 1:
                k, p = chunk[0]
                cap = caption[:1024] if i == 0 else None
                await send_media_safely(message, k, p, cap)
                continue
            group = []
            for j, (k, p) in enumerate(chunk):
                cap = caption[:1024] if (i == 0 and j == 0) else None
                if k == "photo":
                    group.append(InputMediaPhoto(media=FSInputFile(p), caption=cap))
                else:
                    group.append(InputMediaVideo(
                        media=FSInputFile(p), caption=cap, supports_streaming=True
                    ))
            try:
                await message.answer_media_group(group)
            except TelegramBadRequest as e:
                log.warning("فشل media_group، سأرسل بشكل فردي: %s", e)
                for k, p in chunk:
                    await send_media_safely(message, k, p, None)

@tiktok_dp.message(F.text)
async def tiktok_download(message: Message):
    url = (message.text or "").strip()
    if url.startswith("/"):
        return
    if "tiktok.com/" not in url.lower() and "vm.tiktok.com" not in url.lower():
        await message.answer("⚠️ يرجى إرسال رابط تيك توك صحيح.")
        return
    if not await require_subscription(message):
        return

    status = await message.answer("⏳ جارٍ فحص الرابط وتحميل الوسائط…")
    tmpdir = tempfile.mkdtemp(prefix="tiktok_")
    try:
        loop = asyncio.get_running_loop()
        info, files, description = await loop.run_in_executor(
            None, download_tiktok, url, tmpdir
        )
        if not files:
            raise RuntimeError("لم يتم تنزيل أي ملف.")

        caption = (description or "").strip()
        caption = f"📝 {caption}" if caption else "تم تحميل المنشور بنجاح."

        photos, videos, audios = classify_files(files)
        if not (photos or videos or audios):
            raise RuntimeError("لم يتم العثور على وسائط قابلة للإرسال.")

        await status.edit_text("📤 اكتمل التحميل، جارٍ الإرسال…")

        for i, audio in enumerate(audios):
            cap = "🎵 الصوت المستخرج من المنشور" if i == 0 else None
            await send_media_safely(message, "audio", audio, cap)

        media_items = [("photo", p) for p in photos] + [("video", p) for p in videos]
        if media_items:
            await send_media_group_safely(message, media_items, caption[:1024])

        if not media_items and audios:
            await message.answer(caption[:4096])

        await status.delete()

    except Exception as exc:
        log.warning("فشل تحميل تيك توك: %s", exc, exc_info=True)
        try:
            await status.edit_text(
                "❌ تعذّر تحميل هذا الرابط.\n\n"
                "تأكد من أن المنشور:\n"
                "• عام وغير محذوف\n"
                "• الرابط صحيح من تيك توك\n\n"
                "ثم جرّب رابطًا آخر."
            )
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
    "🌅 أذكار الصباح": (
        "🌅 <b>أذكار الصباح</b>\n\n"
        "أصبحنا وأصبح الملك لله، والحمد لله، لا إله إلا الله وحده لا شريك له، له الملك وله الحمد، وهو على كل شيء قدير.\n\n"
        "اللهم بك أصبحنا، وبك أمسينا، وبك نحيا، وبك نموت، وإليك النشور."
    ),
    "🌇 أذكار المساء": (
        "🌇 <b>أذكار المساء</b>\n\n"
        "أمسينا وأمسى الملك لله، والحمد لله، لا إله إلا الله وحده لا شريك له، له الملك وله الحمد، وهو على كل شيء قدير.\n\n"
        "اللهم بك أمسينا، وبك أصبحنا، وبك نحيا، وبك نموت، وإليك المصير."
    ),
    "🕌 أذكار بعد الصلاة": (
        "🕌 <b>أذكار بعد الصلاة</b>\n\n"
        "أستغفر الله. (ثلاث مرات)\n"
        "اللهم أنت السلام، ومنك السلام، تباركت يا ذا الجلال والإكرام."
    ),
    "😴 أذكار النوم": (
        "😴 <b>أذكار النوم</b>\n\n"
        "باسمك اللهم أموت وأحيا.\n"
        "قراءة آية الكرسي وسور الإخلاص والفلق والناس."
    ),
}
DAILY_AZKAR = [
    "سبحان الله وبحمده، سبحان الله العظيم.",
    "لا إله إلا الله وحده لا شريك له، له الملك وله الحمد، وهو على كل شيء قدير.",
    "أستغفر الله وأتوب إليه.",
    "اللهم صل وسلم على نبينا محمد.",
]

@azkar_dp.message(Command("start"))
async def azkar_start(message: Message):
    await message.answer(
        "السلام عليكم ورحمة الله وبركاته.\nأهلًا بك في بوت الأذكار. اختر القسم الذي ترغب فيه.",
        reply_markup=AZKAR_MENU,
    )

@azkar_dp.message(Command("help"))
@azkar_dp.message(F.text == "ℹ️ مساعدة")
async def azkar_help(message: Message):
    await message.answer("اختر قسم الأذكار من القائمة الظاهرة أسفل الشاشة.", reply_markup=AZKAR_MENU)

@azkar_dp.message(F.text == "📿 تسبيح")
async def azkar_tasbeeh(message: Message):
    await message.answer(
        "📿 سبحان الله، والحمد لله، ولا إله إلا الله، والله أكبر.",
        reply_markup=AZKAR_MENU,
    )

@azkar_dp.message(F.text.in_(list(AZKAR_TEXTS.keys())))
async def azkar_section(message: Message):
    await message.answer(AZKAR_TEXTS[message.text], reply_markup=AZKAR_MENU)

@azkar_dp.message()
async def azkar_fallback(message: Message):
    await message.answer("يرجى اختيار قسم من القائمة.", reply_markup=AZKAR_MENU)

TIME_WINDOWS = [
    ("الصباح", time(5, 0), time(9, 0)),
    ("الظهر", time(11, 30), time(13, 30)),
    ("العصر", time(14, 30), time(16, 30)),
    ("المغرب", time(17, 30), time(19, 0)),
    ("العشاء", time(19, 30), time(22, 30)),
]

def random_time_for_day(day, start, end):
    start_dt = datetime.combine(day, start, tzinfo=TIMEZONE)
    end_dt = datetime.combine(day, end, tzinfo=TIMEZONE)
    seconds = int((end_dt - start_dt).total_seconds())
    return start_dt + timedelta(seconds=random.randint(0, seconds))

async def automatic_azkar_poster():
    while True:
        now = datetime.now(TIMEZONE)
        today = now.date()
        schedule = [
            (random_time_for_day(today, start, end), label)
            for label, start, end in TIME_WINDOWS
        ]
        schedule = [(when, label) for when, label in schedule if when > now]
        schedule.sort()
        for when, label in schedule:
            delay = (when - datetime.now(TIMEZONE)).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                await azkar_bot.send_message(
                    chat_id=CHANNEL,
                    text=f"📿 <b>ذكر اليوم — {label}</b>\n\n{random.choice(DAILY_AZKAR)}",
                )
                log.info("تم نشر ذكر تلقائي: %s", label)
            except Exception:
                log.exception("تعذر نشر الذكر في القناة.")
        tomorrow = datetime.combine(today + timedelta(days=1), time(0, 1), tzinfo=TIMEZONE)
        await asyncio.sleep(max(60, (tomorrow - datetime.now(TIMEZONE)).total_seconds()))


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
        asyncio.create_task(automatic_azkar_poster()),
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
