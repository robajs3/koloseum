import os
from dotenv import load_dotenv

load_dotenv(encoding='utf-8')

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.getenv(
        "UPLOAD_FOLDER",
        os.path.join(BASE_DIR, "uploads")
    )
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024
    SESSION_COOKIE_SECURE = (
        os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    )
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REDIS_URL = os.getenv("REDIS_URL")
    FILEVAULT_BASE_URL = os.getenv("FILEVAULT_BASE_URL")
    FILEVAULT_SECRET = os.getenv("FILEVAULT_SECRET")
    FILEVAULT_API_TOKEN = os.getenv("FILEVAULT_API_TOKEN")  # token do API FileVault
    VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY")
    VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY")
    VAPID_CLAIMS_EMAIL = os.getenv("VAPID_CLAIMS_EMAIL")
    PREFIX = os.getenv("PREFIX", "/koloseum")