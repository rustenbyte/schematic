import json
import threading
from collections.abc import Iterator
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from schematic.errors import UiMissingError
from schematic.server import SheetServer
from schematic.states import States
from schematic.target import Results

PAGE = {"index.html": "<!doctype html><title>sheet</title>", "app.js": "void 0;", "style.css": "body {}"}


@pytest.fixture
def web(tmp_path: Path) -> Path:
    for name, content in PAGE.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    return tmp_path


@pytest.fixture
def server(results: Results, web: Path) -> Iterator[SheetServer]:
    served = SheetServer(states=States(results=results), web=web, port=0)
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
    status, content_type, body = get(server, "api/states/working/sheets")
    assert (status, content_type) == (200, "application/json")
    assert [sheet["id"] for sheet in json.loads(body)["sheets"]] == ["shop", "app", "errors", "ports", "store"]


def test_a_sheet_and_its_flows_are_served(server: SheetServer) -> None:
    sheet = json.loads(get(server, "api/states/working/sheets/store")[2])
    flows = json.loads(get(server, "api/states/working/sheets/store/flows")[2])
    assert [part["title"] for part in sheet["parts"]] == ["MemoryOrders", "SystemClock"]
    assert len(flows["flows"]) == 4


def test_the_details_of_a_part_are_served(server: SheetServer) -> None:
    detail = json.loads(get(server, "api/states/working/parts/shop.store.MemoryOrders")[2])
    assert (detail["title"], detail["implements"]) == ("MemoryOrders", ["shop.ports.Orders"])


def test_an_unknown_sheet_answers_404(server: SheetServer) -> None:
    status, _, body = get(server, "api/states/working/sheets/basket")
    assert (status, json.loads(body)) == (404, {"detail": "no sheet 'basket'"})


def test_the_states_list_the_working_tree_first(server: SheetServer) -> None:
    states = json.loads(get(server, "api/states")[2])
    assert (states["current"], [state["id"] for state in states["states"]]) == ("working", ["working"])


def test_an_unknown_state_answers_404(server: SheetServer) -> None:
    status, _, body = get(server, "api/states/v9/sheets")
    assert (status, json.loads(body)) == (404, {"detail": "no state 'v9'"})


def test_a_server_without_the_built_page_refuses_to_start(results: Results, tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("", encoding="utf-8")
    with pytest.raises(UiMissingError, match=r"app\.js, style\.css missing"):
        SheetServer(states=States(results=results), web=tmp_path, port=0)
