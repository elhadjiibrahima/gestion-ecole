from sqlmodel import SQLModel, Session, create_engine

DATABASE_URL = "sqlite:///./ecole.db"

# check_same_thread=False : nécessaire pour SQLite avec FastAPI
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
