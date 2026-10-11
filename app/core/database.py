from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database_options import database_engine_options

engine = create_engine(
    settings.DATABASE_URL,
    **database_engine_options(settings.DATABASE_URL, settings.APP_ENV),
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
