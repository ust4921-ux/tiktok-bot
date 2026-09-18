FROM python:3.12-slim

# منع بايثون من إنشاء ملفات pyc وضبط الإخراج غير المباشر
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# تثبيت متطلبات النظام الأساسية مثل ffmpeg في حال احتاجه yt-dlp
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# نسخ وتثبيت متطلبات بايثون
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ ملفات المشروع
COPY . .

# تشغيل البوت
CMD ["python", "bot.py"]
