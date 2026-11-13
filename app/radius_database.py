from collections.abc import Iterator

from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db_engine import make_engine

settings = get_settings()

# Separate engine for the `radius` database (radcheck, radreply, radusergroup, radacct).
radius_engine = make_engine(settings.RADIUS_DATABASE_URL)

RadiusSessionLocal = sessionmaker(
    bind=radius_engine, autoflush=False, expire_on_commit=False
)


def get_radius_db() -> Iterator[Session]:
    db = RadiusSessionLocal()
    try:
        yield db
    finally:
        db.close()
