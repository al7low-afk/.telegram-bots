تشغيل بوت تيك توك وبوت الأذكار على Render

1) ارفع الملفات الثلاثة main.py و requirements.txt و render.yaml إلى مستودع GitHub.
2) في Render افتح خدمة Web Service المرتبطة بالمستودع.
3) من Settings > Build & Deploy:
   Build Command: pip install -r requirements.txt
   Start Command: python main.py
4) من Environment أضف متغيرين:
   TIKTOK_BOT_TOKEN = توكن بوت تيك توك الجديد
   AZKAR_BOT_TOKEN = توكن بوت الأذكار الجديد
   لا تضع التوكن داخل الكود ولا ترسله لأي شخص.
5) احفظ ثم اعمل Manual Deploy > Deploy latest commit.
6) افتح رابط خدمة Render؛ يجب أن يظهر:
   Telegram bots service is running.
7) جرّب /start في كل بوت على تيليجرام.

مهم:
- إذا سبق أن انكشف أي توكن في صورة أو محادثة، ألغِه من BotFather وأنشئ توكنًا جديدًا قبل وضعه في Render.
- لا تشغّل نسخة ثانية من نفس البوت بالتوكن نفسه في مكان آخر، لأن ذلك قد يسبب تعارض polling.
- خطة Render المجانية قد توقف الخدمة عند الخمول؛ لا يوجد ضمان تشغيل 24/7 مجانًا.
- تحميل تيك توك يعتمد على تغييرات تيك توك وyt-dlp؛ بعض الروابط قد تفشل، والملفات الكبيرة قد تتجاوز حد تيليجرام.
- نصوص الأذكار هنا مختصرة؛ راجع مصدرًا موثوقًا إذا كنت تريد نسخة موثقة كاملة مع التخريج.
