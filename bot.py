import os
import sys
import logging
from pathlib import Path

# ضبط مخرجات الطرفية لتدعم UTF-8 والرموز التعبيرية على ويندوز
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

from downloader import extract_tiktok_url, download_tiktok_video, cleanup_file


class HealthHandler(BaseHTTPRequestHandler):
    """خادم ويب خفيف للاستجابة لفحوصات الصحة السحابية (Health Checks على Render وغيرها)."""
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("OK - TikTok Bot is running 24/7!".encode("utf-8"))

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()

    def log_message(self, format, *args):
        # منع إغراق السجلات بطلبات الفحص المتكررة
        pass


def start_health_server():
    """تشغيل خادم فحص الصحة على منفذ 10000 (الافتراضي لرندر) أو المنفذ المحدد."""
    port = int(os.getenv("PORT", 10000))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"🌐 خادم فحص الصحة يعمل على 0.0.0.0:{port}")
    except Exception as e:
        print(f"تنبيه بخصوص خادم الصحة: {e}")


async def keep_alive_ping():
    """إرسال طلب ذاتي كل 10 دقائق لضمان عدم نوم سيرفر رندر المجاني."""
    import asyncio
    url = os.getenv("RENDER_EXTERNAL_URL")
    if not url:
        return
    print(f"🔄 تفعيل النبض الذاتي لإبقاء السيرفر نشطاً 24/7 على: {url}")
    await asyncio.sleep(60)
    while True:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.get(url)
                print(f"💓 نبضة إبقاء البوت نشطاً نجحت (كود {res.status_code})")
        except Exception as e:
            pass
        await asyncio.sleep(10 * 60)



# إعداد التسجيل (Logging)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)

# تحميل المتغيرات من ملف .env
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """أمر البدء /start"""
    user_name = update.effective_user.first_name or "صديقي"
    welcome_message = (
        f"👋 أهلاً بك يا {user_name} في **بوت تحميل فيديوهات تيك توك**!\n\n"
        "✨ **المميزات:**\n"
        "• سحب الفيديوهات بأعلى جودة بدقة HD.\n"
        "• بدون علامة مائية نهائياً (No Watermark).\n"
        "• إرسال الفيديو كملف قابل للحفظ والمشاركة مباشرة.\n\n"
        "📥 **طريقة الاستخدام:**\n"
        "فقط أرسل أو شارك أي رابط فيديو من تيك توك هنا، وسأتولى الباقي فوراً! 🚀"
    )
    await update.message.reply_text(welcome_message, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """أمر المساعدة /help"""
    help_text = (
        "📖 **دليل الاستخدام:**\n\n"
        "1. افتح تطبيق تيك توك.\n"
        "2. اضغط على أيقونة المشاركة (Share) ثم اختر **نسخ الرابط (Copy Link)**.\n"
        "3. الصق الرابط هنا في المحادثة وأرسله.\n"
        "4. انتظر بضع ثوانٍ وسيصلك الفيديو جاهزاً!\n\n"
        "💡 يدعم البوت جميع صيغ الروابط:\n"
        "• `https://vm.tiktok.com/...`\n"
        "• `https://vt.tiktok.com/...`\n"
        "• `https://www.tiktok.com/@user/video/...`"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالجة الرسائل الواردة واستخراج فيديو تيك توك"""
    if not update.message or not update.message.text:
        return

    text = update.message.text
    tiktok_url = extract_tiktok_url(text)

    # إذا لم يكن هناك رابط تيك توك في محادثة خاصة، نوضح للمستخدم
    if not tiktok_url:
        if update.effective_chat.type == "private":
            await update.message.reply_text(
                "⚠️ لم يتم العثور على رابط تيك توك صالح في رسالتك.\n"
                "يرجى إرسال رابط صحيح يبدأ بـ tiktok.com",
                parse_mode="Markdown"
            )
        return

    # إرسال رسالة انتظار للمستخدم
    status_msg = await update.message.reply_text("⏳ **جاري معالجة وسحب الفيديو، يرجى الانتظار...**", parse_mode="Markdown")

    file_path = None
    try:
        # إشعار المستخدم بأن البوت يقوم برفع الفيديو
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO)

        # تحميل الفيديو
        download_result = await download_tiktok_video(tiktok_url)

        if download_result.get("status") != "success":
            err_msg = download_result.get("error_msg", "حدث خطأ غير معروف أثناء تحميل الفيديو.")
            await status_msg.edit_text(f"❌ **فشل التحميل:**\n{err_msg}", parse_mode="Markdown")
            return

        file_path = download_result["file_path"]
        title = download_result.get("title", "")
        author = download_result.get("author", "تيك توك")
        duration = download_result.get("duration", 0)

        # تنسيق وصف الفيديو
        caption_lines = []
        if title:
            # تقصير العنوان إذا كان طويلاً جداً ليتناسب مع كابشن تليغرام
            clean_title = title if len(title) <= 250 else title[:247] + "..."
            caption_lines.append(f"🎬 {clean_title}\n")
        if author:
            caption_lines.append(f"👤 **الناشر:** {author}")
        caption_lines.append("⚡ **بواسطة:** بوت تحميل تيك توك")

        caption = "\n".join(caption_lines)

        # رفع وإرسال الفيديو
        with open(file_path, "rb") as video_file:
            await update.message.reply_video(
                video=video_file,
                caption=caption,
                duration=duration,
                supports_streaming=True,
                parse_mode="Markdown"
            )

        # حذف رسالة الانتظار بعد اكتمال الإرسال بنجاح
        try:
            await status_msg.delete()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Error handling TikTok message: {e}", exc_info=True)
        await status_msg.edit_text(
            "❌ عذراً، حدث خطأ أثناء إرسال الفيديو إلى تليغرام. يرجى المحاولة مرة أخرى لاحقاً."
        )
    finally:
        # تنظيف وحذف الملف المؤقت لحماية مساحة القرص
        if file_path:
            cleanup_file(file_path)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالجة الأخطاء العامة لضمان عدم توقف البوت"""
    logger.error(f"Telegram Exception caught: {context.error}", exc_info=context.error)


def main():
    if not BOT_TOKEN or "YOUR_TELEGRAM_BOT_TOKEN" in BOT_TOKEN or BOT_TOKEN.strip() == "":
        print("\n" + "=" * 65)
        print("❌ تنبيه: لم يتم تعيين توكن البوت (TELEGRAM_BOT_TOKEN) حتى الآن!")
        print("يرجى فتح ملف .env وكتابة التوكن الخاص بك من @BotFather ثم إعادة التشغيل.")
        print("=" * 65 + "\n")
        sys.exit(1)

    print("🚀 جاري تشغيل بوت تحميل تيك توك...")
    start_health_server()

    async def on_startup(app):
        import asyncio
        asyncio.create_task(keep_alive_ping())

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .concurrent_updates(True)
        .build()
    )

    # تسجيل الأوامر والمعالجات
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    print("✅ البوت قيد العمل وجاهز لاستقبال الروابط 24/7!")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

