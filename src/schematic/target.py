import subprocess
import tomllib
from importlib.resources import as_file, files
from pathlib import Path

from schematic.errors import MissingResultError, ProbeFailedError, TargetError
from schematic.recording import Recording
from schematic.revision import Repository, Revision
from schematic.scan import StaticScan

INTERPRETERS = (Path(".venv/bin/python"), Path(".venv/Scripts/python.exe"))


class Target:
    """The project under analysis: the probes run in its own interpreter, so they see its types and its tests."""

    def __init__(self, *, root: Path, package_dir: Path, interpreter: Path) -> None:
        self.root = root
        self.package_dir = package_dir
        self.interpreter = interpreter

    @classmethod
    def at(cls, root: Path, *, package: str | None) -> Target:
        root = root.resolve()
        name = package or project_package(root)
        candidates = (root / "src" / name, root / name)
        package_dir = next((path for path in candidates if (path / "__init__.py").is_file()), None)
        if package_dir is None:
            raise TargetError(f"no package {name!r} in {root} or {root / 'src'}")
        interpreter = next((root / path for path in INTERPRETERS if (root / path).is_file()), None)
        if interpreter is None:
            raise TargetError(f"no virtual environment in {root / '.venv'}")
        return cls(root=root, package_dir=package_dir, interpreter=interpreter)

    def scan(self, *, output: Path) -> StaticScan:
        self._run("static_scan.py", output)
        return StaticScan.model_validate_json(output.read_bytes())

    def scan_revision(self, revision: Revision, *, results: Results) -> StaticScan:
        """Scan the package as it was at a revision, from an export of its files, with this target's interpreter."""
        package = self.package_dir.relative_to(self.root)
        tree = results.export_dir(revision)
        if not tree.is_dir():
            Repository(root=self.root).export(revision, ("pyproject.toml", package.as_posix()), into=tree)
        exported = Target(root=tree, package_dir=tree / package, interpreter=self.interpreter)
        scan = exported.scan(output=results.revision_scan_file(revision.sha))
        results.revision_file(revision.sha).write_text(revision.model_dump_json(), encoding="utf-8")
        return scan

    def record(self, pytest_args: tuple[str, ...], *, output: Path) -> Recording:
        self._run("flow_recorder.py", output, *pytest_args)
        return Recording.model_validate_json(output.read_bytes())

    def _run(self, probe: str, output: Path, *arguments: str) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        with as_file(files("schematic.probes") / probe) as script:
            # -I keeps the target's PYTHONPATH and user site out; -B keeps .pyc files out of its tree.
            finished = subprocess.run(
                [str(self.interpreter), "-I", "-B", str(script), str(self.package_dir), str(output), *arguments],
                cwd=self.root,
                check=False,
            )
        if finished.returncode != 0:
            raise ProbeFailedError(f"{probe} stopped with exit code {finished.returncode} in {self.root}")


def project_package(root: Path) -> str:
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        raise TargetError(f"no pyproject.toml in {root}: name the package with --package")
    name = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {}).get("name")
    if not isinstance(name, str):
        raise TargetError(f"{pyproject} names no project: name the package with --package")
    return name.replace("-", "_")


class Results:
    """Where the scan and the recording of one project are kept between commands, outside the project."""

    def __init__(self, *, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def of(cls, root: Path) -> Results:
        slug = "-".join(part for part in root.resolve().parts if part not in ("/", "\\"))
        return cls(directory=Path.home() / ".cache" / "schematic" / slug)

    @property
    def scan_file(self) -> Path:
        return self.directory / "scan.json"

    @property
    def recording_file(self) -> Path:
        return self.directory / "recording.json"

    def scan(self) -> StaticScan:
        if not self.scan_file.is_file():
            raise MissingResultError("no scan yet: run `schematic scan` first")
        return StaticScan.model_validate_json(self.scan_file.read_bytes())

    def export_dir(self, revision: Revision) -> Path:
        return self.directory / "exports" / revision.sha

    def revision_scan_file(self, sha: str) -> Path:
        return self.directory / "revisions" / f"{sha}.scan.json"

    def revision_file(self, sha: str) -> Path:
        return self.directory / "revisions" / f"{sha}.revision.json"

    def revisions(self) -> tuple[Revision, ...]:
        """The scanned revisions, newest commit first."""
        folder = self.directory / "revisions"
        found = (
            [
                Revision.model_validate_json(path.read_bytes())
                for path in sorted(folder.glob("*.revision.json"))
                if (folder / path.name.replace(".revision.json", ".scan.json")).is_file()
            ]
            if folder.is_dir()
            else []
        )
        return tuple(sorted(found, key=lambda revision: revision.date, reverse=True))

    def revision_scan(self, sha: str) -> StaticScan:
        path = self.revision_scan_file(sha)
        if not path.is_file():
            raise MissingResultError(f"no scan of {sha}: run `schematic scan --revision {sha}`")
        return StaticScan.model_validate_json(path.read_bytes())

    def has_scan(self) -> bool:
        return self.scan_file.is_file()

    def has_recording(self) -> bool:
        return self.recording_file.is_file()

    def recording(self) -> Recording:
        if not self.recording_file.is_file():
            raise MissingResultError("no recording yet: run `schematic record` first")
        return Recording.model_validate_json(self.recording_file.read_bytes())
