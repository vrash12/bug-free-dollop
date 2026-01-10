# backend/config.py
import os
from datetime import timedelta
from urllib.parse import quote_plus

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "dev-jwt-secret-change-me")

    # Preferred: provide DATABASE_URL directly (recommended for Cloud Run)
    database_url = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_URL".lower())

    if not database_url:
        # Fallback: build from pieces
        DB_HOST = os.environ.get("DB_HOST", "srv667.hstgr.io")
        DB_PORT = os.environ.get("DB_PORT", "3306")
        DB_NAME = os.environ.get("DB_NAME", "u782952718_fitquest")
        DB_USER = os.environ.get("DB_USER", "u782952718_bro")
        DB_PASS = os.environ.get("DB_PASS", "Vanrodolf123.")

        # Escape special characters in password safely
        DB_PASS_ESCAPED = quote_plus(DB_PASS)

        database_url = (
            f"mysql+pymysql://{DB_USER}:{DB_PASS_ESCAPED}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
            f"?charset=utf8mb4"
        )

    SQLALCHEMY_DATABASE_URI = database_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # 🔐 JWT config
    JWT_TOKEN_LOCATION = ["headers"]
    JWT_HEADER_NAME = "Authorization"
    JWT_HEADER_TYPE = "Bearer"
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=7)
