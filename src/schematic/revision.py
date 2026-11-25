import io
import shutil
import subprocess
import tarfile
from pathlib import Path

from pydantic import BaseModel

from schematic.errors import TargetError
from schematic.scan import FROZEN


class Revision(BaseModel):
    """A commit of the target, named as people know it: by its tag when it has one, else by its short hash."""

    model_config = FROZEN

    sha: str
    label: str
    subject: str
    date: str


class Repository:
    """The target's git repository, read only: it names revisions and exports their files, and never checks out."""

    def __init__(self, *, root: Path) -> None:
        self._root = root
        git = shutil.which("git")
        if git is None:
            raise TargetError("git is not on the PATH")
        self._git_path = git

    def resolve(self, name: str) -> Revision:
        found = subprocess.run(
            [self._git_path, "rev-parse", "--verify", "--quiet", f"{name}^{{commit}}"],
            cwd=self._root,
            capture_output=True,
            check=False,
        )
        if found.returncode != 0:
            raise TargetError(f"no revision {name!r} in {self._root}")
        sha = found.stdout.decode().strip()
        tags = self._git("tag", "--points-at", sha).split()
        subject, date = self._git("log", "-1", "--format=%s%x00%cI", sha).rstrip("\n").split("\0")
        return Revision(sha=sha, label=tags[0] if tags else sha[:10], subject=subject, date=date)

    def export(self, revision: Revision, paths: tuple[str, ...], *, into: Path) -> None:
        present = tuple(path for path in paths if self._git("ls-tree", "--name-only", revision.sha, path).strip())
        archive = self._run("archive", "--format=tar", revision.sha, *present)
        into.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(into, filter="data")

    def _git(self, *arguments: str) -> str:
        return self._run(*arguments).decode()

    def _run(self, *arguments: str) -> bytes:
        finished = subprocess.run([self._git_path, *arguments], cwd=self._root, capture_output=True, check=False)
        if finished.returncode != 0:
            detail = finished.stderr.decode().strip() or f"git {arguments[0]} found nothing"
            raise TargetError(f"git {' '.join(arguments[:2])} in {self._root}: {detail}")
        return finished.stdout
