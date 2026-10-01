import argparse

from sqlalchemy import text

from app.core.database import engine
from app.core.models import Base

def create_schema(reset=False):
    with engine.begin() as connection:
        if reset:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
            
        Base.metadata.create_all(connection)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--reset",
        action="store_true",
        help="Menghapus seluruh schema public dan membuat ulang tabel.",
    )

    args = parser.parse_args()

    create_schema(reset=args.reset)