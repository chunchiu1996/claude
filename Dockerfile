FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    TRUST_PROXY=1 SESSION_COOKIE_SECURE=1 \
    DATABASE=/data/shop.db UPLOAD_FOLDER=/data/uploads
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /data
VOLUME /data
EXPOSE 8000
# 2 processes x 4 threads. Long timeout so big spreadsheet / photo uploads finish (Cloudflare allows 100 s).
CMD ["sh", "-c", "gunicorn -w ${WEB_WORKERS:-2} -k gthread --threads 4 --timeout 120 --graceful-timeout 30 -b 0.0.0.0:${PORT:-8000} --access-logfile - wsgi:app"]
