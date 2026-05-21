from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .models import Base

_Session = None


def init_db(path: str):
    global _Session
    engine = create_engine(f"sqlite:///{path}", echo=False, future=True)
    Base.metadata.create_all(engine)
    _Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@contextmanager
def get_session():
    if _Session is None:
        raise RuntimeError("Вызови init_db() первым")
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
