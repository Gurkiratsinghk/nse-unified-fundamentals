"""
Database connection management.

Usage
-----
from data.db import get_db, init_db

init_db()                        # call once at startup

with get_db() as session:
    session.add(some_object)
    session.commit()
"""

from contextlib import contextmanager
from pathlib import Path

from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import sqlite3
import shutil
from datetime import datetime
import os

from data.models import Base

# ---------------------------------------------------------------------------
# Engine — points to the SQLite file inside the data/ directory
# ---------------------------------------------------------------------------

_DB_PATH = Path(__file__).parent / "nse_all_stocks.db"
_ENGINE_URL = f"sqlite:///{_DB_PATH}"

engine = create_engine(
    _ENGINE_URL,
    connect_args={"check_same_thread": False},  # safe for single-threaded use
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create all tables if they do not already exist."""
    logger.info(f"Initialising database at {_DB_PATH}")

    # If the DB file exists but is not a valid SQLite database, back it up
    # and remove it so SQLAlchemy can create a fresh one.
    if _DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(_DB_PATH))
            # simple pragma to validate DB; will raise if file is invalid
            conn.execute("PRAGMA schema_version;")
            conn.close()
        except sqlite3.DatabaseError as exc:
            logger.warning(
                "Detected invalid SQLite DB file at {} — backing up and recreating."
                .format(_DB_PATH)
            )
            timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            backup_name = f"{_DB_PATH.stem}.corrupt.{timestamp}.bak"
            backup_path = _DB_PATH.with_name(backup_name)
            try:
                shutil.move(str(_DB_PATH), str(backup_path))
                logger.info(f"Backed up corrupt DB to {backup_path}")
            except Exception:
                # if move fails, attempt to remove the file as a last resort
                try:
                    os.remove(_DB_PATH)
                    logger.info("Removed corrupt DB file")
                except Exception:
                    logger.error("Failed to backup or remove corrupt DB file")
                    raise

    Base.metadata.create_all(bind=engine)

    # Migrate existing tables: add is_synced column if missing
    _migrate_add_is_synced()

    logger.success("Database initialised — all tables ready.")


def _migrate_add_is_synced() -> None:
    """Add is_synced column to existing tables that were created before v2.1."""
    tables_to_migrate = ["company_essentials", "yearly_financials", "quarterly_financials"]

    conn = sqlite3.connect(str(_DB_PATH))
    cursor = conn.cursor()
    for table in tables_to_migrate:
        # Check if column already exists
        cursor.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in cursor.fetchall()]
        if "is_synced" not in columns:
            logger.info(f"  Migrating {table}: adding is_synced column...")
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN is_synced BOOLEAN DEFAULT 0")
            # Create index for fast lookups of unsynced records
            cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_is_synced ON {table}(is_synced)")
    conn.commit()
    conn.close()


MAX_BACKUPS = 3

def rotate_backups(mode: str = "full") -> None:
    """
    Rotate local database backups before a major ingest.
    Saves tables as compressed Parquet files to save space.
    """
    if not _DB_PATH.exists():
        logger.info("No existing database to back up — skipping rotation.")
        return

    if mode == "single":
        logger.info("Single-symbol scrape mode: skipping backup rotation.")
        return

    logger.info(f"Rotating Parquet backups for mode '{mode}' (max {MAX_BACKUPS})...")

    # Shift existing backup directories
    for i in range(MAX_BACKUPS, 1, -1):
        older = _DB_PATH.parent / f"backup_{mode}_bak{i}"
        newer = _DB_PATH.parent / f"backup_{mode}_bak{i - 1}"
        if newer.exists():
            if older.exists():
                shutil.rmtree(older)
            shutil.move(str(newer), str(older))
            logger.debug(f"  Moved {newer.name} → {older.name}")

    bak1 = _DB_PATH.parent / f"backup_{mode}_bak1"
    if bak1.exists():
        shutil.rmtree(bak1)
    bak1.mkdir(exist_ok=True)

    try:
        import pandas as pd
        import pyarrow
        engine_backup = create_engine(f"sqlite:///{_DB_PATH}")
        with engine_backup.connect() as conn:
            for table in ["companies", "company_essentials", "yearly_financials", "quarterly_financials"]:
                df = pd.read_sql(f"SELECT * FROM {table}", conn)
                df.to_parquet(bak1 / f"{table}.parquet", engine="pyarrow", compression="snappy")
        logger.success(f"  Current DB exported to Parquet at {bak1.name}")
    except Exception as e:
        logger.error(f"Failed to create Parquet backup: {e}")



@contextmanager
def get_db():
    """
    Yield a SQLAlchemy session and guarantee it is closed on exit.

    Example
    -------
    with get_db() as session:
        results = session.query(Company).all()
    """
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()