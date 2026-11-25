import json
import threading
from collections.abc import Iterator
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from schematic.board import Board
from schematic.errors import UiMissingError
from schematic.recording import Recording
from schematic.server import SheetServer
from schematic.sheets import Sheets

PAGE = {"index.html": "<!doctype html><title>sheet</title>", "app.js": "void 0;", "style.css": "body {}"}


@pytest.fixture
def web(tmp_path: Path) -> Path:
    for name, content in PAGE.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    return tmp_path


@pytest.fixture
def server(board: Board, recording: Recording, web: Path) -> Iterator[SheetServer]:
    served = SheetServer(sheets=Sheets(board=board, recording=recording), web=web, port=0)
    thread = threading.Thread(target=served.serve_forever)
    thread.start()
    yield served
    served.close()
    thread.join()


def get(server: SheetServer, path: str) -> tuple[int, str, bytes]:
    try:
        with urlopen(server.url + path) as response:  # noqa: S310 the URL is the test's own loopback server
            return response.status, response.headers["Content-Type"], response.read()
    except HTTPError as error:
        with error:
            return error.code, error.headers["Content-Type"], error.read()


def test_the_page_is_served_at_the_root(server: SheetServer) -> None:
    assert get(server, "") == (200, "text/html; charset=utf-8", PAGE["index.html"].encode())


def test_the_index_of_sheets_is_served_as_json(server: SheetServer) -> None:
    status, content_type, body = get(server, "api/sheets")
    assert (status, content_type) == (200, "application/json")
    assert [sheet["id"] for sheet in json.loads(body)["sheets"]] == ["shop", "app", "errors", "ports", "store"]


def test_a_sheet_and_its_flows_are_served(server: SheetServer) -> None:
    sheet = json.loads(get(server, "api/sheets/store")[2])
    flows = json.loads(get(server, "api/sheets/store/flows")[2])
    assert [part["title"] for part in sheet["parts"]] == ["MemoryOrders", "SystemClock"]
    assert len(flows["flows"]) == 4


def test_an_unknown_sheet_answers_404(server: SheetServer) -> None:
    status, _, body = get(server, "api/sheets/basket")
    assert (status, json.loads(body)) == (404, {"detail": "no sheet 'basket'"})


def test_a_server_without_the_built_page_refuses_to_start(board: Board, tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("", encoding="utf-8")
    with pytest.raises(UiMissingError, match=r"app\.js, style\.css missing"):
        SheetServer(sheets=Sheets(board=board, recording=None), web=tmp_path, port=0)
