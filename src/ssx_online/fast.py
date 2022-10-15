from __future__ import annotations

import pathlib
import re
from contextlib import contextmanager
from datetime import datetime
from enum import Enum
from functools import lru_cache

import dateutil.tz
import fastapi
import ispyb.sqlalchemy as ispyb
import sqlalchemy
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import AnyHttpUrl, BaseModel, DirectoryPath, Extra
from sqlalchemy.orm import Session, joinedload, raiseload

app = FastAPI()

# The database does not store time zones. Let's assume it all happens in one
SITE_TZ = dateutil.tz.gettz("Europe/London")
KNOWN_VISITS = {"mx24447-95"}


@lru_cache
def get_ispyb_sqlalchemy_engine() -> sqlalchemy.Engine:
    return sqlalchemy.create_engine(
        ispyb.url(),
        connect_args={"use_pure": True},
        echo=True,
    )


@contextmanager
def ispyb_connection_sqlalchemy() -> sqlalchemy.Connection:
    with get_ispyb_sqlalchemy_engine().connect() as connection:
        yield connection


# @contextmanager
def get_session() -> Session:
    with ispyb_connection_sqlalchemy() as conn:
        with Session(conn, future=True) as session:
            yield session


def _get_dcs_for_blsession(
    session: Session, blsession: ispyb.BLSesssion
) -> list[ispyb.DataCollection]:
    """
    Get All DataCollections for a given BLSession
    """
    return (
        session.query(ispyb.DataCollection)
        .options(joinedload(ispyb.DataCollection.DataCollectionGroup), raiseload("*"))
        .join(ispyb.DataCollectionGroup)
        .filter(ispyb.DataCollectionGroup.sessionId == blsession.sessionId)
        .all()
    )


class ExperimentType(str, Enum):
    FIXED = "Serial Fixed"
    EXTRUDER = "Serial Jet"


class DB_Proposal(BaseModel):
    proposalCode: str
    proposalNumber: int

    class Config:
        orm_mode = True


class DB_BLSession(BaseModel):
    """
    Minimal BLSession mirror object.

    This is loaded from the database ORM object, but can be safely passed
    around between sessions without the risk of attempting to access.
    """

    sessionId: int
    beamLineName: str
    beamLineOperator: str
    endDate: datetime
    startDate: datetime
    visit_number: int

    Proposal: DB_Proposal

    class Config:
        orm_mode = True
        extra = Extra.allow


class VisitBase(DB_BLSession):
    """
    Base Visit, for getting the list of visits without specific in-depth detail
    """

    # Convenience fields for here
    code: str
    url: AnyHttpUrl

    @classmethod
    def from_blsession(
        cls,
        request: Request,
        session: Session,
        blsession: DB_BLSession,
        **kwargs,
    ) -> VisitBase:
        """
        Create from a database BLSession object

        Args:
            blsession:
                The BLSession. The Proposal object will be accessed, so
                either preload it or expect it to be loaded.
            request:
                The current request. Used for populating the url field.
        """
        visit_code = f"{blsession.Proposal.proposalCode}{blsession.Proposal.proposalNumber}-{blsession.visit_number}"
        db = DB_BLSession.from_orm(blsession)
        return cls(
            code=visit_code,
            url=request.url_for("get_visit", code=visit_code),
            **db.dict(),
            **kwargs,
        )

    @property
    def path(self) -> DirectoryPath:
        return pathlib.Path(f"/dls/{self.beamline}/data/{self.year}/{self.code}")

    class Config:
        schema_extra = {
            "example": {
                "code": "mx23345-92",
                "year": "2022",
                "beamline": "i24",
                "path": "/dls/i24/data/2022/mx23345-92",
                "start": datetime.fromisoformat("2022-10-07T12:00:00+01:00"),
                "end": datetime.fromisoformat("2022-10-07T20:00:00+01:00"),
                "url": "http://localhost:5000/api/visits/mx23345-92",
                "operator": "Dr A. Scientist",
            }
        }
        # orm_mode = True


class Visit(VisitBase):
    DataCollections: list[DataCollection]

    @classmethod
    def from_blsession(
        cls,
        request: Request,
        session: Session,
        blsession: DB_BLSession,
        **kwargs,
    ) -> Visit:
        dcs = [
            DataCollection.from_datacollection(dc, request)
            for dc in _get_dcs_for_blsession(session, blsession)
        ]
        return super().from_blsession(
            request, session, blsession, DataCollections=dcs, **kwargs
        )


class DB_DataCollectionGroup(BaseModel):
    experimentType: ExperimentType | None

    class Config:
        orm_mode = True
        extra = Extra.allow


class DB_DataCollection(BaseModel):
    dataCollectionId: int
    startTime: datetime | None
    endTime: datetime | None
    DataCollectionGroup: DB_DataCollectionGroup
    numberOfImages: int
    runStatus: str | None
    imageDirectory: pathlib.Path
    fileTemplate: str

    class Config:
        orm_mode = True
        extra = Extra.allow


class DataCollection(DB_DataCollection):
    filesystem_path: pathlib.Path
    url: AnyHttpUrl

    @classmethod
    def from_datacollection(
        cls, dc: ispyb.DataCollection, request: Request
    ) -> DataCollection:
        dc = DB_DataCollection.from_orm(dc)
        return cls(
            filesystem_path=pathlib.Path(dc.imageDirectory) / dc.fileTemplate,
            url=request.url_for("get_datacollection", dcid=dc.dataCollectionId),
            **dc.dict(),
        )


class NoSuchVisitError(RuntimeError):
    pass


def get_session_from_visit_code(
    visit: str,
    connection: sqlalchemy.engine.base.Connection | Session,
) -> DB_BLSession:
    """
    Retrieve the session object from a visit code.

    Args:
        visit: The visit code e.g. mx24435-23
        connection:
            The curently open database connection. This will only be
            used if the visit code hasn't been previously fetched and
            cached.

    Returns:
        The minimal BLSession object for this visit code. This is a
        small object that has the core fields from BLSession, but crucially
        can never be connected to the database. This is because this is
        cached; if you have already (recently) asked for a visit, then
        this function will avoid hitting the database again.

    Raises:
        NoVisitError: No session with this visit code could be found
    """
    # If this is the first time calling this, then setup
    if "clear_cache" not in get_session_from_visit_code.__dict__:

        def _clear_cache():
            get_session_from_visit_code._cache = ([], {})

        get_session_from_visit_code.__dict__["clear_cache"] = _clear_cache
        get_session_from_visit_code.clear_cache()  # type: ignore

    visits, results = get_session_from_visit_code._cache  # type: ignore

    if bl_session := results.get(visit):
        print("Returning Cache")
        return bl_session

    def _split_visit_code(visit: str) -> tuple[str, int, int]:
        re_visit = re.compile(r"([^\d]+)([^-]+)-(\d+)")
        m = re_visit.match(visit)
        if m is None:
            raise ValueError(f"Could not decode visit: {visit}")
        code, proposal, visitnum = m.groups()
        return code, int(proposal), int(visitnum)

    # We need to lookup
    code, proposal, visit_num = _split_visit_code(visit)

    def _get_blsession(sess: Session) -> ispyb.BLSession:
        blsession: ispyb.BLSession
        blsession = (
            sess.query(ispyb.BLSession)
            .options(joinedload(ispyb.BLSession.Proposal), raiseload("*"))
            .join(ispyb.Proposal)
            .filter(
                ispyb.Proposal.proposalCode == code,
                ispyb.Proposal.proposalNumber == proposal,
                ispyb.BLSession.visit_number == visit_num,
            )
        ).one()
        return blsession

    try:
        # If we have an existing session, use it
        if isinstance(connection, Session):
            blsession = DB_BLSession.from_orm(_get_blsession(connection))
        else:
            # Otherwise, we need to create our own session
            session: Session
            with Session(connection, future=True) as session:
                blsession = DB_BLSession.from_orm(_get_blsession(session))

    except sqlalchemy.exc.NoResultFound as e:
        raise NoSuchVisitError(f"No such visit: {visit}") from e

    # Append to cache and tidy up
    visits.append(blsession)
    results[visit] = blsession
    if len(visits) > 128:
        remove_visit = visits.pop(0)
        del results[remove_visit.code]

    return blsession


# @app.get("/")
# async def api_root(req: Request):
#     # return {"message": "Hello World"}
#     async def _timer():
#         a = 1
#         try:
#             while True:
#                 await asyncio.sleep(1)
#                 yield a
#                 a += 1
#         except asyncio.CancelledError as e:
#             print("Disconnected from client", req.client)
#             raise e
#         except Exception as e:
#             print("Unknown exception:", e)
#             raise

#     return EventSourceResponse(_timer())


@app.get("/visits", response_model=list[VisitBase])
async def get_visits(req: Request, session: Session = Depends(get_session)):
    """
    Get a list of visits
    """
    visits = []

    for visit_code in KNOWN_VISITS:
        visits.append(
            VisitBase.from_blsession(
                req, session, get_session_from_visit_code(visit_code, session)
            )
        )

    return visits


@app.get("/visits/{code}", response_model=Visit)
async def get_visit(
    request: Request,
    code: str = fastapi.Path(description="The Visit code"),
    session: Session = Depends(get_session),
):
    try:
        blsession = get_session_from_visit_code(code, session)
    except NoSuchVisitError:
        raise HTTPException(404, "No such visit")
    return Visit.from_blsession(request, session, blsession)


@app.get("/dc/{dcid}", response_model=DataCollection)
async def get_datacollection(
    request: Request,
    dcid: int = fastapi.Path(description="The Data Collection ID"),
    session: Session = Depends(get_session),
):
    try:
        dc: ispyb.DataCollection = (
            session.query(ispyb.DataCollection)
            .options(
                joinedload(ispyb.DataCollection.DataCollectionGroup), raiseload("*")
            )
            .join(ispyb.DataCollectionGroup)
            .filter(ispyb.DataCollection.dataCollectionId == dcid)
            .one()
        )
    except sqlalchemy.exc.NoResultFound:
        raise HTTPException(404, "No such DCID")

    return DataCollection.from_datacollection(dc, request)
