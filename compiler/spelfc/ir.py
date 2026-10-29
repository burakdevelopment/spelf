import json

from . import ast as A

_SKIP = {"line", "col"}
_ANN = ("_ty", "_ref", "_callee", "_kind", "_tag", "_bits", "_factor", "_d", "_const_raw", "_enc", "_obj",
        "_cval", "_conv_bits")

def _val(v):
    if isinstance(v, A.Node):
        return node(v)
    if isinstance(v, (list, tuple)):
        return [_val(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _val(x) for k, x in v.items()}
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)

def node(n: A.Node) -> dict:
    out = {"k": type(n).__name__, "at": [n.line, n.col]}
    for f in n.__dataclass_fields__:
        if f in _SKIP:
            continue
        out[f] = _val(getattr(n, f))
    for a in _ANN:
        if hasattr(n, a):
            v = getattr(n, a)
            out[a.lstrip("_")] = str(v) if not isinstance(v, (int, float, str, bool, type(None), tuple)) else \
                (list(v) if isinstance(v, tuple) else v)
    return out

def dump(ck) -> str:
    prog = {"format": "spelf-ir/1",
            "module": ck.module.path if ck.module else None,
            "float_model": ck.float_model,
            "signatures": {name: {"params": [[p, str(t)] for p, t, _ in sig.params], "ret": str(sig.ret),
                                  "modes": sorted(sig.modes)} for name, sig in ck.sigs.items()},
            "items": [node(it) for it in ck.prog.items]}
    return json.dumps(prog, ensure_ascii=False, indent=1)
