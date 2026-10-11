import os
import aiohttp
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, InputMediaPhoto, InputMediaVideo, FSInputFile
import logging

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

# خادم فحص الصحة لإرضاء منصة Render
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

# التحقق من الاشتراك الإجباري
async def is_subscribed(user_id: int) -> bool:
    try:
        member = await tiktok_bot.get_chat_member(CHANNEL, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return True # إذا حدث خطأ في التحقق نسمح للمستخدم بالاستمرار لتفادي التعطيل

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
        "أهلاً بك في بوت تحميل تيك توك 🚀\n\n"
        "أرسل أي رابط تيك توك (فيديو، صور، أو منشور) وسأقوم بتحميله وإرساله لك فوراً!"
    )

@tiktok_dp.message(F.text)
async def tiktok_download(message: Message):
    url = (message.text or "").strip()
    if url.startswith("/"):
        return
    if "tiktok.com" not in url.lower():
        await message.answer("⚠️ يرجى إرسال رابط تيك توك صحيح 🔗")
        return
    if not await require_subscription(message):
        return

    status = await message.answer("جاري المعالجة والتحميل... ⏳")

    try:
        api_url = f"https://www.tikwm.com/api/?url={url}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers=headers) as resp:
                data = await resp.json()

                if data.get("code") == 0:
                    item = data["data"]
                    title = item.get("title", "تم التحميل بنجاح 🚀")
                    
                    # التحقق إذا كان المنشور يحتوي على صور متعددة (Slideshow)
                    images = item.get("images", [])
                    video_url = item.get("play", "")
                    music_url = item.get("music", "")

                    if images:
                        await status.edit_text("جارٍ إرسال الصور...")
                        media_group = []
                        for i, img_url in enumerate(images[:10]): # أقصى حد 10 صور في المرة
                            caption = title[:1024] if i == 0 else None
                            media_group.append(InputMediaPhoto(media=img_url, caption=caption))
                        await message.answer_media_group(media_group)
                    
                    elif video_url:
                        await status.edit_text("جارٍ إرسال الفيديو...")
                        await message.answer_video(video=video_url, caption=title[:1024], supports_streaming=True)
                    
                    # إرسال الصوت المرفق إن وجد
                    if music_url:
                        try:
                            await message.answer_audio(audio=music_url, caption="الصوت المستخرج من المنشور 🎵")
                        except Exception:
                            pass

                    await status.delete()
                else:
                    await status.edit_text("❌ تعذّر جلب البيانات من هذا الرابط، تأكد أنه عام وغير محذوف.")
    except Exception as e:
        log.error("خطأ في التحميل: %s", e)
        try:
            await status.edit_text("❌ حدث خطأ أثناء التحميل، حاول مرة أخرى برابط آخر.")
        except Exception:
            pass

# --- بوت الأذكار ---
@azkar_dp.message(Command("start"))
async def azkar_start(message: Message):
    await message.answer("أهلاً بك في بوت الأذكار 🕌\nسيتم إرسال الأذكار اليومية هنا بانتظام.")

@azkar_dp.message()
async def azkar_fallback(message: Message):
    await message.answer("جزاك الله خيراً! تابع الأذكار والروابط في القناة.")

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
        asyncio.gather(*tasks)
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
