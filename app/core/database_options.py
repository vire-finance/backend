"""Connection policy shared by the application and separately run Alembic jobs."""
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.pool import NullPool


def database_engine_options(database_url: str, app_env: str) -> dict:
    options = {
        "pool_pre_ping": True,
        "isolation_level": "READ COMMITTED",
        "hide_parameters": True,
    }
    if app_env != "production":
        return options

    try:
        url = make_url(database_url)
        # SQLAlchemy drops blank query values, so inspect the original TLS option.
        sslmodes = [value for key, value in parse_qsl(
            urlsplit(database_url).query, keep_blank_values=True
        ) if key == "sslmode"]
    except (ArgumentError, ValueError, TypeError):
        raise ValueError("PROD_DATABASE_URL must be a valid PostgreSQL URL.") from None
    if url.get_backend_name() != "postgresql":
        raise ValueError("Production requires a PostgreSQL database URL.")
    driver = url.drivername.partition("+")[2] or "psycopg2"
    if driver not in {"psycopg", "psycopg2"}:
        raise ValueError("Production requires the psycopg or psycopg2 PostgreSQL driver.")
    sslmode = sslmodes[0] if sslmodes else "require"
    if len(sslmodes) > 1 or sslmode not in ("require", "verify-ca", "verify-full"):
        raise ValueError("Production database TLS requires sslmode=require, verify-ca, or verify-full.")

    connect_args = {"sslmode": sslmode, "connect_timeout": 10}
    if driver == "psycopg":
        # Supabase transaction pooling cannot retain session prepared statements.
        # None disables automatic preparation; 0 would prepare every statement.
        connect_args["prepare_threshold"] = None
    options.update(poolclass=NullPool, connect_args=connect_args)
    return options
