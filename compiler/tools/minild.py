import struct

R_SPARC = {3: "32", 6: "DISP32", 7: "WDISP30", 8: "WDISP22", 9: "HI22", 11: "13", 12: "LO10"}
SHF_ALLOC, SHF_EXEC = 0x2, 0x4
SHT_NOBITS, SHT_RELA, SHT_SYMTAB = 8, 4, 2

class _Obj:
    def __init__(self, data: bytes):
        if data[:4] != b"\x7fELF" or data[4] != 1:
            raise ValueError("not ELF32")
        self.data = data
        self.e = e = ">" if data[5] == 2 else "<"
        etype, = struct.unpack(e + "H", data[16:18])
        if etype != 1:
            raise ValueError("expected a relocatable object (ET_REL)")
        shoff, = struct.unpack(e + "I", data[32:36])
        shentsize, shnum, self.shstrndx = struct.unpack(e + "HHH", data[46:52])
        self.secs = [struct.unpack(e + "IIIIIIIIII", data[shoff + i * shentsize: shoff + i * shentsize + 40])
                     for i in range(shnum)]
        self.place = {}
        symsec = next(i for i, s in enumerate(self.secs) if s[1] == SHT_SYMTAB)
        st = self.secs[symsec]
        stb = self.secs[st[6]][4]
        self.syms = []
        for j in range(st[5] // 16):
            so = st[4] + j * 16
            nm, val, _, info, _, shndx = struct.unpack(e + "IIIBBH", data[so:so + 16])
            name = data[stb + nm: data.index(b"\0", stb + nm)].decode()
            self.syms.append((name, shndx, val, info >> 4))

    def secname(self, i):
        o = self.secs[self.shstrndx][4] + self.secs[i][0]
        return self.data[o:self.data.index(b"\0", o)].decode()

def link(objs, base: int = 0x4000_0000):
    if isinstance(objs, (bytes, bytearray)):
        objs = [objs]
    objs = [_Obj(o) for o in objs]
    alloc = [(k, i) for k, ob in enumerate(objs) for i, s in enumerate(ob.secs) if s[2] & SHF_ALLOC]
    rank = lambda ob, i: 0 if ob.secs[i][2] & SHF_EXEC else (2 if ob.secs[i][1] == SHT_NOBITS else 1)
    alloc.sort(key=lambda ki: (rank(objs[ki[0]], ki[1]), ki[0], ki[1]))
    addr = base
    for k, i in alloc:
        s = objs[k].secs[i]
        al = max(s[8], 1)
        addr = (addr + al - 1) // al * al
        objs[k].place[i] = addr
        addr += s[5]
    end = (addr + 0xFFF) & ~0xFFF
    image = bytearray(end - base)
    for k, i in alloc:
        ob, s = objs[k], objs[k].secs[i]
        if s[1] != SHT_NOBITS:
            image[ob.place[i] - base: ob.place[i] - base + s[5]] = ob.data[s[4]:s[4] + s[5]]

    def value(ob, shndx, val):
        if shndx == 0:
            return None
        if shndx == 0xFFF1:
            return val
        return ob.place[shndx] + val if shndx in ob.place else 0

    glob = {}
    for ob in objs:
        for name, shndx, val, bind in ob.syms:
            if name and shndx != 0 and bind in (1, 2):
                if name in glob and bind == 1 and glob[name][1] == 1:
                    raise ValueError(f"duplicate symbol: {name}")
                if name not in glob or bind == 1:
                    glob[name] = (value(ob, shndx, val), bind)
    syms = {n: v for n, (v, _) in glob.items()}
    for ob in objs:
        for name, shndx, val, bind in ob.syms:
            if name and shndx != 0 and name not in syms:
                syms[name] = value(ob, shndx, val)
        for i, s in enumerate(ob.secs):
            if s[1] != SHT_RELA or s[7] not in ob.place:
                continue
            tgt = s[7]
            for r in range(s[5] // 12):
                ro = s[4] + r * 12
                off, info, A = struct.unpack(ob.e + "IIi", ob.data[ro:ro + 12])
                name, shndx, val, bind = ob.syms[info >> 8]
                S = value(ob, shndx, val) if shndx != 0 else (glob.get(name, (None,))[0])
                if S is None:
                    raise ValueError(f"undefined symbol: {name}")
                P = ob.place[tgt] + off
                kind = R_SPARC.get(info & 0xFF)
                if kind is None:
                    raise ValueError(f"unsupported SPARC relocation {info & 0xFF} "
                                     f"({ob.secname(tgt)} + {off:#x}, {name})")
                loc = P - base
                word, = struct.unpack(">I", image[loc:loc + 4])
                v = (S + A) & 0xFFFFFFFF
                if kind == "32":
                    word = v
                elif kind == "DISP32":
                    word = (S + A - P) & 0xFFFFFFFF
                elif kind == "WDISP30":
                    word = (word & 0xC0000000) | (((S + A - P) >> 2) & 0x3FFFFFFF)
                elif kind == "WDISP22":
                    word = (word & 0xFFC00000) | (((S + A - P) >> 2) & 0x3FFFFF)
                elif kind == "HI22":
                    word = (word & 0xFFC00000) | ((v >> 10) & 0x3FFFFF)
                elif kind == "13":
                    word = (word & 0xFFFFE000) | (v & 0x1FFF)
                elif kind == "LO10":
                    word = (word & 0xFFFFFC00) | (v & 0x3FF)
                image[loc:loc + 4] = struct.pack(">I", word)
    return {"endian": objs[0].e, "segments": [(base, bytes(image), len(image))], "symbols": syms}
