from collections.abc import Iterator

from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db_engine import make_engine

settings = get_settings()

engine = make_engine(settings.DATABASE_URL)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
