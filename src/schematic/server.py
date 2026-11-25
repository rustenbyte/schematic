import json
import socket
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources.abc import Traversable
from socketserver import BaseServer
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import BaseModel

from schematic.changes import Comparison
from schematic.errors import UiMissingError, UnknownNameError
from schematic.states import States

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
JSON = "application/json"


class SheetServer:
    """Serves the sheets of one project and the page that draws them, on the loopback interface only."""

    def __init__(self, *, states: States, web: Traversable, port: int) -> None:
        missing = [name for name, _ in STATIC.values() if not web.joinpath(name).is_file()]
        if missing:
            raise UiMissingError(
                f"the UI is not built ({', '.join(missing)} missing): run `npm ci` and `npm run build` in ui/"
            )
        self._server = ThreadingHTTPServer(("127.0.0.1", port), partial(SheetRequests, states=states, web=web))

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}/"

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class SheetRequests(BaseHTTPRequestHandler):
    def __init__(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
        server: BaseServer,
        *,
        states: States,
        web: Traversable,
    ) -> None:
        self._states = states
        self._web = web
        super().__init__(request, client_address, server)

    def do_GET(self) -> None:
        address = urlsplit(self.path)
        path = address.path
        if path in STATIC:
            name, content_type = STATIC[path]
            self._send(HTTPStatus.OK, content_type, self._web.joinpath(name).read_bytes())
            return
        try:
            body = self._api([unquote(part) for part in path.strip("/").split("/")], parse_qs(address.query))
        except UnknownNameError as error:
            self._send(HTTPStatus.NOT_FOUND, JSON, json.dumps({"detail": str(error)}).encode())
            return
        if body is None:
            self._send(HTTPStatus.NOT_FOUND, JSON, json.dumps({"detail": f"nothing at {path}"}).encode())
            return
        self._send(HTTPStatus.OK, JSON, body.model_dump_json().encode())

    def log_message(self, format: str, *args: object) -> None:
        # One local viewer: a line per request would bury the URL in the terminal.
        return

    def _api(self, parts: list[str], query: dict[str, list[str]]) -> BaseModel | None:
        match parts:
            case ["api", "states"]:
                return self._states.index()
            case ["api", "states", state, "data"]:
                return self._states.sheets(state).data(query.get("call", []))
            case ["api", "states", state, *rest]:
                return self._state(state, rest)
            case ["api", "compare", base, head, *rest]:
                return self._compare(base, head, rest)
        return None

    def _state(self, state: str, rest: list[str]) -> BaseModel | None:
        sheets = self._states.sheets(state)
        match rest:
            case ["sheets"]:
                return sheets.index()
            case ["sheets", sheet_id]:
                return sheets.sheet(sheet_id)
            case ["sheets", sheet_id, "flows"]:
                return sheets.flows(sheet_id)
            case ["parts", part_id]:
                return sheets.detail(part_id)
        return None

    def _compare(self, base: str, head: str, rest: list[str]) -> BaseModel | None:
        comparison = self._comparison(base, head)
        match rest:
            case []:
                return comparison.index()
            case ["sheets", sheet_id]:
                return comparison.sheet(sheet_id)
        return None

    def _comparison(self, base: str, head: str) -> Comparison:
        return Comparison(base=self._states.sheets(base), head=self._states.sheets(head), base_id=base, head_id=head)

    def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
