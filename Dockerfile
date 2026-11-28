FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

# WeasyPrint (export PDF) a besoin de Pango ; Tesseract sert à l'OCR du bot produits.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 \
        fonts-dejavu-core tesseract-ocr tesseract-ocr-fra \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN DJANGO_DEBUG=False DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput \
    && useradd --create-home app \
    && mkdir -p /app/media /app/var \
    && chown -R app:app /app/media /app/var \
    && chmod +x docker/entrypoint.sh

USER app

EXPOSE 8000

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["sh", "-c", "gunicorn config.wsgi:application --bind 0.0.0.0:${PORT} --workers ${GUNICORN_WORKERS:-3} --timeout ${GUNICORN_TIMEOUT:-120} --access-logfile -"]
