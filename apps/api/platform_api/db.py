import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from . import telemetry


@contextmanager
def connect():
    password = (
        Path(os.environ.get("DB_PASSWORD_FILE", "/run/secrets/app_password")).read_text().strip()
    )
    with (
        telemetry.span("db.transaction"),
        psycopg.connect(
            host=os.environ.get("DB_HOST", "db"),
            port=int(os.environ.get("DB_PORT", "5432")),
            dbname="platform",
            user="platform_app",
            password=password,
            connect_timeout=2,
            options="-c statement_timeout=3000 -c lock_timeout=1000",
            row_factory=dict_row,
        ) as conn,
    ):
        yield conn
