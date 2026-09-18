import os
import re
import uuid
import asyncio
import logging
from pathlib import Path
import httpx
import yt_dlp

logger = logging.getLogger(__name__)

DOWNLOAD_DIR = Path("temp_downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# حجم الفيديو الأقصى المسموح برفعه عبر بوت التليغرام العادي (50 ميغابايت)
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024

TIKTOK_URL_REGEX = re.compile(
    r"https?://(?:(?:www|vt|vm|m)\.)?tiktok\.com/[^\s]+"
)


def extract_tiktok_url(text: str) -> str | None:
    """استخراج أول رابط تيك توك موجود في النص المرسل."""
    if not text:
        return None
    match = TIKTOK_URL_REGEX.search(text)
    if match:
        url = match.group(0)
        # إزالة أي علامات ترقيم زائدة في نهاية الرابط إن وجدت
        return url.rstrip(".,;!?)\"'")
    return None


async def download_tiktok_tikwm(url: str) -> dict | None:
    """
    تحميل الفيديو بدون علامة مائية وبأعلى سرعة عبر واجهة TikWM المجانية.
    """
    api_url = "https://www.tikwm.com/api/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=25.0) as client:
            response = await client.post(api_url, data={"url": url, "hd": 1})
            if response.status_code != 200:
                return None

            data = response.json()
            if data.get("code") != 0 or "data" not in data:
                return None

            video_data = data["data"]
            # رابط الفيديو بدون علامة مائية
            video_url = video_data.get("play") or video_data.get("hdplay") or video_data.get("wmplay")
            if not video_url:
                return None

            # استخراج تفاصيل الفيديو
            title = video_data.get("title", "").strip()
            author_info = video_data.get("author", {})
            author_name = author_info.get("nickname") or author_info.get("unique_id") or "تيك توك"
            duration = video_data.get("duration", 0)

            # تنزيل ملف الفيديو
            unique_filename = f"{uuid.uuid4().hex}.mp4"
            target_path = DOWNLOAD_DIR / unique_filename

            async with client.stream("GET", video_url, follow_redirects=True) as stream_resp:
                if stream_resp.status_code != 200:
                    return None

                downloaded_size = 0
                with open(target_path, "wb") as f:
                    async for chunk in stream_resp.aiter_bytes(chunk_size=1024 * 64):
                        downloaded_size += len(chunk)
                        if downloaded_size > MAX_FILE_SIZE_BYTES:
                            f.close()
                            if target_path.exists():
                                target_path.unlink()
                            return {
                                "status": "error",
                                "error_msg": "حجم الفيديو يتجاوز الحد الأقصى المسموح به في تليغرام (50 ميغابايت)."
                            }
                        f.write(chunk)

            return {
                "status": "success",
                "file_path": str(target_path),
                "title": title,
                "author": author_name,
                "duration": duration
            }

    except Exception as e:
        logger.warning(f"TikWM download failed: {e}, falling back to yt-dlp...")
        return None


def _download_with_ytdlp_sync(url: str) -> dict:
    """
    طريقة احتياطية باستخدام yt-dlp لسحب الفيديو إذا تعذر الـ API المباشر.
    """
    unique_id = uuid.uuid4().hex
    out_template = str(DOWNLOAD_DIR / f"{unique_id}.%(ext)s")

    ydl_opts = {
        "outtmpl": out_template,
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "max_filesize": MAX_FILE_SIZE_BYTES,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

            # التأكد من الامتداد الفعلي للملف
            file_path = Path(filename)
            if not file_path.exists():
                # محاولة البحث بالـ id
                candidates = list(DOWNLOAD_DIR.glob(f"{unique_id}.*"))
                if candidates:
                    file_path = candidates[0]
                else:
                    return {"status": "error", "error_msg": "تعذر حفظ الفيديو على السيرفر."}

            title = info.get("title", "")
            author = info.get("uploader") or info.get("creator") or "تيك توك"
            duration = info.get("duration", 0)

            return {
                "status": "success",
                "file_path": str(file_path),
                "title": title,
                "author": author,
                "duration": int(duration) if duration else 0
            }
    except yt_dlp.utils.DownloadError as e:
        err_str = str(e)
        if "File is larger than max-filesize" in err_str:
            return {"status": "error", "error_msg": "حجم الفيديو يتجاوز 50 ميغابايت (الحد الأقصى لتليغرام)."}
        return {"status": "error", "error_msg": "تعذر تحميل الفيديو، قد يكون محذوفاً أو خاصاً."}
    except Exception as e:
        logger.error(f"yt-dlp error: {e}")
        return {"status": "error", "error_msg": "حدث خطأ غير متوقع أثناء تحميل الفيديو."}


async def download_tiktok_video(url: str) -> dict:
    """
    الدالة الرئيسية لتحميل فيديو تيك توك:
    تحاول أولاً عبر TikWM للحصول على فيديو بدون علامة مائية،
    وفي حال الفشل تعتمد على yt-dlp كخيار احتياطي موثوق.
    """
    # 1. المحاولة عبر TikWM (بدون علامة مائية وفائق السرعة)
    result = await download_tiktok_tikwm(url)
    if result:
        return result

    # 2. في حال فشل الـ API المباشر، اللجوء لـ yt-dlp
    logger.info("TikWM didn't return a result, trying yt-dlp...")
    return await asyncio.to_thread(_download_with_ytdlp_sync, url)


def cleanup_file(file_path: str | None) -> None:
    """حذف الملف المؤقت بأمان بعد إرساله لتوفير المساحة."""
    if not file_path:
        return
    try:
        p = Path(file_path)
        if p.exists():
            p.unlink()
    except Exception as e:
        logger.error(f"Error cleaning up file {file_path}: {e}")
