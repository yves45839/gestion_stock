"""Isolated sales demo. Never points at the normal application's database."""
from .settings import *  # noqa: F403

IS_SALES_DEMO = True
DEMO_ROOT = BASE_DIR / "var" / "sales-demo"
DEMO_ROOT.mkdir(parents=True, exist_ok=True)
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3", "NAME": DEMO_ROOT / "db.sqlite3",
    "OPTIONS": {"timeout": 20},
}}
MEDIA_ROOT = DEMO_ROOT / "media"
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
CSRF_TRUSTED_ORIGINS = []
SESSION_COOKIE_NAME = "lrstock_sales_demo"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
MISTRAL_API_KEY = MISTRAL_AGENT_ID = SERPER_API_KEY = None
GOOGLE_CUSTOM_SEARCH_API_KEY = GOOGLE_CUSTOM_SEARCH_ENGINE_ID = None
GOOGLE_CSE_API_KEY = GOOGLE_CSE_CX = None
PRODUCT_BOT_IMAGE_URL_TEMPLATE = ""
PRODUCT_BOT_GOOGLE_IMAGE_SEARCH_ENABLED = PRODUCT_BOT_SERPER_IMAGE_SEARCH_ENABLED = False
PRODUCT_BOT_IMAGE_OCR_ENABLED = False
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
CELERY_TASK_ALWAYS_EAGER = True
MIDDLEWARE = ["inventory.demo.DemoMiddleware", *MIDDLEWARE]
TEMPLATES[0]["OPTIONS"]["context_processors"] = [
    *TEMPLATES[0]["OPTIONS"]["context_processors"], "inventory.demo.context",
]

DOCUMENT_DELIVERY_ENABLED = True
DOCUMENT_DELIVERY_SIMULATE = True
DOCUMENT_DELIVERY_ROOT = DEMO_ROOT / "document-delivery"
