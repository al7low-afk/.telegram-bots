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

# خادم فحص الصحة المطلوب لخدمة Render Web Service
async def health(request: web.Request) -> web.Response:
    return web.Response(text="الخدمة تعمل.")

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
        return False

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
        "مرحبًا بك في بوت تحميل تيك توك.\n\n"
        "أرسل رابط منشور عام من تيك توك. سيحاول البوت تحميل الفيديو أو الصور المتعددة "
        "وإرفاق وصف المنشور، وإرسال الصوت إذا كان متاحًا.\n"
        "قد لا تعمل بعض الروابط الخاصة أو المحذوفة أو المحمية."
    )

@tiktok_dp.message(Command("help"))
async def tiktok_help(message: Message):
    if await require_subscription(message):
        await message.answer(
            "طريقة الاستخدام:\n"
            "١. اشترك في القناة.\n"
            "٢. أرسل رابطًا عامًا من تيك توك.\n"
            "٣. انتظر اكتمال التحميل.\n\n"
            "قد لا يتوفر الصوت الأصلي أو وصف المنشور في بعض الروابط."
        )

def download_tiktok(url: str, folder: str):
    opts = {
        "outtmpl": str(Path(folder) / "%(id)s_%(autonumber)03d.%(ext)s"),
        "format": "best[ext=mp4]/best",
        "noplaylist": False,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "max_filesize": 49 * 1024 * 1024,
        "extractor_retries": 2,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    files = sorted([p for p in Path(folder).iterdir() if p.is_file()])
    return info, files

@tiktok_dp.message(F.text)
async def tiktok_download(message: Message):
    url = (message.text or "").strip()
    if url.startswith("/"):
        return
    if "tiktok.com/" not in url.lower():
        await message.answer("يرجى إرسال رابط صحيح من تيك توك.")
        return
    if not await require_subscription(message):
        return

    status = await message.answer("جارٍ فحص الرابط ومحاولة تحميل الوسائط…")
    tmpdir = tempfile.mkdtemp(prefix="tiktok_")
    try:
        loop = asyncio.get_running_loop()
        info, files = await loop.run_in_executor(None, download_tiktok, url, tmpdir)
        if not files:
            raise RuntimeError("لم يتم تنزيل أي ملف.")
        description = (info or {}).get("description") or (info or {}).get("title") or ""
        caption = ("وصف المنشور:\n" + str(description).strip())[:1000]
        if not caption.strip():
            caption = "تم تحميل المنشور بنجاح."

        photos_videos, audios = [], []
        for path in files:
            if path.stat().st_size > 49 * 1024 * 1024:
                continue
            ext = path.suffix.lower()
            if ext in (".jpg", ".jpeg", ".png"):
                photos_videos.append(("photo", path))
            elif ext in (".mp4", ".mov", ".m4v", ".webm"):
                photos_videos.append(("video", path))
            elif ext in (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus"):
                audios.append(path)

        await status.edit_text("اكتمل التحميل، جارٍ إرسال الملفات…")
        for audio in audios:
            await message.answer_audio(FSInputFile(audio), caption="الصوت المستخرج من المنشور")

        if not photos_videos and not audios:
            raise RuntimeError("لم يتم العثور على وسائط قابلة للإرسال.")

        for start in range(0, len(photos_videos), 10):
            chunk = photos_videos[start:start + 10]
            if len(chunk) == 1:
                kind, path = chunk[0]
                if kind == "photo":
                    await message.answer_photo(FSInputFile(path), caption=caption[:1024])
                else:
                    await message.answer_video(
                        FSInputFile(path), caption=caption[:1024], supports_streaming=True
                    )
            elif chunk:
                group = []
                for i, (kind, path) in enumerate(chunk):
                    cap = caption[:1024] if i == 0 else None
                    if kind == "photo":
                        group.append(InputMediaPhoto(media=FSInputFile(path), caption=cap))
                    else:
                        group.append(InputMediaVideo(
                            media=FSInputFile(path), caption=cap, supports_streaming=True
                        ))
                await message.answer_media_group(group)
        await status.delete()
    except Exception as exc:
        log.warning("فشل تحميل تيك توك: %s", exc, exc_info=True)
        try:
            await status.edit_text(
                "تعذّر تحميل هذا الرابط.\n\n"
                "تأكد من أن المنشور عام وغير محذوف، ثم جرّب رابطًا آخر. "
                "قد يمنع تيك توك الوصول إلى بعض المنشورات."
            )
        except Exception:
            pass
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

# بوت الأذكار: قائمة الاستخدام اليدوي
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
        "اللهم بك أصبحنا، وبك أمسينا، وبك نحيا، وبك نموت، وإليك النشور.\n\n"
        "رضيت بالله ربًا، وبالإسلام دينًا، وبمحمد ﷺ نبيًا."
    ),
    "🌇 أذكار المساء": (
        "🌇 <b>أذكار المساء</b>\n\n"
        "أمسينا وأمسى الملك لله، والحمد لله، لا إله إلا الله وحده لا شريك له، له الملك وله الحمد، وهو على كل شيء قدير.\n\n"
        "اللهم بك أمسينا، وبك أصبحنا، وبك نحيا، وبك نموت، وإليك المصير.\n\n"
        "رضيت بالله ربًا، وبالإسلام دينًا، وبمحمد ﷺ نبيًا."
    ),
    "🕌 أذكار بعد الصلاة": (
        "🕌 <b>أذكار بعد الصلاة</b>\n\n"
        "أستغفر الله. (ثلاث مرات)\n"
        "اللهم أنت السلام، ومنك السلام، تباركت يا ذا الجلال والإكرام.\n"
        "سبحان الله (33 مرة)، والحمد لله (33 مرة)، والله أكبر (33 مرة)."
    ),
    "😴 أذكار النوم": (
        "😴 <b>أذكار النوم</b>\n\n"
        "باسمك اللهم أموت وأحيا.\n"
        "قراءة آية الكرسي.\n"
        "قراءة سور الإخلاص والفلق والناس، والنفث في الكفين ومسح ما استطاع من الجسد."
    ),
}
DAILY_AZKAR = [
    "سبحان الله وبحمده، سبحان الله العظيم.",
    "لا إله إلا الله وحده لا شريك له، له الملك وله الحمد، وهو على كل شيء قدير.",
    "أستغفر الله وأتوب إليه.",
    "اللهم صل وسلم على نبينا محمد.",
    "سبحان الله، والحمد لله، ولا إله إلا الله، والله أكبر.",
    "اللهم أعنّي على ذكرك وشكرك وحسن عبادتك.",
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
        "📿 سبحان الله، والحمد لله، ولا إله إلا الله، والله أكبر.\n"
        "يمكنك تكرار هذا الذكر بالقدر الذي تستطيع.",
        reply_markup=AZKAR_MENU,
    )

@azkar_dp.message(F.text.in_(list(AZKAR_TEXTS.keys())))
async def azkar_section(message: Message):
    await message.answer(AZKAR_TEXTS[message.text], reply_markup=AZKAR_MENU)

@azkar_dp.message()
async def azkar_fallback(message: Message):
    await message.answer("يرجى اختيار قسم من القائمة.", reply_markup=AZKAR_MENU)

# توقيت عشوائي واحد داخل كل فترة، بتوقيت الإمارات
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
                log.exception(
                    "تعذر نشر الذكر. تأكد أن بوت الأذكار مشرف في القناة ولديه صلاحية نشر الرسائل."
                )
        tomorrow = datetime.combine(today + timedelta(days=1), time(0, 1), tzinfo=TIMEZONE)
        await asyncio.sleep(max(60, (tomorrow - datetime.now(TIMEZONE)).total_seconds()))

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
