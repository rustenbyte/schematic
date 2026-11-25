import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from schematic.changes import Comparison, SheetChanges, SheetComparison, Status
from schematic.errors import TargetError
from schematic.revision import Repository, Revision
from schematic.states import States
from schematic.target import Results, Target

RELEASE_TWO = {
    "src/shop/store.py": """
from datetime import UTC, datetime

from shop.errors import EmptyItemError
from shop.ports import Clock


class UtcClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class AuditLog:
    def note(self, item: str) -> None:
        self.last = item


class MemoryOrders:
    \"\"\"Orders kept in memory, each with the time it was placed.\"\"\"

    def __init__(self, *, clock: Clock) -> None:
        self._clock = clock
        self._placed: list[tuple[str, datetime]] = []

    def place(self, item: str) -> int:
        if not item:
            raise EmptyItemError("an order needs an item")
        AuditLog().note(item)
        self._placed.append((item, self._clock.now()))
        return self._count()

    def _count(self) -> int:
        \"\"\"Count every order placed so far.\"\"\"
        return len(self._placed)
""",
}


def git(root: Path, *arguments: str, when: str = "2026-01-01T12:00:00+00:00") -> None:
    command = [
        shutil.which("git") or "git",
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        *arguments,
    ]
    dates = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    subprocess.run(command, cwd=root, check=True, capture_output=True, env={**os.environ, **dates})


@pytest.fixture(scope="session")
def repository(sample: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("repository")
    shutil.copytree(sample, root, dirs_exist_ok=True)
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "Place orders")
    git(root, "tag", "v1")
    for name, text in RELEASE_TWO.items():
        (root / name).write_text(text.lstrip(), encoding="utf-8")
    app = root / "src/shop/app.py"
    app.write_text(app.read_text(encoding="utf-8").replace("SystemClock", "UtcClock"), encoding="utf-8")
    git(root, "commit", "-q", "-am", "Note each order", when="2026-02-01T12:00:00+00:00")
    git(root, "tag", "v2")
    return root


@pytest.fixture(scope="session")
def releases(repository: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Results, Revision, Revision]:
    root = repository
    results = Results(directory=tmp_path_factory.mktemp("results"))
    target = Target(root=root, package_dir=root / "src" / "shop", interpreter=Path(sys.executable))
    named = Repository(root=root)
    one, two = named.resolve("v1"), named.resolve("v2")
    target.scan_revision(one, results=results)
    target.scan_revision(two, results=results)
    return results, one, two


@pytest.fixture(scope="session")
def comparison(releases: tuple[Results, Revision, Revision]) -> Comparison:
    results, one, two = releases
    states = States(results=results)
    return Comparison(base=states.sheets(one.sha), head=states.sheets(two.sha), base_id=one.sha, head_id=two.sha)


def statuses(found: SheetComparison) -> dict[str, list[tuple[str, Status]]]:
    parts = found.sheet.parts
    return {
        "parts": [(part.title, change.status) for part, change in zip(parts, found.parts, strict=True)],
        "calls": [
            (f"{parts[c.source].title}.{c.source_method} > {parts[c.target].title}.{c.target_method}", status)
            for c, status in zip(found.sheet.calls, found.calls, strict=True)
        ],
        "inbound": [
            (f"{c.sheet} > {parts[c.target].title}.{c.target_method}", status)
            for c, status in zip(found.sheet.inbound, found.inbound, strict=True)
        ],
    }


def test_a_revision_is_named_by_its_tag(releases: tuple[Results, Revision, Revision]) -> None:
    _, one, two = releases
    assert (one.label, one.subject, two.label, two.subject) == ("v1", "Place orders", "v2", "Note each order")


def test_the_scanned_revisions_are_states_newest_first(releases: tuple[Results, Revision, Revision]) -> None:
    results, one, two = releases
    assert [state.label for state in States(results=results).index().states] == ["v2", "v1"]
    assert [revision.sha for revision in results.revisions()] == [two.sha, one.sha]


def test_a_sheet_shows_what_two_states_added_removed_and_changed(comparison: Comparison) -> None:
    assert statuses(comparison.sheet("store")) == {
        "parts": [
            ("AuditLog", Status.ADDED),
            ("MemoryOrders", Status.CHANGED),
            ("SystemClock", Status.REMOVED),
            ("UtcClock", Status.ADDED),
        ],
        "calls": [
            ("MemoryOrders.place > AuditLog.new", Status.ADDED),
            ("MemoryOrders.place > AuditLog.note", Status.ADDED),
            ("MemoryOrders.place > SystemClock.now", Status.REMOVED),
            ("MemoryOrders.place > UtcClock.now", Status.ADDED),
        ],
        "inbound": [
            ("app > MemoryOrders.new", Status.SAME),
            ("app > MemoryOrders.place", Status.SAME),
            ("app > SystemClock.new", Status.REMOVED),
            ("app > UtcClock.new", Status.ADDED),
        ],
    }


def test_a_part_whose_docstring_changed_is_changed(comparison: Comparison) -> None:
    found = comparison.sheet("store")
    memory = next(
        change for part, change in zip(found.sheet.parts, found.parts, strict=True) if part.title == "MemoryOrders"
    )
    assert (memory.doc, memory.methods, memory.changes) == (True, {"new": Status.SAME, "place": Status.SAME}, 1)


def test_the_index_counts_the_changes_of_each_sheet(comparison: Comparison) -> None:
    assert comparison.index().sheets == (
        SheetChanges(id="shop", title="shop", status=Status.CHANGED, changes=1),
        SheetChanges(id="app", title="app", status=Status.SAME, changes=0),
        SheetChanges(id="errors", title="errors", status=Status.SAME, changes=0),
        SheetChanges(id="ports", title="ports", status=Status.SAME, changes=0),
        SheetChanges(id="store", title="store", status=Status.CHANGED, changes=10),
    )


def test_an_unknown_revision_is_refused(repository: Path) -> None:
    with pytest.raises(TargetError, match="no revision 'v9'"):
        Repository(root=repository).resolve("v9")
