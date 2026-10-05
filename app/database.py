import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Locate project root relative to this file
BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "saas_capacity.db"

# Ensure data directory exists
DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Read DATABASE_URL from environment or fallback to SQLite
raw_db_url = os.getenv("DATABASE_URL")
if not raw_db_url:
    DATABASE_URL = f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"
else:
    # If a relative SQLite path is given, resolve it relative to BASE_DIR
    if raw_db_url.startswith("sqlite:///") and not raw_db_url.startswith("sqlite:////"):
        rel_path = raw_db_url.replace("sqlite:///", "")
        resolved = (BASE_DIR / rel_path).resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        DATABASE_URL = f"sqlite:///{resolved.as_posix()}"
    else:
        DATABASE_URL = raw_db_url

# Configure engine
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=False,
    pool_pre_ping=True
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI database session dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables defined in models if they do not exist."""
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
