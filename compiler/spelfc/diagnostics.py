class SpelfError(Exception):

    def __init__(self, rule: str, message: str, filename: str = "<input>",
                 line: int = 0, col: int = 0, note: str | None = None):
        super().__init__(message)
        self.rule = rule
        self.message = message
        self.filename = filename
        self.line = line
        self.col = col
        self.note = note

    def render(self) -> str:
        loc = f"{self.filename}:{self.line}:{self.col}"
        out = f"{loc}: error [{self.rule}] {self.message}"
        if self.note:
            out += "\n    " + self.note.replace("\n", "\n    ")
        return out
