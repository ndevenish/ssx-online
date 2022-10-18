from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import re
from contextlib import contextmanager
from datetime import datetime
from enum import Enum
from functools import lru_cache

import aiofiles.os
import dateutil.tz
import fastapi
import ispyb.sqlalchemy as ispyb
import sqlalchemy
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import AnyHttpUrl, BaseModel, Extra, Field
from sqlalchemy.orm import Session, joinedload, raiseload
from sse_starlette.sse import EventSourceResponse

from . import filewatcher

logging.basicConfig(level=logging.DEBUG)

app = FastAPI()

# @app.on_event("shutdown")
# async def shutdown():
#     print("Shutting down event")

# The database does not store time zones. Let's assume it all happens in one
SITE_TZ = dateutil.tz.gettz("Europe/London")
KNOWN_VISITS = {"mx24447-95", "mx24447-42"}


def visit_path_for_blsession(blsession: DB_BLSession | ispyb.BLSession) -> pathlib.Path:
    """
    Map from a BLSession to a filesystem visit path
    """
    visit_code = f"{blsession.Proposal.proposalCode}{blsession.Proposal.proposalNumber}-{blsession.visit_number}"
    return pathlib.Path(
        f"/dls/{blsession.beamLineName}/data/{blsession.startDate.year}/{visit_code}"
    )


def _remap_path(path: pathlib.Path) -> pathlib.Path:
    """
    Remap any file access path before access, for testing and transplantation.
    """
    # return pathlib.Path("/Users/nickd/dials/react/ssx-online/_test_root") / path
    if not path.is_absolute():
        raise ValueError("Cannot remap relative path")
    if root := os.getenv("DLS_ROOT"):
        return pathlib.Path(root) / pathlib.Path(*path.parts[1:])
    return path


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


@contextmanager
def ispyb_session() -> Session:
    """Make an ispyb sqlalchemy session, as a context manager"""
    try:
        with ispyb_connection_sqlalchemy() as conn:
            with Session(conn, future=True) as session:
                yield session
    except sqlalchemy.exc.InterfaceError:
        raise HTTPException(503, "The database was unavailable")


def get_session() -> Session:
    """
    FastAPI Dependency function to make an sqlalchemy connection
    """
    with ispyb_session() as session:
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
        schema_extra = {
            "example": {
                "proposalCode": "mx",
                "proposalNumber": 424242,
            }
        }


class DB_BLSession(BaseModel):
    """
    Minimal BLSession mirror object.

    This is loaded from the database ORM object, but can be safely passed
    around between sessions without the risk of attempting to access.
    """

    sessionId: int = Field()
    beamLineName: str
    beamLineOperator: str
    endDate: datetime
    startDate: datetime
    visit_number: int

    Proposal: DB_Proposal

    class Config:
        orm_mode = True
        extra = Extra.allow
        schema_extra = {
            "example": {
                "sessionId": 20000042,
                "beamLineName": "i24",
                "beamLineOperator": "Dr Nota Number",
                "endDate": "2004-04-24T12:00:00",
                "startDate": "2004-04-24T08:00:00",
                "visit_number": 42,
                "Proposal": {"proposalCode": "mx", "proposalNumber": 42424},
            }
        }


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

    class Config:
        schema_extra = {
            "example": DB_BLSession.Config.schema_extra["example"]
            | {"code": "mx42424-42", "url": "http://localhost:5000/visits/mx42424-42"}
        }


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

        # Work out where the PIA results would be

        return cls(
            filesystem_path=pathlib.Path(dc.imageDirectory) / dc.fileTemplate,
            url=request.url_for("get_datacollection", dcid=dc.dataCollectionId),
            **dc.dict(),
        )

    class Config:
        schema_extra = {
            "example": {
                "dataCollectionId": 9000001,
                "startTime": "2004-04-24T09:42:32",
                "endTime": "2004-04-24T10:24:42",
                "DataCollectionGroup": {"experimentType": "Serial Fixed"},
                "numberOfImages": 24242,
                "runStatus": "DataCollection Successful",
                "imageDirectory": "/dls/i24/data/2022/mx42424-42/empty_space",
                "fileTemplate": "nothing01_#####.cbf",
                "filesystem_path": "/dls/i24/data/2022/mx42424-42/empty_space/nothing01_#####.cbf",
                "url": "http://localhost:5000/dc/9000001",
            }
        }


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

    class Config:
        schema_extra = {
            "example": VisitBase.Config.schema_extra["example"]
            | {"DataCollections": [DataCollection.Config.schema_extra["example"]]}
        }


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


@app.get("/", include_in_schema=False)
def get_root():
    return RedirectResponse("/docs")


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

    # Attempt to work out where the PIA records should be
    return DataCollection.from_datacollection(dc, request)


class PIAResult(BaseModel):
    n_spots_4A: int
    n_spots_total: int
    file_number: int

    class Config:
        schema_extra = {
            "example": {"n_spots_4A": 42, "n_spots_total": 50, "file_number": 332}
        }


def _get_classic_pia_path(dc: ispyb.DataCollection) -> pathlib.Path:
    visit_path = visit_path_for_blsession(dc.DataCollectionGroup.BLSession)
    print("Calculated visit path:", visit_path)
    file_parts = re.match(r"^([^#]*)(_#*).(.+)$", dc.fileTemplate)
    assert file_parts is not None, f"Could not parse prefix from {dc.fileTemplate}"
    file_prefix = file_parts.group(1)
    return _remap_path(
        visit_path / "processing" / "_hitfind_results" / f"{file_prefix}.out"
    )


@app.get(
    "/dc/{dcid}/pia",
    description="Get the PIA data",
    response_model=list[PIAResult],
    responses={
        200: {
            "content": {
                "application/json": {},
                "text/event-stream": {
                    "example": (
                        'data: {"n_spots_4A": 42, "n_spots_total": 50, "file_number": 332}'
                        "\n\n"
                        'data: {"n_spots_4A": 12, "n_spots_total": 12, "file_number": 333}'
                    )
                },
            },
            "description": "Return the PIA data",
        }
    },
)
async def get_datacollection_pia(
    request: Request,
    dcid: int = fastapi.Path(description="The Data Collection ID", example="9121304"),
    range: str
    | None = Header(
        default=None,
        description="Partial range to return. Used to resume entries when partial contents have been received. Accepts form [lines=]<range-start>[-]",
    ),
):
    # The user might accept multiple forms. If we do, then try to choose the most
    # appropriate form for response.
    accepted_types = {
        x.strip() for x in request.headers.get("accept", "application/json").split(",")
    }

    with ispyb_session() as session:
        try:
            dc: ispyb.DataCollection = (
                session.query(ispyb.DataCollection)
                .options(
                    joinedload(ispyb.DataCollection.DataCollectionGroup)
                    .joinedload(ispyb.DataCollectionGroup.BLSession)
                    .joinedload(ispyb.BLSession.Proposal),
                    raiseload("*"),
                )
                .filter(ispyb.DataCollection.dataCollectionId == dcid)
                .one()
            )
        except sqlalchemy.exc.NoResultFound:
            raise HTTPException(404, "No such DCID")

    # Let's work out if we have PIA data for this datacollection.
    # We could have three states:
    # - There is no PIA (yet). This means that it might appear in the future
    #   - JSON: Return 404, it is not yet found
    #   - Stream: Results might appear. Allow the stream to watch for data to appear.
    # - There is PIA in progress. The file is partial.
    #   - JSON: Result the partial file with a 206 PARTIAL CONTENT status code. If the
    #           user provided a Range header, then offset the results by the number of
    #           entries provided.
    #   - Stream: Use the Range header to work out the offset, then resume streaming
    #             from there.
    # - The PIA is complete, no more results will be found.
    #   - JSON: Return the whole PIA document. If a range was requested, and we know
    #           that this is the end, then return a 200 code instead of a 206.
    #   - Stream: Return the stream/offset for stream to the end, and then close
    #             the stream. If a stream is already opened, then close it.
    #
    # Note that - it might not be possible to truly tell if all of the PIA images
    # have appeared, if it's just slow, or we have gotten a timeout whilst waiting.
    # So in the worst case this might need to be timeout-determined, which makes it
    # hard to re-determine if the client re-requests.

    # 1. "Classic" non-zocalo PIA
    pia_results_path = _get_classic_pia_path(dc)

    print("Expected 'Classic' PIA results at:", pia_results_path)

    if not await aiofiles.os.path.isfile(pia_results_path):
        if "text/event-stream" not in accepted_types:
            raise HTTPException(
                404, "PIA Results not found. They might not have started yet."
            )

    # File might exist. We want to return, either partially or in whole. Either way,
    # we need to read and parse the file.

    async def _emit_lines_to_client():
        # reader = FileLineReader("a.txt")

        # line_reader = reader.read_lines_continuous()
        listener = filewatcher.PIAListener(pathlib.Path("b.txt"))
        print("Starting listen loop")
        try:
            while True:
                data = await asyncio.wait_for(listener.get_data_chunk(), timeout=10)
                print("Got data:", data)

                yield "\n".join(str(x) for x in data)

        except asyncio.TimeoutError:
            print("Went a long period with no data; ending")

    # If we've asked for a text stream... then send one
    if "text/event-stream" in accepted_types:
        return EventSourceResponse(_emit_lines_to_client())

    return [PIAResult(n_spots_4A=42, n_spots_total=50, file_number=221)]
