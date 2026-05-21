"""SQLite соединение и сессия."""
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from .models import Base


_engine = None
_SessionLocal = None


def init_db(db_path: str) -> None:
    """Создать engine и таблицы."""
    global _engine, _SessionLocal
    _engine = create_engine(f"sqlite:///{db_path}", echo=False, future=True)
    Base.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


@contextmanager
def get_session() -> Session:
    if _SessionLocal is None:
        raise RuntimeError("DB не инициализирована. Вызови init_db() первым.")
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
