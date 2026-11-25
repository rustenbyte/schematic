from enum import StrEnum

from pydantic import BaseModel, ConfigDict

FROZEN = ConfigDict(frozen=True, extra="forbid")


class CallKind(StrEnum):
    FUNCTION = "function"
    CONSTRUCTOR = "constructor"
    PROTOCOL = "protocol"
    VALUE = "value"
    UNRESOLVED = "unresolved"


class ClassKind(StrEnum):
    PROTOCOL = "protocol"
    ENUM = "enum"
    DATACLASS = "dataclass"
    PYDANTIC = "pydantic"
    EXCEPTION = "exception"
    ABSTRACT = "abstract"
    NAMEDTUPLE = "namedtuple"


class ParamKind(StrEnum):
    POSITIONAL = "positional"
    KEYWORD = "keyword"
    VAR_POSITIONAL = "var-positional"
    VAR_KEYWORD = "var-keyword"


class TypeText(BaseModel):
    model_config = FROZEN

    text: str
    refs: tuple[str, ...]


class Param(BaseModel):
    model_config = FROZEN

    name: str
    kind: ParamKind
    type: TypeText


class AnnotationCall(BaseModel):
    model_config = FROZEN

    target: str | None
    references: tuple[str, ...]


class DecoratorUse(BaseModel):
    """A decorator; a parameter of the enclosing function appears in `args` as `⟨name⟩`."""

    model_config = FROZEN

    target: str
    receiver: str | None = None
    receiver_param: str | None = None
    args: tuple[str | None, ...] = ()
    keywords: dict[str, str] = {}
    references: tuple[str, ...] = ()


class FunctionScan(BaseModel):
    model_config = FROZEN

    fullname: str
    module: str
    line: int
    is_async: bool
    params: tuple[Param, ...]
    returns: TypeText
    annotation_calls: tuple[AnnotationCall, ...]
    decorators: tuple[DecoratorUse, ...]


class ClassScan(BaseModel):
    model_config = FROZEN

    fullname: str
    module: str
    line: int
    kinds: tuple[ClassKind, ...]
    bases: tuple[str, ...]
    methods: tuple[str, ...]
    init: tuple[Param, ...]


class Receiver(BaseModel):
    model_config = FROZEN

    path: str
    type: TypeText


class Argument(BaseModel):
    """What a parameter receives at a call: a string, a named variable or function, or the caller's own parameter."""

    model_config = FROZEN

    param: str
    literal: str | None
    ref: str | None
    from_param: str | None


class CallSite(BaseModel):
    model_config = FROZEN

    caller: str
    line: int
    kind: CallKind
    target: str
    receiver: Receiver | None
    arguments: tuple[Argument, ...]
    raised: bool


class Reference(BaseModel):
    """A function passed as a value: a dependency, a callback, a hand-off to a thread."""

    model_config = FROZEN

    caller: str
    line: int
    target: str
    via: str


class Binding(BaseModel):
    """A concrete class given where a Protocol is declared: the wiring that picks an implementation."""

    model_config = FROZEN

    protocol: str
    implementation: str
    site: str
    line: int


class Construction(BaseModel):
    model_config = FROZEN

    name: str
    line: int
    kind: CallKind
    target: str
    keywords: dict[str, str]
    references: tuple[str, ...]


class ModuleScan(BaseModel):
    model_config = FROZEN

    name: str
    path: str


class StaticScan(BaseModel):
    model_config = FROZEN

    package: str
    modules: tuple[ModuleScan, ...]
    classes: tuple[ClassScan, ...]
    functions: tuple[FunctionScan, ...]
    calls: tuple[CallSite, ...]
    references: tuple[Reference, ...]
    bindings: tuple[Binding, ...]
    constructions: tuple[Construction, ...]
    type_errors: int
