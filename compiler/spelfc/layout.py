from .stypes import ArrayT, BoolT, EnumT, FixT, FloatT, InstantT, IntT

I32_MIN, I32_MAX = -(2 ** 31), 2 ** 31 - 1

_SIZE = {"uint8_t": 1, "int8_t": 1, "bool": 1, "uint16_t": 2, "int16_t": 2, "uint32_t": 4, "int32_t": 4,
         "float": 4, "int64_t": 8, "double": 8}

def arith_ctype(t: IntT) -> str:
    return "int32_t" if I32_MIN <= t.lo and t.hi <= I32_MAX else "int64_t"

def storage_ctype(t: IntT) -> str:
    if t.lo >= 0:
        for hi, name in ((255, "uint8_t"), (65535, "uint16_t"), (2 ** 32 - 1, "uint32_t")):
            if t.hi <= hi:
                return name
        return "int64_t"
    for lo, hi, name in ((-128, 127, "int8_t"), (-32768, 32767, "int16_t"), (I32_MIN, I32_MAX, "int32_t")):
        if lo <= t.lo and t.hi <= hi:
            return name
    return "int64_t"

def field_ctype(ty) -> str:
    if isinstance(ty, FixT):
        ty = ty.as_int()
    if isinstance(ty, IntT):
        return storage_ctype(ty)
    if isinstance(ty, FloatT):
        return "float" if ty.bits == 32 else "double"
    if isinstance(ty, InstantT):
        return "double"
    if isinstance(ty, BoolT):
        return "bool"
    if isinstance(ty, EnumT):
        return "int32_t"
    raise ValueError(ty)

def size_align(ty) -> tuple[int, int]:
    if isinstance(ty, ArrayT):
        s, a = size_align(ty.elem)
        return s * ty.n, a
    c = field_ctype(ty)
    return _SIZE[c], _SIZE[c]

def struct_size(fields) -> int:
    off, amax = 0, 1
    for _, fty in fields:
        s, a = size_align(fty)
        off = (off + a - 1) // a * a + s
        amax = max(amax, a)
    return max(1, (off + amax - 1) // amax * amax)
