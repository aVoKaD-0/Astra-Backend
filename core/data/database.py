from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from core.config import DATABASE_URL
from core.data import Base

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    hide_parameters=True,
    connect_args={"connect_timeout": 5},
)

SessionLocal = sessionmaker(bind=engine)


def init_db() -> None:
    with engine.begin() as connection:
        connection.execute(text("SELECT 1"))
        Base.metadata.create_all(bind=connection)


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")
