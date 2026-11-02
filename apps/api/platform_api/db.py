import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def connect():
    password = (
        Path(os.environ.get("DB_PASSWORD_FILE", "/run/secrets/app_password")).read_text().strip()
    )
    return psycopg.connect(
        host=os.environ.get("DB_HOST", "db"),
        dbname="platform",
        user="platform_app",
        password=password,
        connect_timeout=2,
        options="-c statement_timeout=3000 -c lock_timeout=1000",
        row_factory=dict_row,
    )
