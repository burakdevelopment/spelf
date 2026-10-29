from dataclasses import dataclass
from typing import Optional

from .units import Unit

INT64_MIN = -(2 ** 63)
INT64_MAX = 2 ** 63 - 1

@dataclass(frozen=True)
class IntT:
    lo: int
    hi: int

    def __str__(self):
        return f"Int<{self.lo}..={self.hi}>"

@dataclass(frozen=True)
class FloatT:
    unit: Unit
    frame: Optional[str] = None
    lo: Optional[float] = None
    hi: Optional[float] = None
    bits: int = 64

    def __str__(self):
        s = str(self.unit)
        if self.frame:
            s += f" @ {self.frame}"
        if self.lo is not None:
            s += f", {self.lo}..={self.hi}"
        return f"Float{'32' if self.bits == 32 else ''}<{s}>"

@dataclass(frozen=True)
class BoolT:
    def __str__(self):
        return "bool"

@dataclass(frozen=True)
class InstantT:
    clock: str

    def __str__(self):
        return f"Instant<{self.clock}>"

@dataclass(frozen=True)
class EnumT:
    name: str

    def __str__(self):
        return self.name

@dataclass(frozen=True)
class ArrayT:
    elem: object
    n: int

    def __str__(self):
        return f"[{self.elem}; {self.n}]"

@dataclass(frozen=True)
class SuspectT:
    inner: object

    def __str__(self):
        return f"Suspect<{self.inner}>"

@dataclass(frozen=True)
class BoundedT:
    elem: object
    n: int

    def __str__(self):
        return f"Bounded<{self.elem}, {self.n}>"

@dataclass(frozen=True)
class FixT:
    frac: int
    unit: Unit
    lo: int
    hi: int

    def __str__(self):
        for t in range(2, 63):
            if self.lo == -(2 ** (t - 1)) and self.hi == 2 ** (t - 1) - 1:
                return f"Fix<{t}, {self.frac}, {self.unit}>"
        return f"Fix<frac {self.frac}, {self.unit}, raw {self.lo}..={self.hi}>"

    def as_int(self) -> "IntT":
        return IntT(self.lo, self.hi)

@dataclass(frozen=True)
class HeldT:
    inner: object

    def __str__(self):
        return f"Held<{self.inner}>"

@dataclass(frozen=True)
class HeldReadT:
    inner: object

    def __str__(self):
        return f"HeldRead<{self.inner}>"

@dataclass(frozen=True)
class RecordT:
    name: str

    def __str__(self):
        return self.name

@dataclass(frozen=True)
class WireT:
    name: str

    def __str__(self):
        return f"Wire<{self.name}>"

@dataclass(frozen=True)
class ClampedT:
    inner: object

    def __str__(self):
        return f"Clamped<{self.inner}>"

@dataclass(frozen=True)
class LatchT:
    def __str__(self):
        return "Latch"

@dataclass(frozen=True)
class PushResT:
    def __str__(self):
        return "PushResult"

@dataclass(frozen=True)
class UnitT:
    def __str__(self):
        return "()"

@dataclass(frozen=True)
class StrT:
    def __str__(self):
        return "string"

SUSPECT_TAGS = {"Valid": 0, "Saturated": 1, "Stale": 2, "Invalid": 3}
HELD_TAGS = {"Fresh": 0, "Expired": 1}
CLAMP_TAGS = {"Unclamped": 0, "Clamped": 1}
PUSH_TAGS = {"Ok": 0, "Full": 1}

def fits_int64(t: IntT) -> bool:
    return INT64_MIN <= t.lo and t.hi <= INT64_MAX

def structurally_equal(a, b) -> bool:
    if isinstance(a, FloatT) and isinstance(b, FloatT):
        return a.unit.same_unit(b.unit) and a.frame == b.frame and a.lo == b.lo and a.hi == b.hi \
            and a.bits == b.bits
    if isinstance(a, ArrayT) and isinstance(b, ArrayT):
        return a.n == b.n and structurally_equal(a.elem, b.elem)
    if isinstance(a, SuspectT) and isinstance(b, SuspectT):
        return structurally_equal(a.inner, b.inner)
    if isinstance(a, BoundedT) and isinstance(b, BoundedT):
        return a.n == b.n and structurally_equal(a.elem, b.elem)
    if isinstance(a, FixT) and isinstance(b, FixT):
        return a.frac == b.frac and a.unit.same_unit(b.unit) and a.lo == b.lo and a.hi == b.hi
    if isinstance(a, (HeldT, HeldReadT, ClampedT)) and type(a) is type(b):
        return structurally_equal(a.inner, b.inner)
    return a == b

@dataclass(frozen=True)
class ResT:
    name: str
    states: frozenset

    def single(self):
        return next(iter(self.states)) if len(self.states) == 1 else None

    def __str__(self):
        return f"{self.name}<{' | '.join(sorted(self.states))}>"

@dataclass(frozen=True)
class BorrowT:
    inner: ResT

    def __str__(self):
        return f"&{self.inner}"
