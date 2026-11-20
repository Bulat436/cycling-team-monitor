from collections.abc import Iterator

from fastapi import HTTPException, Request
from sqlalchemy import URL, create_engine, event, text
from sqlalchemy.orm import Session

from .config import Settings


def make_engine(settings: Settings):
    path = settings.database_file
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        URL.create("sqlite", database=str(path)), connect_args={"timeout": 5}, hide_parameters=True,
    )

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA secure_delete=ON")

    return engine


def verify_origin(request: Request) -> None:
    if request.headers.get("origin") not in request.app.state.settings.allowed_origins:
        raise HTTPException(403, "origin_denied")


def get_db(request: Request) -> Iterator[Session]:
    writing = request.method in {"POST", "PUT", "PATCH", "DELETE"}
    if writing:
        verify_origin(request)

    with Session(request.app.state.engine, expire_on_commit=False) as db:
        db.execute(text("BEGIN IMMEDIATE" if writing else "BEGIN"))
        yield db
