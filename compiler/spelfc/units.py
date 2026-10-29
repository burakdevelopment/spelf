import math
from dataclasses import dataclass, field

_BASE = {
    "1":   (1.0, {}),
    "m":   (1.0, {"L": 1}),
    "km":  (1000.0, {"L": 1}),
    "ft":  (0.3048, {"L": 1}),
    "s":   (1.0, {"T": 1}),
    "ms":  (1e-3, {"T": 1}),
    "us":  (1e-6, {"T": 1}),
    "min": (60.0, {"T": 1}),
    "h":   (3600.0, {"T": 1}),
    "kg":  (1.0, {"M": 1}),
    "g":   (1e-3, {"M": 1}),
    "lb":  (0.45359237, {"M": 1}),
    "N":   (1.0, {"M": 1, "L": 1, "T": -2}),
    "lbf": (4.4482216152605, {"M": 1, "L": 1, "T": -2}),
    "Pa":  (1.0, {"M": 1, "L": -1, "T": -2}),
    "rad": (1.0, {"ANG": 1}),
    "deg": (math.pi / 180.0, {"ANG": 1}),
    "Hz":  (1.0, {"T": -1}),
    "kHz": (1e3, {"T": -1}),
    "MHz": (1e6, {"T": -1}),
    "A":   (1.0, {"I": 1}),
    "T":   (1.0, {"M": 1, "T": -2, "I": -1}),
    "uT":  (1e-6, {"M": 1, "T": -2, "I": -1}),
    "nT":  (1e-9, {"M": 1, "T": -2, "I": -1}),
    "B":   (1.0, {"INFO": 1}),
    "KiB": (1024.0, {"INFO": 1}),
    "MiB": (1048576.0, {"INFO": 1}),
}

@dataclass(frozen=True)
class Unit:
    scale: float
    dims: tuple
    text: str
    sym: tuple = field(default=(), compare=False)

    def same_dimension(self, other: "Unit") -> bool:
        return self.dims == other.dims

    def same_unit(self, other: "Unit") -> bool:
        return self.dims == other.dims and math.isclose(self.scale, other.scale, rel_tol=1e-12)

    def __mul__(self, other: "Unit") -> "Unit":
        return _make(self.scale * other.scale, _add(self.dims, other.dims, 1), _add(self.sym, other.sym, 1))

    def __truediv__(self, other: "Unit") -> "Unit":
        return _make(self.scale / other.scale, _add(self.dims, other.dims, -1), _add(self.sym, other.sym, -1))

    def is_dimensionless(self) -> bool:
        return not self.dims

    def __str__(self):
        return self.text

def _norm(d: dict) -> tuple:
    return tuple(sorted((k, v) for k, v in d.items() if v != 0))

def _add(a: tuple, b: tuple, sign: int) -> tuple:
    d = dict(a)
    for k, v in b:
        d[k] = d.get(k, 0) + sign * v
    return _norm(d)

def _sym_text(sym: tuple) -> str:
    def part(fs):
        return "*".join(n if e == 1 else f"{n}^{e}" for n, e in fs)
    num = [(n, e) for n, e in sym if e > 0]
    den = [(n, -e) for n, e in sym if e < 0]
    if not den:
        return part(num) or "1"
    d = part(den)
    return f"{part(num) or '1'}/{d if len(den) == 1 else '(' + d + ')'}"

def _make(scale: float, dims: tuple, sym: tuple) -> Unit:
    return Unit(scale, dims, _sym_text(sym), sym)

def base(name: str) -> Unit:
    if name not in _BASE:
        raise KeyError(name)
    scale, dims = _BASE[name]
    sym = () if name == "1" else ((name, 1),)
    return Unit(scale, _norm(dims), name, sym)

def power(u: Unit, p: int) -> Unit:
    return _make(u.scale ** p, _norm({k: v * p for k, v in u.dims}), _norm({n: e * p for n, e in u.sym}))

DIMENSIONLESS = base("1")

def known_units() -> list[str]:
    return sorted(_BASE)
