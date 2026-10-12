"""Run backend checks on a disposable loopback database, never the app database.

Usage: python tests/run_isolated.py [pytest arguments]
Requires the local development PostgreSQL role to have CREATEDB.
Only the randomly generated test database is migrated/dropped.
"""
import os
from pathlib import Path
import subprocess
import sys
import uuid

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def main():
    root = Path(__file__).resolve().parents[1]
    local = dotenv_values(root / '.env')
    raw = os.environ.get('DEV_DATABASE_URL') or local.get('DEV_DATABASE_URL', '')
    try:
        url = make_url(raw).set(drivername='postgresql+psycopg')
        if url.host not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError('loopback required')
    except Exception:
        print('BLOCKED: a loopback DEV_DATABASE_URL is required (value omitted).')
        return 2
    name = 'test_vire_e2e_' + uuid.uuid4().hex
    admin = create_engine(url.set(database='postgres'), isolation_level='AUTOCOMMIT',
                          hide_parameters=True, connect_args={'connect_timeout': 5})
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
            created = True
        env = dict(os.environ, APP_ENV='development',
                   DEV_DATABASE_URL=url.set(database=name).render_as_string(hide_password=False),
                   SECRET_KEY='isolated-test-signing-key-not-for-deployment',
                   FIREBASE_SERVICE_ACCOUNT='', FIREBASE_PROJECT_ID='',
                   PROD_LLM_API_KEY='', PROD_DATABASE_URL='', SUPABASE_URL='',
                   SUPABASE_SECRET_KEY='', GOOGLE_CLIENT_ID='', DEV_LLM_PROVIDER='disabled',
                   PYTHONPATH=str(root))
        commands = [[sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider',
                     *(sys.argv[1:] or ['-q'])]]
        if os.environ.get('VIRE_CHECK_MIGRATIONS') == '1':
            commands += [[sys.executable, '-m', 'alembic', *args] for args in
                         [('heads',), ('upgrade', 'head'), ('check',)]]
        result = 0
        for command in commands:
            run = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True,
                                 encoding='utf-8', errors='replace')
            output = run.stdout + run.stderr
            # Never emit connection strings or local credentials in failures.
            for value in (raw, env['DEV_DATABASE_URL'], url.password):
                if value:
                    output = output.replace(value, '[REDACTED]')
            print(output, end='')
            result = result or run.returncode
        return result
    except Exception as error:
        print('BLOCKED: isolated PostgreSQL checks failed (' + type(error).__name__ + ').')
        return 2
    finally:
        if created:
            assert name.startswith('test_vire_e2e_') and name != url.database
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


if __name__ == '__main__':
    sys.exit(main())
