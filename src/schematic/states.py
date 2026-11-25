from datetime import UTC, datetime

from pydantic import BaseModel

from schematic.board import Board
from schematic.errors import MissingResultError, UnknownNameError
from schematic.routes import FastApiRoutes
from schematic.scan import FROZEN
from schematic.sheets import Sheets
from schematic.target import Results

WORKING = "working"


class State(BaseModel):
    """A scanned state of the code: the working tree, or a revision; only the working tree has recorded flows."""

    model_config = FROZEN

    id: str
    label: str
    subject: str
    date: str
    flows: bool


class StateList(BaseModel):
    model_config = FROZEN

    current: str
    states: tuple[State, ...]


class States:
    """The scanned states of one project, each drawn as sheets.

    A state's sheets are built the first time they are asked for and then kept, as building them resolves
    every call of the package.
    """

    def __init__(self, *, results: Results) -> None:
        self._results = results
        self._sheets: dict[str, Sheets] = {}

    def index(self) -> StateList:
        states: list[State] = []
        if self._results.has_scan():
            scanned = datetime.fromtimestamp(self._results.scan_file.stat().st_mtime, tz=UTC)
            states.append(
                State(
                    id=WORKING,
                    label="working tree",
                    subject="",
                    date=scanned.isoformat(timespec="seconds"),
                    flows=self._results.has_recording(),
                )
            )
        states.extend(
            State(id=found.sha, label=found.label, subject=found.subject, date=found.date, flows=False)
            for found in self._results.revisions()
        )
        if not states:
            raise MissingResultError("no scan yet: run `schematic scan`")
        return StateList(current=states[0].id, states=tuple(states))

    def sheets(self, state_id: str) -> Sheets:
        found = self._sheets.get(state_id)
        if found is None:
            found = self._load(state_id)
            self._sheets[state_id] = found
        return found

    def _load(self, state_id: str) -> Sheets:
        if state_id == WORKING:
            scan = self._results.scan()
            recording = self._results.recording() if self._results.has_recording() else None
        elif state_id in {found.sha for found in self._results.revisions()}:
            scan = self._results.revision_scan(state_id)
            recording = None
        else:
            raise UnknownNameError(f"no state {state_id!r}")
        return Sheets(board=Board(scan=scan, routes=FastApiRoutes(scan=scan)), recording=recording)
