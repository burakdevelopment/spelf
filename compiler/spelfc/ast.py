from dataclasses import dataclass, field
from typing import Optional

@dataclass
class Node:
    line: int = field(default=0, kw_only=True)
    col: int = field(default=0, kw_only=True)

@dataclass
class TInt(Node):
    lo: int
    hi: int

@dataclass
class TFloat(Node):
    unit: object
    frame: Optional[str]
    lo: Optional[float]
    hi: Optional[float]
    bits: int = 64

@dataclass
class TBool(Node):
    pass

@dataclass
class TInstant(Node):
    clock: str

@dataclass
class TArray(Node):
    elem: Node
    n: int

@dataclass
class TSuspect(Node):
    inner: Node

@dataclass
class TBounded(Node):
    elem: Node
    n: int

@dataclass
class TName(Node):
    name: str

@dataclass
class TFix(Node):
    total: int
    frac: int
    unit: object

@dataclass
class THeld(Node):
    inner: Node

@dataclass
class TLatch(Node):
    pass

@dataclass
class IntLit(Node):
    value: int

@dataclass
class FloatLit(Node):
    value: float
    unit: object

@dataclass
class BoolLit(Node):
    value: bool

@dataclass
class StrLit(Node):
    value: str

@dataclass
class Name(Node):
    name: str

@dataclass
class Path(Node):
    left: str
    right: str
    sep: str

@dataclass
class Unary(Node):
    op: str
    operand: Node

@dataclass
class Binary(Node):
    op: str
    left: Node
    right: Node

@dataclass
class Call(Node):
    func: str
    args: list
    kwargs: dict = field(default_factory=dict)

@dataclass
class Requantize(Node):
    target: Node
    expr: Node
    rounding: Optional[str] = None

@dataclass
class Index(Node):
    base: Node
    index: Node

@dataclass
class ArrayRepeat(Node):
    value: Node
    n: int

@dataclass
class Narrow(Node):
    target: Node
    expr: Node

@dataclass
class Convert(Node):
    unit: object
    expr: Node

@dataclass
class Pattern(Node):
    kind: str
    enum: Optional[str] = None
    variant: Optional[str] = None
    bind: Optional[str] = None

@dataclass
class Arm(Node):
    pattern: Pattern
    body: Node

@dataclass
class Match(Node):
    scrutinee: Node
    arms: list

@dataclass
class Block(Node):
    stmts: list
    tail: Optional[Node]

@dataclass
class IfExpr(Node):
    cond: Node
    then: Block
    els: Optional[Node]

@dataclass
class Let(Node):
    name: str
    type: Optional[Node]
    value: Node

@dataclass
class Var(Node):
    name: str
    type: Node
    value: Node

@dataclass
class Assign(Node):
    target: Node
    value: Node

@dataclass
class For(Node):
    var: str
    lo: int
    hi: int
    body: Block
    invariant: Optional[Node] = None

@dataclass
class Return(Node):
    value: Optional[Node]

@dataclass
class Halt(Node):
    because: Optional[str]
    allowed_in: Optional[list]

@dataclass
class ExprStmt(Node):
    expr: Node

@dataclass
class Annotation(Node):
    name: str
    args: list

@dataclass
class Param(Node):
    name: str
    type: Node

@dataclass
class Effect(Node):
    kind: str
    value: object = None

@dataclass
class FnDecl(Node):
    name: str
    params: list
    ret: Optional[Node]
    requires: list
    ensures: list
    effects: list
    recursive: Optional[int]
    annotations: list
    body: Optional[Block]
    is_extern: bool = False

@dataclass
class EnumDecl(Node):
    name: str
    variants: list

@dataclass
class StateDecl(Node):
    name: str
    type: Node
    value: Node

@dataclass
class EncodedDecl(Node):
    name: str
    wire: Node
    semantic: Node
    dparam: str
    dexpr: Node
    eparam: str
    eexpr: Node

@dataclass
class TWire(Node):
    name: str

@dataclass
class GenericCall(Node):
    func: str
    tname: str
    args: list

@dataclass
class StructDecl(Node):
    name: str
    fields: list
    invariant: Optional[Node]

@dataclass
class StructLit(Node):
    name: str
    fields: list

@dataclass
class TaskDecl(Node):
    name: str
    props: dict
    body: Block
    kind: str = "task"

@dataclass
class ProtectedDecl(Node):
    name: str
    fields: list
    fns: list
    annotations: list

@dataclass
class ProtCall(Node):
    obj: str
    method: str
    args: list

@dataclass
class FaultHypothesis(Node):
    name: str
    failed: list
    channel_faults: int = 0
    fault_mode: Optional[str] = None
    healthy_start: int = 0

@dataclass
class RedundancyDecl(Node):
    name: str
    props: dict

@dataclass
class ConstDecl(Node):
    name: str
    type: Node
    value: Node

@dataclass
class ConfigField(Node):
    name: str
    type: Node
    value: Node

@dataclass
class ConfigDecl(Node):
    name: str
    fields: list

@dataclass
class ModeLattice(Node):
    name: str
    edges: list

@dataclass
class FlightPhase(Node):
    modes: list

@dataclass
class ModuleDecl(Node):
    path: str
    annotations: list

@dataclass
class Program(Node):
    items: list

@dataclass
class ScheduleDecl(Node):
    name: str
    modes: list
    entries: list

@dataclass
class ClockDecl(Node):
    nominal: Node
    degraded: list

@dataclass
class WatchdogDecl(Node):
    name: str
    props: dict

@dataclass
class TRes(Node):
    name: str
    states: list

@dataclass
class TBorrow(Node):
    inner: Node

@dataclass
class ResourceDecl(Node):
    name: str
    states: list
    region: Optional[str]
    device: list
    read: Optional[list]
    write: Optional[list]
    fields: list

@dataclass
class TransitionDecl(Node):
    resource: str
    src: str
    dst: str
    witness: Optional[tuple]

@dataclass
class RegionDecl(Node):
    name: str
    props: dict

@dataclass
class RotationDecl(Node):
    name: str
    src: str
    dst: str
    m: list

@dataclass
class TypeAlias(Node):
    name: str
    type: Node
