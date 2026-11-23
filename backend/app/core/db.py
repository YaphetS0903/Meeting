from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as session:
        try:
            yield session
            from app.services.operation_logs import finish_request
            finish_request(session)
            session.commit()
        except Exception as error:
            session.rollback()
            # Keep rejected operations after rolling back their business changes.
            try:
                from app.services.operation_logs import finish_request
                finish_request(session, error)
                session.commit()
            except Exception:
                session.rollback()
                import logging
                logging.getLogger(__name__).exception("Could not persist rejected operation")
            raise


def locked_get(db: Session, model, identifier):
    from sqlalchemy import select

    return db.scalar(select(model).where(model.id == identifier).with_for_update())
