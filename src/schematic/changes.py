from collections.abc import Callable, Hashable, Sequence
from enum import StrEnum

from pydantic import BaseModel

from schematic.scan import FROZEN
from schematic.sheets import Call, Inbound, Outbound, Part, Sheet, Sheets


class Status(StrEnum):
    SAME = "same"
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"


class PartChange(BaseModel):
    """How a part changed: its status, each method's status, and whether its docstring changed.

    `changes` counts what changed inside it: methods and docstring for a class or module, and every changed item
    of its sheet for a package on the overview.
    """

    model_config = FROZEN

    status: Status
    methods: dict[str, Status]
    doc: bool
    changes: int


class SheetComparison(BaseModel):
    """One sheet drawn for both states at once: the parts and calls of either, each with its status."""

    model_config = FROZEN

    base: str
    head: str
    sheet: Sheet
    parts: tuple[PartChange, ...]
    calls: tuple[Status, ...]
    inbound: tuple[Status, ...]
    outbound: tuple[Status, ...]

    def changes(self) -> int:
        statuses = (*(part.status for part in self.parts), *self.calls, *self.inbound, *self.outbound)
        return sum(1 for status in statuses if status is not Status.SAME)


class SheetChanges(BaseModel):
    model_config = FROZEN

    id: str
    title: str
    status: Status
    changes: int


class ComparisonIndex(BaseModel):
    model_config = FROZEN

    base: str
    head: str
    overview: str
    sheets: tuple[SheetChanges, ...]


def status_of(base: object | None, head: object | None) -> Status:
    if base is None:
        return Status.ADDED
    if head is None:
        return Status.REMOVED
    return Status.SAME if base == head else Status.CHANGED


class Comparison:
    """The difference between two states of the code, sheet by sheet."""

    def __init__(self, *, base: Sheets, head: Sheets, base_id: str, head_id: str) -> None:
        self._base = base
        self._head = head
        self._base_id = base_id
        self._head_id = head_id

    def index(self) -> ComparisonIndex:
        overview = self._head.overview
        base_ids, head_ids = set(self._base.ids), set(self._head.ids)
        overview_changes = self.sheet(overview).changes()
        rows = [
            SheetChanges(
                id=overview,
                title=overview,
                status=Status.CHANGED if overview_changes else Status.SAME,
                changes=overview_changes,
            )
        ]
        for sheet_id in sorted(base_ids | head_ids):
            changes = self.sheet(sheet_id).changes()
            status = status_of(sheet_id if sheet_id in base_ids else None, sheet_id if sheet_id in head_ids else None)
            rows.append(
                SheetChanges(
                    id=sheet_id,
                    title=sheet_id,
                    status=Status.CHANGED if status is Status.SAME and changes else status,
                    changes=changes,
                )
            )
        return ComparisonIndex(base=self._base_id, head=self._head_id, overview=overview, sheets=tuple(rows))

    def sheet(self, sheet_id: str) -> SheetComparison:
        overview = sheet_id == self._head.overview
        base = self._sheet(self._base, self._base.overview if overview else sheet_id)
        head = self._sheet(self._head, sheet_id)
        base_parts = {part.id: part for part in base.parts}
        head_parts = {part.id: part for part in head.parts}
        ids = sorted(base_parts.keys() | head_parts.keys())
        index = {part_id: position for position, part_id in enumerate(ids)}
        parts: list[Part] = []
        changes: list[PartChange] = []
        for part_id in ids:
            shown = head_parts.get(part_id) or base_parts[part_id]
            part, change = self._part(shown, base_parts.get(part_id), head_parts.get(part_id), overview=overview)
            parts.append(part)
            changes.append(change)
        calls, call_statuses = merge(
            base.calls,
            head.calls,
            base.parts,
            head.parts,
            key=lambda c, p: (p[c.source].id, c.source_method, p[c.target].id, c.target_method),
            rebuild=lambda c, p: c.model_copy(
                update={"source": index[p[c.source].id], "target": index[p[c.target].id]}
            ),
            weight=lambda c: c.count,
        )
        inbound, inbound_statuses = merge(
            base.inbound,
            head.inbound,
            base.parts,
            head.parts,
            key=lambda c, p: (c.sheet, p[c.target].id, c.target_method),
            rebuild=lambda c, p: c.model_copy(update={"target": index[p[c.target].id]}),
            weight=lambda c: c.count,
        )
        outbound, outbound_statuses = merge(
            base.outbound,
            head.outbound,
            base.parts,
            head.parts,
            key=lambda c, p: (p[c.source].id, c.source_method, c.target),
            rebuild=lambda c, p: c.model_copy(update={"source": index[p[c.source].id]}),
            weight=lambda c: c.count,
        )
        return SheetComparison(
            base=self._base_id,
            head=self._head_id,
            sheet=head.model_copy(
                update={"parts": tuple(parts), "calls": calls, "inbound": inbound, "outbound": outbound}
            ),
            parts=tuple(changes),
            calls=call_statuses,
            inbound=inbound_statuses,
            outbound=outbound_statuses,
        )

    def _sheet(self, sheets: Sheets, sheet_id: str) -> Sheet:
        if sheet_id == sheets.overview or sheet_id in sheets.ids:
            return sheets.sheet(sheet_id)
        return Sheet(id=sheet_id, title=sheet_id, parts=(), calls=(), inbound=(), outbound=())

    def _part(self, shown: Part, base: Part | None, head: Part | None, *, overview: bool) -> tuple[Part, PartChange]:
        status = status_of(
            (base.kind, base.role) if base is not None else None, (head.kind, head.role) if head is not None else None
        )
        base_methods = base.methods if base is not None else ()
        head_methods = head.methods if head is not None else ()
        methods = {
            method: status_of(method if method in base_methods else None, method if method in head_methods else None)
            for method in (*head_methods, *(m for m in base_methods if m not in head_methods))
        }
        doc = base is not None and head is not None and self._base.doc_of(base.id) != self._head.doc_of(head.id)
        changes = sum(1 for method in methods.values() if method is not Status.SAME) + int(doc)
        if overview and status is not Status.ADDED and status is not Status.REMOVED:
            changes = self.sheet(shown.id).changes()
        if status is Status.CHANGED or (status is Status.SAME and changes):
            status = Status.CHANGED
        merged = shown.model_copy(update={"methods": tuple(methods)})
        return merged, PartChange(status=status, methods=methods, doc=doc, changes=changes)


def merge[T: (Call, Inbound, Outbound)](
    base: Sequence[T],
    head: Sequence[T],
    base_parts: Sequence[Part],
    head_parts: Sequence[Part],
    *,
    key: Callable[[T, Sequence[Part]], Hashable],
    rebuild: Callable[[T, Sequence[Part]], T],
    weight: Callable[[T], int],
) -> tuple[tuple[T, ...], tuple[Status, ...]]:
    """Pair the items of two sheets by what they connect, and renumber them onto the merged parts."""
    before = {key(item, base_parts): item for item in base}
    after = {key(item, head_parts): item for item in head}
    items: list[T] = []
    statuses: list[Status] = []
    for found in sorted(before.keys() | after.keys(), key=repr):
        old, new = before.get(found), after.get(found)
        if new is not None:
            items.append(rebuild(new, head_parts))
        elif old is not None:
            items.append(rebuild(old, base_parts))
        statuses.append(status_of(weight(old) if old else None, weight(new) if new else None))
    return tuple(items), tuple(statuses)
