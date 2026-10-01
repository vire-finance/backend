from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    isolation_level="READ COMMITTED",
)

if engine.dialect.name != "postgresql":
    raise RuntimeError(
        "Dibutuhkan PostgreSQL. Gunakan DATABASE_URL postgresql+psycopg://..."
    )

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

def get_db():
    db = SessionLocal()

    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()