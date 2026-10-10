import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Не задана переменная окружения: {name}")
    return value


DATABASE_URL = URL.create(
    drivername="postgresql+psycopg",
    username=required_env("POSTGRES_USER"),
    password=required_env("POSTGRES_PASSWORD"),
    host=required_env("POSTGRES_HOST"),
    port=int(required_env("POSTGRES_PORT")),
    database=required_env("POSTGRES_DB"),
)
