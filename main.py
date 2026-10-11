# main.py
import asyncio
import json as _json
import logging
import os
import random
import re
import shutil
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    FSInputFile, InputMediaPhoto, InputMediaVideo,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("telegram-bots")

TIKTOK_TOKEN = os.getenv("TIKTOK_BOT_TOKEN", "").strip()
AZKAR_TOKEN  = os.getenv("AZKAR_BOT_TOKEN",  "").strip()
CHANNEL      = os.getenv("REQUIRED_CHANNEL", "@azkar201").strip()
TIMEZONE     = ZoneInfo("Asia/Dubai")

if not TIKTOK_TOKEN:
    raise RuntimeError("متغير Render مفقود: TIKTOK_BOT_TOKEN")
if not AZKAR_TOKEN:
    raise RuntimeError("متغير Render مفقود: AZKAR_BOT_TOKEN")

tiktok_bot = Bot(TIKTOK_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
azkar_bot  = Bot(AZKAR_TOKEN,  default=DefaultBotProperties(parse_mode=ParseMode.HTML))
tiktok_dp  = Dispatcher()
azkar_dp   = Dispatcher()

# ── خادم الفحص ────────────────────────────────────────────────────────────────
async def health(request: web.Request) -> web.Response:
    return web.Response(text="الخدمة تعمل.")

async def start_web_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/",       health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "10000"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("خادم الفحص يعمل على المنفذ %s", port)
    return runner

# ── مساعدات تيك توك ───────────────────────────────────────────────────────────
_REQ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.tiktok.com/",
}

def _clean_tiktok_url(url: str) -> str:
    """
    أزل معاملات التتبع (_r, _t, وما شابهها).
    tiktok.com/@user/video/ID  أو  tiktok.com/@user/photo/ID
    هذا هو الشكل الذي يقبله tikwm.
    """
    p = urlparse(url)
    clean = urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
    log.info("URL بعد التنظيف: %s", clean)
    return clean

async def _try_parse_json(resp) -> dict | None:
    """اقرأ نص الرد وحاول تحليله كـ JSON. أرجع None عند الفشل."""
    try:
        text = await resp.text()
    except Exception:
        return None
    if not text.strip():
        log.warning("tikwm: رد فارغ تمامًا (status=%s)", resp.status)
        return None
    log.info("tikwm رد خام (أول 300 حرف): %s", text[:300])
    try:
        return _json.loads(text)
    except Exception:
        log.warning("tikwm: الرد ليس JSON صالحًا")
        return None

async def _tikwm_fetch_info(session: aiohttp.ClientSession, url: str) -> dict:
    """
    جرّب عدة نقاط دخول لـ tikwm بالترتيب.
    أرجع dict البيانات أو ارفع RuntimeError.
    """
    clean_url = _clean_tiktok_url(url)
    timeout   = aiohttp.ClientTimeout(total=30)

    attempts = [
        # (method, endpoint, kwargs)
        ("POST", "https://www.tikwm.com/api/", {"data": {"url": clean_url, "hd": "1"}}),
        ("GET",  "https://www.tikwm.com/api/", {"params": {"url": clean_url, "hd": "1"}}),
        ("POST", "https://tikwm.com/api/",     {"data": {"url": clean_url, "hd": "1"}}),
        ("GET",  "https://tikwm.com/api/",     {"params": {"url": clean_url, "hd": "1"}}),
        # URL الأصلي (بمعاملاته) كآخر محاولة
        ("POST", "https://www.tikwm.com/api/", {"data": {"url": url, "hd": "1"}}),
    ]

    for method, endpoint, kwargs in attempts:
        log.info("tikwm محاولة: %s %s", method, endpoint)
        try:
            fn = session.post if method == "POST" else session.get
            async with fn(endpoint, timeout=timeout, **kwargs) as resp:
                body = await _try_parse_json(resp)
                if not body:
                    continue
                code = body.get("code")
                if code != 0:
                    log.warning("tikwm code=%s msg=%s", code, body.get("msg"))
                    continue
                data = body.get("data")
                if data:
                    log.info("tikwm نجح: %s %s", method, endpoint)
                    return data
        except Exception as exc:
            log.warning("tikwm فشل (%s %s): %s", method, endpoint, exc)

    raise RuntimeError(
        "tikwm: جميع المحاولات فشلت — تحقق من اللوج أعلاه لرؤية رد الخادم"
    )

async def _stream_to_file(session: aiohttp.ClientSession, src: str, dest: str) -> bool:
    try:
        async with session.get(src, timeout=aiohttp.ClientTimeout(total=90)) as resp:
            if resp.status != 200:
                log.warning("_stream_to_file: status=%s url=%s", resp.status, src)
                return False
            with open(dest, "wb") as fh:
                async for chunk in resp.content.iter_chunked(65_536):
                    fh.write(chunk)
        return True
    except Exception as exc:
        log.warning("_stream_to_file فشل (%s): %s", src, exc)
        return False

def _img_url(item) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return item.get("url") or (item.get("url_list") or [""])[0]
    return ""

async def download_tiktok(url: str, tmpdir: str) -> tuple[dict, list[Path]]:
    async with aiohttp.ClientSession(headers=_REQ_HEADERS) as session:
        data = await _tikwm_fetch_info(session, url)
        files: list[Path] = []
        is_slideshow = bool(data.get("images"))

        if is_slideshow:
            for i, item in enumerate(data["images"]):
                img_url = _img_url(item)
                if not img_url:
                    continue
                dest = str(Path(tmpdir) / f"slide_{i:03d}.jpg")
                if await _stream_to_file(session, img_url, dest):
                    files.append(Path(dest))
            music_url: str = (
                data.get("music")
                or (data.get("music_info") or {}).get("play", "")
            )
            if music_url:
                dest = str(Path(tmpdir) / "audio.mp3")
                if await _stream_to_file(session, music_url, dest):
                    files.append(Path(dest))
        else:
            for key in ("hdplay", "play", "wmplay"):
                video_url: str = data.get(key, "")
                if not video_url:
                    continue
                dest = str(Path(tmpdir) / "video.mp4")
                if await _stream_to_file(session, video_url, dest):
                    files.append(Path(dest))
                    break

        if not files:
            raise RuntimeError("لم يتم تنزيل أي ملف")

        return {
            "title":        data.get("title", "TikTok"),
            "description":  data.get("title", ""),
            "uploader":     (data.get("author") or {}).get("nickname", ""),
            "is_slideshow": is_slideshow,
        }, files

# ── التحقق من الاشتراك ────────────────────────────────────────────────────────
async def is_subscribed(user_id: int) -> bool:
    try:
        member = await tiktok_bot.get_chat_member(CHANNEL, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        log.exception("تعذر التحقق من الاشتراك.")
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

# ── معالجات بوت تيك توك ──────────────────────────────────────────────────────
@tiktok_dp.message(Command("start"))
async def tiktok_start(message: Message):
    if not await require_subscription(message):
        return
    await message.answer(
        "مرحبًا بك في بوت تحميل تيك توك.\n\n"
        "أرسل رابط منشور عام من تيك توك وسيقوم البوت بتحميل الفيديو "
        "أو الصور المتعددة مع الصوت إن وُجد."
    )

@tiktok_dp.message(Command("help"))
async def tiktok_help(message: Message):
    if await require_subscription(message):
        await message.answer(
            "طريقة الاستخدام:\n"
            "١. اشترك في القناة.\n"
            "٢. أرسل رابطًا عامًا من تيك توك.\n"
            "٣. انتظر اكتمال التحميل."
        )

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
        info, files = await download_tiktok(url, tmpdir)

        description = (info or {}).get("description") or (info or {}).get("title") or ""
        caption = ("وصف المنشور:\n" + str(description).strip())[:1000]
        if not caption.strip():
            caption = "تم تحميل المنشور بنجاح."

        photos_videos: list[tuple[str, Path]] = []
        audios: list[Path] = []

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

        if not photos_videos and not audios:
            raise RuntimeError("لم يتم العثور على وسائط قابلة للإرسال.")

        await status.edit_text("اكتمل التحميل، جارٍ إرسال الملفات…")

        for audio in audios:
            await message.answer_audio(FSInputFile(audio), caption="الصوت المستخرج من المنشور")

        for start in range(0, len(photos_videos), 10):
            chunk = photos_videos[start: start + 10]
            if len(chunk) == 1:
                kind, path = chunk[0]
                if kind == "photo":
                    await message.answer_photo(FSInputFile(path), caption=caption[:1024])
                else:
                    await message.answer_video(FSInputFile(path), caption=caption[:1024], supports_streaming=True)
            else:
                group = []
                for i, (kind, path) in enumerate(chunk):
                    cap = caption[:1024] if i == 0 else None
                    if kind == "photo":
                        group.append(InputMediaPhoto(media=FSInputFile(path), caption=cap))
                    else:
                        group.append(InputMediaVideo(media=FSInputFile(path), caption=cap, supports_streaming=True))
                await message.answer_media_group(group)

        await status.delete()

    except Exception as exc:
        log.warning("فشل تحميل تيك توك: %s", exc, exc_info=True)
        try:
            await status.edit_text(
                "تعذّر تحميل هذا الرابط.\n\n"
                "تأكد من أن المنشور عام وغير محذوف، ثم جرّب رابطًا آخر."
            )
        except Exception:
            pass
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

# ── بوت الأذكار ───────────────────────────────────────────────────────────────
AZKAR_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌅 أذكار الصباح"),     KeyboardButton(text="🌇 أذكار المساء")],
        [KeyboardButton(text="🕌 أذكار بعد الصلاة"), KeyboardButton(text="😴 أذكار النوم")],
        [KeyboardButton(text="📿 تسبيح"),             KeyboardButton(text="ℹ️ مساعدة")],
    ],
    resize_keyboard=True,
    input_field_placeholder="اختر قسم الأذكار",
)

AZKAR_TEXTS = {
    "🌅 أذكار الصباح": (
        "🌅 <b>أذكار الصباح</b>\n\n"
        "أصبحنا وأصبح الملك لله، والحمد لله، لا إله إلا الله وحده لا شريك له، "
        "له الملك وله الحمد، وهو على كل شيء قدير.\n\n"
        "اللهم بك أصبحنا، وبك أمسينا، وبك نحيا، وبك نموت، وإليك النشور.\n\n"
        "رضيت بالله ربًا، وبالإسلام دينًا، وبمحمد ﷺ نبيًا."
    ),
    "🌇 أذكار المساء": (
        "🌇 <b>أذكار المساء</b>\n\n"
        "أمسينا وأمسى الملك لله، والحمد لله، لا إله إلا الله وحده لا شريك له، "
        "له الملك وله الحمد، وهو على كل شيء قدير.\n\n"
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
        "السلام عليكم ورحمة الله وبركاته.\n"
        "أهلًا بك في بوت الأذكار. اختر القسم الذي ترغب فيه.",
        reply_markup=AZKAR_MENU,
    )

@azkar_dp.message(Command("help"))
@azkar_dp.message(F.text == "ℹ️ مساعدة")
async def azkar_help(message: Message):
    await message.answer("اختر قسم الأذكار من القائمة.", reply_markup=AZKAR_MENU)

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

# ── نشر الأذكار التلقائي ──────────────────────────────────────────────────────
TIME_WINDOWS = [
    ("الصباح", time(5,  0),  time(9,  0)),
    ("الظهر",  time(11, 30), time(13, 30)),
    ("العصر",  time(14, 30), time(16, 30)),
    ("المغرب", time(17, 30), time(19,  0)),
    ("العشاء", time(19, 30), time(22, 30)),
]

def random_time_for_day(day, start: time, end: time) -> datetime:
    start_dt = datetime.combine(day, start, tzinfo=TIMEZONE)
    end_dt   = datetime.combine(day, end,   tzinfo=TIMEZONE)
    secs     = int((end_dt - start_dt).total_seconds())
    return start_dt + timedelta(seconds=random.randint(0, secs))

async def automatic_azkar_poster():
    while True:
        now   = datetime.now(TIMEZONE)
        today = now.date()
        schedule = [
            (random_time_for_day(today, start, end), label)
            for label, start, end in TIME_WINDOWS
        ]
        schedule = [(w, l) for w, l in schedule if w > now]
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
                log.exception("تعذر نشر الذكر التلقائي.")
        tomorrow = datetime.combine(today + timedelta(days=1), time(0, 1), tzinfo=TIMEZONE)
        await asyncio.sleep(max(60, (tomorrow - datetime.now(TIMEZONE)).total_seconds()))

# ── تشغيل البوتات ─────────────────────────────────────────────────────────────
async def run_bot(bot: Bot, dp: Dispatcher, label: str):
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("بدء تشغيل بوت %s", label)
    try:
        await dp.start_polling(bot, handle_signals=False, close_bot_session=False)
    except asyncio.CancelledError:
        log.info("بوت %s أُوقف بشكل نظيف.", label)

async def main():
    runner = await start_web_server()
    tasks = [
        asyncio.create_task(run_bot(tiktok_bot, tiktok_dp, "تيك توك")),
        asyncio.create_task(run_bot(azkar_bot,  azkar_dp,  "الأذكار")),
        asyncio.create_task(automatic_azkar_poster()),
    ]
    try:
        await asyncio.gather(*tasks)
    except (asyncio.CancelledError, Exception) as exc:
        log.info("إيقاف التطبيق: %s", exc)
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
