# schematic

Reads a Python application and maps its architecture: components, the wires between them, the HTTP routes, every
path a call may take, and the path each test actually takes.

Two probes run inside the target's own virtual environment, so they see exactly the types and tests the target sees:

- `static_scan.py` resolves every call with the target's own mypy (the target must have mypy installed). It does not
  import or run the application.
- `flow_recorder.py` runs the target's tests under `sys.monitoring` and records, per test, which functions of the
  package called which, and where exceptions were raised and handled.

The target needs a `.venv` in its root and its package in `src/<name>` or `<name>`. Nothing is written into the
target: results are kept in `~/.cache/schematic/<project path>/`.

## Use

```bash
uv sync                                   # once, in this folder
cd <project>                              # or pass --project <path>
schematic scan                            # resolve the package (--package <name> if not the project name)
schematic routes                          # every FastAPI route and its handler
schematic trace "GET /orders/{item}"      # every path a route or a function may take
schematic chip MemoryOrders               # one component: wires, pins, holders, callers
schematic record -- -m flow tests         # run tests (pytest arguments after --) and record their paths
schematic flows                           # the recorded flows
schematic flow "place an order"           # the path one flow took, against the paths it may take
```

`schematic` is `.venv/bin/schematic` of this folder.

## Flows

A flow is a test. Mark it with the use case it plays, and give it the input data that picks the branch:

```python
@pytest.mark.flow("place an order")
def test_an_order_is_placed() -> None: ...
```

An unmarked test records under its test id. Only the call phase of a test is recorded, not its fixtures. A test that
stands in fakes for services records only up to the fake: a flow shows the real path only when the test runs the real
composition.

## Trace symbols

| Symbol | Meaning |
| --- | --- |
| `⇐ P` | dispatch through Protocol `P` to the class wired to it |
| `◇` | a FastAPI dependency (`Depends`, also on the router) |
| `↪` | a function handed off as a value |
| `new` / `raises` | a class built, or built and raised |
| `⏚ module:` | calls that leave the package, by top-level module |
| `?` | a call to a callable passed as a value, which the types do not resolve |
| `↑` | expanded above, or a cycle |
| `← module` | a flow entered from outside the package, through that module |

## Checks

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy && uv run pytest
```
