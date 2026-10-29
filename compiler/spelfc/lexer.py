from dataclasses import dataclass

from .diagnostics import SpelfError

KEYWORDS = {
    "module", "fn", "extern", "let", "var", "if", "else", "for", "in", "return",
    "match", "sealed", "enum", "const", "config", "mode_lattice", "flight_phase",
    "requires", "ensures", "effects", "recursive", "halt", "true", "false",
    "narrow", "convert", "invariant", "state", "fault_hypothesis", "encoded", "task", "protected", "struct", "redundancy", "isr",
}

PUNCT = [
    "..=", "::", "->", "=>", "==", "!=", "<=", ">=", "&&", "||", "..",
    "{", "}", "(", ")", "[", "]", "<", ">", ",", ";", ":", ".", "=",
    "+", "-", "*", "/", "%", "!", "@", "|", "^", "&",
]

@dataclass
class Token:
    kind: str
    text: str
    line: int
    col: int

    def __repr__(self):
        return f"{self.kind}:{self.text!r}@{self.line}:{self.col}"

def lex(src: str, filename: str = "<input>") -> list[Token]:
    toks: list[Token] = []
    i, line, col = 0, 1, 1
    n = len(src)

    def adv(k: int):
        nonlocal i, line, col
        for _ in range(k):
            if src[i] == "\n":
                line += 1
                col = 1
            else:
                col += 1
            i += 1

    while i < n:
        c = src[i]
        if c in " \t\r\n":
            adv(1)
            continue
        if src.startswith("//", i):
            while i < n and src[i] != "\n":
                adv(1)
            continue
        start_line, start_col = line, col
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            word = src[i:j]
            toks.append(Token("KW" if word in KEYWORDS else "IDENT", word, start_line, start_col))
            adv(j - i)
            continue
        if c.isdigit():
            j = i
            while j < n and (src[j].isdigit() or src[j] == "_"):
                j += 1
            is_float = False
            if j < n and src[j] == "." and j + 1 < n and src[j + 1].isdigit():
                is_float = True
                j += 1
                while j < n and (src[j].isdigit() or src[j] == "_"):
                    j += 1
            if j < n and src[j] in "eE":
                k = j + 1
                if k < n and src[k] in "+-":
                    k += 1
                if k < n and src[k].isdigit():
                    is_float = True
                    j = k
                    while j < n and src[j].isdigit():
                        j += 1
            text = src[i:j].replace("_", "")
            toks.append(Token("FLOAT" if is_float else "INT", text, start_line, start_col))
            adv(j - i)
            continue
        if c == '"':
            j = i + 1
            while j < n and src[j] != '"':
                if src[j] == "\n":
                    raise SpelfError("LEX", "unterminated string", filename, start_line, start_col)
                j += 1
            if j >= n:
                raise SpelfError("LEX", "unterminated string", filename, start_line, start_col)
            toks.append(Token("STRING", src[i + 1:j], start_line, start_col))
            adv(j + 1 - i)
            continue
        for p in PUNCT:
            if src.startswith(p, i):
                toks.append(Token("PUNCT", p, start_line, start_col))
                adv(len(p))
                break
        else:
            raise SpelfError("LEX", f"unexpected character {c!r}", filename, start_line, start_col)
    toks.append(Token("EOF", "", line, col))
    return toks
