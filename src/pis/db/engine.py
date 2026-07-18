from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def get_engine(url: str):
    return create_engine(url, pool_pre_ping=True)


def make_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
