import pathlib
import struct
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from spelfc import checker, codegen, parser

TARGETS = [
    ("LEON3 (SPARC V8)", ["-target", "sparc-freestanding", "-mcpu=leon3"]),
    ("RISC-V RV32", ["-target", "riscv32-freestanding", "-mcpu=generic_rv32"]),
    ("ARM Cortex-R5", ["-target", "thumb-freestanding", "-mcpu=cortex_r5"]),
]

def text_size(obj: pathlib.Path) -> int:
    data = obj.read_bytes()
    assert data[:4] == b"\x7fELF", "not an ELF file"
    endian = "<" if data[5] == 1 else ">"
    if data[4] != 1:
        raise ValueError("only ELF32 is supported")
    shoff = struct.unpack_from(endian + "I", data, 0x20)[0]
    shentsize, shnum, shstrndx = struct.unpack_from(endian + "HHH", data, 0x2E)
    secs = [struct.unpack_from(endian + "IIIIII", data, shoff + i * shentsize) for i in range(shnum)]
    strtab_off = secs[shstrndx][4]
    total = 0
    for name_off, _type, _flags, _addr, _off, size in secs:
        end = data.index(b"\0", strtab_off + name_off)
        name = data[strtab_off + name_off:end].decode()
        if name == ".text" or name.startswith(".text."):
            total += size
    return total

def gen(src_path: pathlib.Path, force64: bool) -> str:
    prog = parser.parse(src_path.read_text(encoding="utf-8"), src_path.name)
    ck = checker.check(prog, src_path.name)
    if force64:
        saved = (codegen.arith_ctype, codegen.storage_ctype)
        codegen.arith_ctype = lambda t: "int64_t"
        codegen.storage_ctype = lambda t: "int64_t"
        try:
            return codegen.generate(ck, freestanding=True)
        finally:
            codegen.arith_ctype, codegen.storage_ctype = saved
    return codegen.generate(ck, freestanding=True)

def compile_obj(csrc: str, flags: list, d: pathlib.Path, tag: str) -> pathlib.Path:
    c = d / f"{tag}.c"
    o = d / f"{tag}.o"
    c.write_text(csrc, encoding="utf-8")
    cmd = [sys.executable, "-m", "ziglang", "cc", *flags, "-O2", "-std=c11", "-ffreestanding",
           "-ffunction-sections", "-c", str(c), "-o", str(o)]
    cp = subprocess.run(cmd, capture_output=True, text=True)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr)
    return o

def main():
    for stream in (sys.stdout,):
        stream.reconfigure(encoding="utf-8", errors="replace")
    srcs = [pathlib.Path(a) for a in sys.argv[1:]] or [HERE.parent / "tests" / "ok" / "ccsds_primary_header.spelf"]
    for src in srcs:
        base, opt = gen(src, True), gen(src, False)
        print(f"\n{src.name}  (-O2, .text bytes)")
        print(f"  {'target':<18} {'baseline (int64)':>14} {'spelf':>8} {'diff':>8}")
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            for name, flags in TARGETS:
                a = text_size(compile_obj(base, flags, d, "base"))
                b = text_size(compile_obj(opt, flags, d, "opt"))
                print(f"  {name:<18} {a:>14} {b:>8} {100 * (b - a) / a:>7.1f}%")

if __name__ == "__main__":
    main()
