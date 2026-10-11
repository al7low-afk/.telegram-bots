import os
import aiohttp
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, InputMediaPhoto, InputMediaVideo
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("telegram-bots")

TIKTOK_TOKEN = os.getenv("TIKTOK_BOT_TOKEN", "").strip()
AZKAR_TOKEN = os.getenv("AZKAR_BOT_TOKEN", "").strip()

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

# ==================== بوت تيك توك (يعتمد على API خارجي مدعوم للفيديوهات والصور) ====================

@tiktok_dp.message(Command("start"))
async def tiktok_start(message: Message):
    await message.answer(
        "مرحبًا بك في بوت تحميل تيك توك 🚀\n\n"
        "أرسل أي رابط تيك توك (فيديو أو صور) وسأقوم بتحميله وإرساله لك فوراً بدقة عالية."
    )

@tiktok_dp.message(F.text)
async def tiktok_download(message: Message):
    url = (message.text or "").strip()
    if url.startswith("/"):
        return
    if "tiktok.com" not in url.lower():
        await message.answer("⚠️ يرجى إرسال رابط تيك توك صحيح.")
        return

    status = await message.answer("⏳ جارٍ المعالجة والتحميل...")

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
                    
                    images = item.get("images", [])
                    video_url = item.get("play", "")

                    if images:
                        await status.edit_text("جارٍ إرسال الصور...")
                        media_group = []
                        for i, img_url in enumerate(images[:10]):
                            caption = title[:1024] if i == 0 else None
                            media_group.append(InputMediaPhoto(media=img_url, caption=caption))
                        await message.answer_media_group(media_group)
                    elif video_url:
                        await status.edit_text("جارٍ إرسال الفيديو...")
                        await message.answer_video(video=video_url, caption=title[:1024], supports_streaming=True)
                    else:
                        await status.edit_text("❌ لم يتم العثور على وسائط لتحميلها.")
                        return

                    await status.delete()
                else:
                    await status.edit_text("❌ تعذّر جلب البيانات من هذا الرابط، تأكد أنه عام وغير محذوف.")
    except Exception as e:
        log.error("خطأ في التحميل: %s", e)
        try:
            await status.edit_text("❌ حدث خطأ أثناء التحميل، حاول مرة أخرى.")
        except Exception:
            pass

# ==================== بوت الأذكار ====================

@azkar_dp.message(Command("start"))
async def azkar_start(message: Message):
    await message.answer("أهلاً بك في بوت الأذكار 🕌\nسيتم إرسال الأذكار وتحديثاتها هنا بانتظام.")

@azkar_dp.message()
async def azkar_fallback(message: Message):
    await message.answer("جزاك الله خيراً! تابع الأذكار والقناة.")

# ==================== التشغيل الأساسي بدون تعارض ====================

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
