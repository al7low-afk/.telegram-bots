
import asyncio
import html
import logging
import os
import random
import re
import shutil
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import yt_dlp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
    InputMediaPhoto,
    InputMediaVideo,
)
from aiohttp import web


# =========================================================
# الإعدادات
# =========================================================

TIKTOK_BOT_TOKEN = os.getenv("TIKTOK_BOT_TOKEN", "").strip()
AZKAR_BOT_TOKEN = os.getenv("AZKAR_BOT_TOKEN", "").strip()
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "@azkar201").strip()

TIMEZONE = ZoneInfo("Asia/Dubai")
PORT = int(os.getenv("PORT", "10000"))

MAX_FILE_SIZE = 49 * 1024 * 1024
DOWNLOAD_TIMEOUT = 180

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("telegram-bots")

if not TIKTOK_BOT_TOKEN or not AZKAR_BOT_TOKEN:
    raise RuntimeError(
        "يرجى إضافة TIKTOK_BOT_TOKEN و AZKAR_BOT_TOKEN "
        "في Environment داخل Render."
    )

tiktok_bot = Bot(
    token=TIKTOK_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
azkar_bot = Bot(
    token=AZKAR_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

tiktok_dp = Dispatcher()
azkar_dp = Dispatcher()


# =========================================================
# خادم الصحة الخاص بـ Render
# =========================================================

async def health(request):
    return web.json_response({
        "status": "ok",
        "service": "telegram-bots",
        "time": datetime.now(TIMEZONE).isoformat(),
    })


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info("Health server started on port %s", PORT)
    return runner


# =========================================================
# بوت تحميل تيك توك
# =========================================================

JOIN_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="الاشتراك في القناة",
                url="https://t.me/azkar201",
            )
        ],
        [
            InlineKeyboardButton(
                text="تحقق من الاشتراك",
                callback_data="check_membership",
            )
        ],
    ]
)


def is_tiktok_url(text):
    if not text:
        return False

    return bool(
        re.search(
            r"https?://(?:www\.|m\.|vm\.|vt\.)?tiktok\.com/\S+",
            text,
            re.IGNORECASE,
        )
    )


async def is_subscribed(user_id):
    try:
        member = await tiktok_bot.get_chat_member(
            chat_id=REQUIRED_CHANNEL,
            user_id=user_id,
        )
        return member.status in ("creator", "administrator", "member")
    except Exception as exc:
        logger.warning("Membership check failed: %s", exc)
        return False


@tiktok_dp.message(CommandStart())
async def tiktok_start(message: Message):
    if not await is_subscribed(message.from_user.id):
        await message.answer(
            "أهلًا بك في بوت تحميل تيك توك.\n\n"
            "يرجى الاشتراك في قناة الأذكار أولًا، ثم اضغط "
            "«تحقق من الاشتراك».\n\n"
            "بعد التحقق، أرسل رابط فيديو أو منشور تيك توك.",
            reply_markup=JOIN_KEYBOARD,
        )
        return

    await message.answer(
        "أهلًا بك في بوت تحميل تيك توك.\n\n"
        "أرسل رابط الفيديو أو منشور الصور، وسأحاول تحميل المحتوى "
        "المتاح وإرساله إليك.\n\n"
        "تأكد من أن الرابط عام ويمكن الوصول إليه."
    )


@tiktok_dp.message(Command("help"))
async def tiktok_help(message: Message):
    await message.answer(
        "طريقة الاستخدام:\n"
        "1. اشترك في القناة المطلوبة.\n"
        "2. أرسل رابط تيك توك العام.\n"
        "3. انتظر حتى تنتهي محاولة التحميل.\n\n"
        "قد لا تعمل بعض الروابط الخاصة أو المحذوفة، "
        "وقد تتعذر بعض المنشورات التي تتطلب تسجيل الدخول."
    )


@tiktok_dp.callback_query(F.data == "check_membership")
async def check_membership(callback: CallbackQuery):
    if await is_subscribed(callback.from_user.id):
        await callback.answer("تم التحقق من اشتراكك.", show_alert=True)
        if callback.message:
            await callback.message.answer(
                "تم التحقق بنجاح. أرسل الآن رابط تيك توك."
            )
    else:
        await callback.answer(
            "لم أتمكن من التحقق من الاشتراك. تأكد من الاشتراك "
            "ومن صلاحيات البوت في القناة.",
            show_alert=True,
        )


def extract_files(directory):
    allowed = {
        ".mp4", ".mov", ".webm", ".mkv",
        ".jpg", ".jpeg", ".png", ".webp",
        ".mp3", ".m4a", ".aac", ".ogg",
    }

    files = []
    for path in Path(directory).rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in allowed:
            continue
        if path.stat().st_size > MAX_FILE_SIZE:
            logger.warning("Skipping oversized file: %s", path.name)
            continue
        files.append(path)

    return sorted(files)


async def download_with_ytdlp(url, directory):
    output_template = str(Path(directory) / "%(autonumber)03d_%(id)s.%(ext)s")

    options = {
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "fragment_retries": 2,
        "socket_timeout": 25,
        "max_filesize": MAX_FILE_SIZE,
        "restrictfilenames": True,
        "format": "best[ext=mp4]/best",
        "merge_output_format": "mp4",
    }

    def run_download():
        with yt_dlp.YoutubeDL(options) as downloader:
            downloader.download([url])

    await asyncio.wait_for(
        asyncio.to_thread(run_download),
        timeout=DOWNLOAD_TIMEOUT,
    )

    return extract_files(directory)


async def download_with_tikwm(url, directory):
    """
    محاولة احتياطية عبر واجهة TikWM العامة.
    قد تتغير الواجهة أو تتوقف؛ لذلك لا يعتمد عليها البوت وحدها.
    """
    api_url = "https://www.tikwm.com/api/"

    timeout = aiohttp.ClientTimeout(total=45)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            api_url,
            data={"url": url, "hd": "1"},
            headers={"User-Agent": "Mozilla/5.0"},
        ) as response:
            response.raise_for_status()
            data = await response.json(content_type=None)

        if not isinstance(data, dict) or data.get("code") != 0:
            raise RuntimeError("لم تُرجع خدمة التحميل بيانات صالحة.")

        item = data.get("data") or {}
        downloaded = []

        async def save_remote(remote_url, filename):
            if not remote_url:
                return None

            destination = Path(directory) / filename

            async with session.get(
                remote_url,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as response:
                response.raise_for_status()

                size = 0
                with destination.open("wb") as output:
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        size += len(chunk)
                        if size > MAX_FILE_SIZE:
                            output.close()
                            destination.unlink(missing_ok=True)
                            raise RuntimeError("الملف أكبر من الحد المسموح.")
                        output.write(chunk)

            return destination

        images = item.get("images") or []

        if images:
            for index, image_url in enumerate(images, start=1):
                saved = await save_remote(
                    image_url,
                    f"photo_{index:03d}.jpg",
                )
                if saved:
                    downloaded.append(saved)
        else:
            video_url = item.get("play") or item.get("hdplay")
            if video_url:
                saved = await save_remote(video_url, "tiktok_video.mp4")
                if saved:
                    downloaded.append(saved)

        music_url = item.get("music")
        if music_url:
            try:
                saved = await save_remote(music_url, "tiktok_audio.mp3")
                if saved:
                    downloaded.append(saved)
            except Exception:
                logger.info("Audio download unavailable.")

        return downloaded, item


async def send_downloaded_media(message, files, metadata=None):
    if not files:
        await message.answer(
            "تعذّر العثور على ملفات قابلة للإرسال لهذا الرابط.\n"
            "قد يكون المنشور خاصًا أو غير مدعوم أو أن خدمة التحميل "
            "لم تتمكن من الوصول إليه."
        )
        return

    metadata = metadata or {}
    title = metadata.get("title") or metadata.get("desc") or ""
    caption = html.escape(str(title).strip())[:900]

    photos = [
        file for file in files
        if file.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    ]
    videos = [
        file for file in files
        if file.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}
    ]
    audios = [
        file for file in files
        if file.suffix.lower() in {".mp3", ".m4a", ".aac", ".ogg"}
    ]

    # إرسال الصور في مجموعات لا تتجاوز 10 عناصر.
    for start in range(0, len(photos), 10):
        group = photos[start:start + 10]

        if len(group) == 1:
            await message.answer_photo(
                FSInputFile(group[0]),
                caption=caption or None,
            )
        else:
            media = []
            for index, path in enumerate(group):
                media.append(
                    InputMediaPhoto(
                        media=FSInputFile(path),
                        caption=(caption if start == 0 and index == 0 else None),
                    )
                )
            await message.answer_media_group(media)

    for index, video in enumerate(videos):
        await message.answer_video(
            FSInputFile(video),
            caption=caption if index == 0 and not photos else None,
            supports_streaming=True,
        )

    for audio in audios:
        await message.answer_audio(FSInputFile(audio))


@tiktok_dp.message(F.text)
async def handle_tiktok_link(message: Message):
    text = (message.text or "").strip()

    if not is_tiktok_url(text):
        await message.answer(
            "أرسل رابط تيك توك صحيحًا، أو استخدم /help لمعرفة طريقة الاستخدام."
        )
        return

    if not await is_subscribed(message.from_user.id):
        await message.answer(
            "يلزم الاشتراك في القناة قبل استخدام البوت.",
            reply_markup=JOIN_KEYBOARD,
        )
        return

    status_message = await message.answer(
        "جارٍ فحص الرابط ومحاولة تحميل المحتوى، يرجى الانتظار..."
    )

    with tempfile.TemporaryDirectory(prefix="tiktok_") as temp_dir:
        try:
            files = await download_with_ytdlp(text, temp_dir)
            metadata = {}

            if files:
                await send_downloaded_media(message, files, metadata)
            else:
                raise RuntimeError("لم يُنتج yt-dlp أي ملفات.")

        except Exception as first_error:
            logger.warning("yt-dlp failed: %s", first_error)

            # حذف الملفات الجزئية قبل تجربة الخدمة الاحتياطية.
            for item in Path(temp_dir).iterdir():
                try:
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                except OSError:
                    pass

            try:
                files, metadata = await download_with_tikwm(text, temp_dir)
                await send_downloaded_media(message, files, metadata)

            except Exception as second_error:
                logger.exception("All TikTok download methods failed: %s", second_error)
                await message.answer(
                    "تعذّر تحميل هذا المنشور حاليًا.\n\n"
                    "جرّب رابطًا عامًا آخر، أو حدّث yt-dlp، "
                    "أو أعد المحاولة لاحقًا."
                )

    try:
        await status_message.delete()
    except Exception:
        pass


@tiktok_dp.message()
async def tiktok_other_messages(message: Message):
    await message.answer(
        "أرسل رابط تيك توك في رسالة نصية، أو استخدم /help."
    )


# =========================================================
# بوت الأذكار
# =========================================================

AZKAR = [
    "سبحان الله وبحمده، سبحان الله العظيم.",
    "أستغفر الله وأتوب إليه.",
    "لا إله إلا الله وحده لا شريك له، له الملك وله الحمد، وهو على كل شيء قدير.",
    "سبحان الله، والحمد لله، ولا إله إلا الله، والله أكبر.",
    "اللهم صلِّ وسلم على نبينا محمد.",
    "ربنا آتنا في الدنيا حسنة وفي الآخرة حسنة وقنا عذاب النار.",
    "حسبي الله لا إله إلا هو عليه توكلت وهو رب العرش العظيم.",
    "اللهم أعنّا على ذكرك وشكرك وحسن عبادتك.",
    "اللهم اغفر لنا وارحمنا واهدنا وعافنا وارزقنا.",
    "لا حول ولا قوة إلا بالله.",
    "رضيت بالله ربًا، وبالإسلام دينًا، وبمحمد صلى الله عليه وسلم نبيًا.",
    "اللهم إني أسألك العفو والعافية في الدنيا والآخرة.",
]

AZKAR_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="ذكر آخر",
                callback_data="another_zikr",
            )
        ],
        [
            InlineKeyboardButton(
                text="قناة الأذكار",
                url="https://t.me/azkar201",
            )
        ],
    ]
)


@azkar_dp.message(CommandStart())
async def azkar_start(message: Message):
    await message.answer(
        "مرحبًا بك في بوت الأذكار.\n\n"
        "يمكنك طلب ذكر، أو متابعة الأذكار المنشورة في القناة.\n"
        "استخدم /zikr للحصول على ذكر.",
        reply_markup=AZKAR_KEYBOARD,
    )


@azkar_dp.message(Command("zikr"))
async def send_zikr_command(message: Message):
    zikr = random.choice(AZKAR)
    await message.answer(
        f"🌿 <b>ذِكر</b>\n\n{html.escape(zikr)}",
        reply_markup=AZKAR_KEYBOARD,
    )


@azkar_dp.callback_query(F.data == "another_zikr")
async def another_zikr(callback: CallbackQuery):
    zikr = random.choice(AZKAR)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"🌿 <b>ذِكر</b>\n\n{html.escape(zikr)}",
            reply_markup=AZKAR_KEYBOARD,
        )


@azkar_dp.message()
async def azkar_other_messages(message: Message):
    await message.answer(
        "للحصول على ذكر، أرسل /zikr."
    )


# =========================================================
# النشر التلقائي للأذكار
# =========================================================

# نختار وقتًا عشوائيًا واحدًا داخل كل فترة يومية بتوقيت الإمارات.
DAILY_WINDOWS = [
    ("الصباح", time(5, 0), time(9, 0)),
    ("الضحى", time(11, 30), time(13, 30)),
    ("العصر", time(14, 30), time(16, 30)),
    ("المساء", time(17, 30), time(19, 0)),
    ("الليل", time(19, 30), time(22, 30)),
]


def random_time_in_window(start, end):
    today = datetime.now(TIMEZONE).date()

    start_dt = datetime.combine(today, start, tzinfo=TIMEZONE)
    end_dt = datetime.combine(today, end, tzinfo=TIMEZONE)

    total_seconds = int((end_dt - start_dt).total_seconds())
    offset = random.randint(0, max(total_seconds, 0))

    return start_dt + timedelta(seconds=offset)


async def post_zikr(window_name):
    zikr = random.choice(AZKAR)

    text = (
        f"🌿 <b>ذكر {html.escape(window_name)}</b>\n\n"
        f"{html.escape(zikr)}\n\n"
        "اللهم اجعل ألسنتنا عامرة بذكرك."
    )

    await azkar_bot.send_message(
        chat_id=REQUIRED_CHANNEL,
        text=text,
    )

    logger.info("Posted %s zikr", window_name)


async def azkar_scheduler():
    logger.info("Azkar scheduler started.")

    while True:
        now = datetime.now(TIMEZONE)
        scheduled = []

        for name, start, end in DAILY_WINDOWS:
            run_at = random_time_in_window(start, end)

            # إذا دخلنا الفترة الحالية بعد الوقت العشوائي، نتجاوزها
            # بدل نشر أذكار متأخرة دفعة واحدة.
            if run_at <= now:
                continue

            scheduled.append((run_at, name))

        if not scheduled:
            tomorrow = datetime.combine(
                now.date() + timedelta(days=1),
                time(0, 5),
                tzinfo=TIMEZONE,
            )
            await asyncio.sleep(max(1, (tomorrow - now).total_seconds()))
            continue

        scheduled.sort(key=lambda item: item[0])

        for run_at, name in scheduled:
            now = datetime.now(TIMEZONE)
            delay = (run_at - now).total_seconds()

            if delay > 0:
                await asyncio.sleep(delay)

            try:
                await post_zikr(name)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to post scheduled zikr.")

        # انتظر حتى اليوم التالي قبل اختيار أوقات جديدة.
        now = datetime.now(TIMEZONE)
        tomorrow = datetime.combine(
            now.date() + timedelta(days=1),
            time(0, 5),
            tzinfo=TIMEZONE,
        )
        await asyncio.sleep(max(1, (tomorrow - now).total_seconds()))


# =========================================================
# تشغيل البوتين
# =========================================================

async def main():
    runner = await start_web_server()

    logger.info("Starting TikTok bot polling.")
    logger.info("Starting Azkar bot polling.")

    tasks = [
        asyncio.create_task(
            tiktok_dp.start_polling(
                tiktok_bot,
                handle_signals=False,
                close_bot_session=False,
            )
        ),
        asyncio.create_task(
            azkar_dp.start_polling(
                azkar_bot,
                handle_signals=False,
                close_bot_session=False,
            )
        ),
        asyncio.create_task(azkar_scheduler()),
    ]

    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)
        await runner.cleanup()

        await tiktok_bot.session.close()
        await azkar_bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bots stopped.")
