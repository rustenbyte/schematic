import json
import socket
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources.abc import Traversable
from socketserver import BaseServer
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel

from schematic.errors import UiMissingError, UnknownNameError
from schematic.sheets import Sheets

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
JSON = "application/json"


class SheetServer:
    """Serves the sheets of one project and the page that draws them, on the loopback interface only."""

    def __init__(self, *, sheets: Sheets, web: Traversable, port: int) -> None:
        missing = [name for name, _ in STATIC.values() if not web.joinpath(name).is_file()]
        if missing:
            raise UiMissingError(
                f"the UI is not built ({', '.join(missing)} missing): run `npm ci` and `npm run build` in ui/"
            )
        self._server = ThreadingHTTPServer(("127.0.0.1", port), partial(SheetRequests, sheets=sheets, web=web))

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
        sheets: Sheets,
        web: Traversable,
    ) -> None:
        self._sheets = sheets
        self._web = web
        super().__init__(request, client_address, server)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path in STATIC:
            name, content_type = STATIC[path]
            self._send(HTTPStatus.OK, content_type, self._web.joinpath(name).read_bytes())
            return
        try:
            body = self._api([unquote(part) for part in path.strip("/").split("/")])
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

    def _api(self, parts: list[str]) -> BaseModel | None:
        match parts:
            case ["api", "sheets"]:
                return self._sheets.index()
            case ["api", "sheets", sheet_id]:
                return self._sheets.sheet(sheet_id)
            case ["api", "sheets", sheet_id, "flows"]:
                return self._sheets.flows(sheet_id)
        return None

    def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
