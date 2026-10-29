import argparse
import pathlib
import re
import sys

from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, Preformatted, SimpleDocTemplate,
                                Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.platypus.flowables import PageBreakIfNotEmpty
from reportlab.pdfgen.canvas import Canvas
import functools
from reportlab.lib.fonts import addMapping

HERE = pathlib.Path(__file__).resolve().parent
FONTS = pathlib.Path("C:/Windows/Fonts")

def register_fonts():
    candidates = [FONTS, pathlib.Path("/usr/share/fonts/truetype/msttcorefonts"),
                  pathlib.Path("/Library/Fonts"), pathlib.Path("/System/Library/Fonts/Supplemental")]
    base = next((d for d in candidates if (d / "times.ttf").exists() or (d / "Times New Roman.ttf").exists()), None)
    if base is None:
        sys.exit("Times New Roman TrueType files not found")
    names = {"TNR": ["times.ttf", "Times New Roman.ttf"], "TNR-B": ["timesbd.ttf", "Times New Roman Bold.ttf"],
             "TNR-I": ["timesi.ttf", "Times New Roman Italic.ttf"],
             "TNR-BI": ["timesbi.ttf", "Times New Roman Bold Italic.ttf"],
             "CNR": ["cour.ttf", "Courier New.ttf"], "CNR-B": ["courbd.ttf", "Courier New Bold.ttf"]}
    for name, files in names.items():
        f = next((base / x for x in files if (base / x).exists()), None)
        if f is None:
            sys.exit(f"font file for {name} not found in {base}")
        pdfmetrics.registerFont(TTFont(name, str(f)))
    addMapping("TNR", 0, 0, "TNR")
    addMapping("TNR", 1, 0, "TNR-B")
    addMapping("TNR", 0, 1, "TNR-I")
    addMapping("TNR", 1, 1, "TNR-BI")
    addMapping("CNR", 0, 0, "CNR")
    addMapping("CNR", 1, 0, "CNR-B")

PAGE_W, PAGE_H = A4
MARGIN = 24 * mm
TEXT_W = PAGE_W - 2 * MARGIN

S = {
    "body": ParagraphStyle("body", fontName="TNR", fontSize=11, leading=14.6, alignment=TA_JUSTIFY,
                           spaceAfter=7),
    "h1": ParagraphStyle("h1", fontName="TNR-B", fontSize=16, leading=20, spaceBefore=4, spaceAfter=12,
                         alignment=TA_LEFT),
    "h2": ParagraphStyle("h2", fontName="TNR-B", fontSize=12.5, leading=16, spaceBefore=10, spaceAfter=5,
                         alignment=TA_LEFT, keepWithNext=1),
    "title": ParagraphStyle("title", fontName="TNR-B", fontSize=30, leading=36, alignment=TA_CENTER),
    "subtitle": ParagraphStyle("subtitle", fontName="TNR", fontSize=14, leading=19, alignment=TA_CENTER),
    "meta": ParagraphStyle("meta", fontName="TNR", fontSize=11, leading=15, alignment=TA_CENTER),
    "caption": ParagraphStyle("caption", fontName="TNR-I", fontSize=9.6, leading=12.2, alignment=TA_JUSTIFY,
                              spaceBefore=3, spaceAfter=10),
    "cell": ParagraphStyle("cell", fontName="TNR", fontSize=9.4, leading=11.6, alignment=TA_LEFT),
    "cellh": ParagraphStyle("cellh", fontName="TNR-B", fontSize=9.4, leading=11.6, alignment=TA_LEFT),
    "toc1": ParagraphStyle("toc1", fontName="TNR", fontSize=11, leading=15, leftIndent=0),
    "toc2": ParagraphStyle("toc2", fontName="TNR", fontSize=10, leading=13, leftIndent=14),
}

def inline(text: str) -> str:
    out = []
    for i, part in enumerate(re.split(r"(`[^`]+`)", text)):
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            code = part[1:-1].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            out.append(f'<font name="CNR" size="9.2">{code}</font>')
        else:
            t = part.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            t = re.sub(r"\*([^*\s][^*]*?)\*", r"<i>\1</i>", t)
            t = re.sub(r"(\d)\^(-?)(\d+)",
                       lambda m: f"{m.group(1)}<super>{chr(0x2212) if m.group(2) else ''}{m.group(3)}</super>", t)
            out.append(t)
    return "".join(out)

class Doc(SimpleDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and getattr(flowable, "_toc", None) is not None:
            level, text = flowable._toc
            key = f"h{id(flowable)}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=level, closed=level > 0)
            self.notify("TOCEntry", (level, text, self.page, key))

def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("TNR", 9.5)
    canvas.drawCentredString(PAGE_W / 2, 13 * mm, str(doc.page))
    canvas.restoreState()

def code_block(lines):
    longest = max((len(l) for l in lines), default=1)
    size = max(6.4, min(8.6, TEXT_W / (0.6 * longest + 2)))
    style = ParagraphStyle("code", fontName="CNR", fontSize=size, leading=size * 1.28, leftIndent=0,
                           spaceBefore=3, spaceAfter=9)
    return Preformatted("\n".join(lines), style)

def table(rows):
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    ncol = max(len(r) for r in cells)
    cells = [r + [""] * (ncol - len(r)) for r in cells]
    def text_w(t, bold=False):
        return pdfmetrics.stringWidth(t, "TNR-B" if bold else "TNR", 9.4)
    minw = []
    for j in range(ncol):
        words = [(w, i == 0) for i, r in enumerate(cells) for w in r[j].replace("`", "").split()]
        minw.append(max((text_w(w, b) for w, b in words), default=10) + 9)
    need = [sum(len(r[j]) for r in cells) for j in range(ncol)]
    spare = TEXT_W - sum(minw)
    if spare < 0:
        widths = [w * TEXT_W / sum(minw) for w in minw]
    else:
        extra = [n ** 1.6 for n in need]
        tot = sum(extra) or 1.0
        widths = [m + spare * e / tot for m, e in zip(minw, extra)]
    data = [[Paragraph(inline(c), S["cellh"] if i == 0 else S["cell"]) for c in r] for i, r in enumerate(cells)]
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEABOVE", (0, 0), (-1, 0), 0.8, "black"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, "black"),
        ("LINEBELOW", (0, -1), (-1, -1), 0.8, "black"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    t.spaceAfter = 8
    return [t]

def figure(path: pathlib.Path, caption: str):
    from reportlab.lib.utils import ImageReader
    if not path.exists():
        return [Paragraph(inline(f"[missing figure {path.name}]"), S["caption"])]
    iw, ih = ImageReader(str(path)).getSize()
    w = TEXT_W
    h = w * ih / iw
    max_h = PAGE_H * 0.52
    if h > max_h:
        h = max_h
        w = h * iw / ih
    img = Image(str(path), width=w, height=h)
    img.hAlign = "CENTER"
    return [KeepTogether([Spacer(1, 4), img, Paragraph(inline(caption), S["caption"])])]

def build(src: pathlib.Path, figdir: pathlib.Path, out: pathlib.Path):
    register_fonts()
    lines = src.read_text(encoding="utf-8").splitlines()
    meta = {}
    story, para, i, first_h1 = [], [], 0, True

    def flush():
        if para:
            story.append(Paragraph(inline(" ".join(para)), S["body"]))
            para.clear()
    body = []
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("@"):
            k, _, v = ln[1:].partition(" ")
            meta[k] = v.strip()
            i += 1
            continue
        body.append(ln)
        i += 1
    story += [Spacer(1, 70 * mm), Paragraph(meta.get("title", ""), S["title"]), Spacer(1, 10 * mm),
              Paragraph(inline(meta.get("subtitle", "")), S["subtitle"]), Spacer(1, 60 * mm),
              Paragraph(inline(meta.get("author", "")), S["subtitle"]), Spacer(1, 4 * mm),
              Paragraph(inline(meta.get("meta", "")), S["meta"]), PageBreak()]
    toc = TableOfContents()
    toc.levelStyles = [S["toc1"], S["toc2"]]
    toc.dotsMinLevel = 0
    story += [Paragraph("Contents", S["h1"]), toc, PageBreak()]
    i = 0
    while i < len(body):
        ln = body[i]
        if ln.startswith("```"):
            flush()
            j = i + 1
            block = []
            while j < len(body) and not body[j].startswith("```"):
                block.append(body[j])
                j += 1
            story.append(code_block(block))
            i = j + 1
            continue
        if ln.startswith("# "):
            flush()
            if not first_h1:
                story.append(PageBreakIfNotEmpty())
            first_h1 = False
            p = Paragraph(inline(ln[2:].strip()), S["h1"])
            p._toc = (0, ln[2:].strip())
            story.append(p)
            i += 1
            continue
        if ln.startswith("## "):
            flush()
            p = Paragraph(inline(ln[3:].strip()), S["h2"])
            p._toc = (1, ln[3:].strip())
            story.append(p)
            i += 1
            continue
        if ln.startswith("[[figure:"):
            flush()
            m = re.match(r"\[\[figure:\s*([^|]+?)\s*\|\s*(.*)\]\]\s*$", ln)
            story += figure(figdir / m.group(1), m.group(2))
            i += 1
            continue
        if ln.startswith("|"):
            flush()
            rows = []
            while i < len(body) and body[i].startswith("|"):
                rows.append(body[i])
                i += 1
            story += table(rows)
            continue
        if not ln.strip():
            flush()
        else:
            para.append(ln.strip())
        i += 1
    flush()
    doc = Doc(str(out), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN,
              bottomMargin=MARGIN, title=f"{meta.get('title', '')}: technical guide", author=meta.get("author", ""),
              subject=meta.get("subtitle", ""))
    doc.multiBuild(story, onFirstPage=lambda c, d: None, onLaterPages=footer,
                   canvasmaker=functools.partial(Canvas, initialFontName="TNR", initialFontSize=11))
    print("wrote", out, f"({doc.page} pages)")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--figures", default=str(HERE.parent.parent / "benchmark" / "figures"))
    ap.add_argument("--out", default=str(HERE / "Spelf-Guide.pdf"))
    a = ap.parse_args()
    build(HERE / "guide_source.txt", pathlib.Path(a.figures), pathlib.Path(a.out))

if __name__ == "__main__":
    main()
