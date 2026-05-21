"""SQLite соединение и сессия."""
from contextlib import contextmanager
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from loguru import logger

from .models import Base, OpportunityLog


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


def cleanup_opportunities(keep_days: int = 7) -> int:
    """Удалить записи opportunities старше keep_days дней.

    Возвращает количество удалённых строк.
    Позиции и funding_events не трогаем — они маленькие и ценные.
    """
    cutoff = datetime.utcnow() - timedelta(days=keep_days)
    with get_session() as s:
        deleted = (
            s.query(OpportunityLog)
            .filter(OpportunityLog.found_at < cutoff)
            .delete(synchronize_session=False)
        )
    if deleted:
        logger.info(f"DB cleanup: удалено {deleted} opportunities старше {keep_days} дней")
    return deleted
