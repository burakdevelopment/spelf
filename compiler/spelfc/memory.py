import re

from . import stack

def parse_sections(asm: str) -> dict:
    out, cur, align, pending = {}, None, 1, {}
    sizes = dict((m.group(1), int(m.group(2))) for m in re.finditer(r"^\s*\.size\s+([\w.$]+),\s*(\d+)", asm, re.M))
    for raw in asm.splitlines():
        line = raw.strip()
        m = re.match(r"\.section\s+([\w.$]+)", line)
        if m:
            cur, align = m.group(1), 1
            continue
        if re.match(r"\.(text|data|bss)\b", line):
            cur, align = None, 1
            continue
        m = re.match(r"\.p2align\s+(\d+)", line)
        if m:
            align = 1 << int(m.group(1))
            continue
        m = re.match(r"\.(balign|align)\s+(\d+)", line)
        if m:
            align = int(m.group(2))
            continue
        m = re.match(r"([\w.$]+):$", line)
        if m and cur and cur.startswith(".spelf.") and m.group(1) in sizes:
            out.setdefault(cur, []).append((m.group(1), align, sizes[m.group(1)]))
            align = 1
    return out

def analyze(ck, csrc: str, target: str) -> dict:
    asm = stack.compile_asm(csrc, target)
    secs = parse_sections(asm)
    res = {"target": target, "regions": {}}
    for rname, reg in ck.regions.items():
        objs = secs.get(f".spelf.{rname}", [])
        off, items, problems = 0, [], []
        for sym, al, sz in objs:
            off = (off + al - 1) // al * al
            items.append({"symbol": sym, "offset": off, "align": al, "size": sz})
            if reg["kind"] == "dma" and al < reg["line"]:
                problems.append(f"{sym}: alignment {al} < cache line {reg['line']} (R-205)")
            off += sz
        if reg["kind"] == "dma" and objs:
            off = (off + reg["line"] - 1) // reg["line"] * reg["line"]
        if off > reg["size"]:
            problems.append(f"section {off} B > region {reg['size']} B")
        if off > reg.get("used", 0):
            problems.append(f"the checker's figure ({reg.get('used', 0)} B) is smaller than the real layout ({off} B): "
                            "layout model is wrong, capacity proof invalid")
        res["regions"][rname] = {"kind": reg["kind"], "capacity": reg["size"], "checker": reg.get("used", 0),
                                 "actual": off, "objects": items, "problems": problems, "ok": not problems}
    return res

def render(res: dict) -> str:
    lines = [f"MEMORY REGIONS (R-209), target {res['target']}, object layout compiled with flight flags"]
    if not res["regions"]:
        lines.append("  no region declared")
    for rname, r in res["regions"].items():
        lines.append(f"  {rname} ({r['kind']}): actual {r['actual']} B / capacity {r['capacity']} B "
                     f"(checker {r['checker']} B): {'OK' if r['ok'] else 'ERROR'}")
        for o in r["objects"]:
            lines.append(f"    +{o['offset']:<6} {o['symbol']:<24} {o['size']:>6} B  align {o['align']}")
        for p in r["problems"]:
            lines.append(f"    ! {p}")
    return "\n".join(lines)
