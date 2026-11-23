"""Minimal VT screen: enough CSI to render Claude Code's TUI for screenshots.

Handles printable text, CR/LF/BS/TAB, cursor moves, ED/EL, scroll regions,
insert/delete lines and chars; ignores SGR, mode switches and OSC. Good enough
to read a permission dialog; not a general terminal.
"""


class Screen:
    def __init__(self, rows=60, cols=200):
        self.rows, self.cols = rows, cols
        self.cells = [[" "] * cols for _ in range(rows)]
        self.r = self.c = 0
        self.top, self.bottom = 0, rows - 1
        self.saved = (0, 0)

    def _scroll_up(self, n=1):
        for _ in range(n):
            del self.cells[self.top]
            self.cells.insert(self.bottom, [" "] * self.cols)

    def _scroll_down(self, n=1):
        for _ in range(n):
            del self.cells[self.bottom]
            self.cells.insert(self.top, [" "] * self.cols)

    def _lf(self):
        if self.r == self.bottom:
            self._scroll_up(1)
        elif self.r < self.rows - 1:
            self.r += 1

    def _put(self, ch):
        if self.c >= self.cols:
            self.c = 0
            self._lf()
        self.cells[self.r][self.c] = ch
        self.c += 1

    def _csi(self, params, final):
        parts = [p for p in params.replace("?", " ").split(";")]

        def num(i, default):
            try:
                value = int(parts[i])
            except (IndexError, ValueError):
                return default
            return value if value else default

        if final in "Hf":
            self.r = min(self.rows - 1, num(0, 1) - 1)
            self.c = min(self.cols - 1, num(1, 1) - 1)
        elif final == "A":
            self.r = max(0, self.r - num(0, 1))
        elif final == "B":
            self.r = min(self.rows - 1, self.r + num(0, 1))
        elif final == "C":
            self.c = min(self.cols - 1, self.c + num(0, 1))
        elif final == "D":
            self.c = max(0, self.c - num(0, 1))
        elif final == "E":
            self.r = min(self.rows - 1, self.r + num(0, 1)); self.c = 0
        elif final == "F":
            self.r = max(0, self.r - num(0, 1)); self.c = 0
        elif final == "G":
            self.c = min(self.cols - 1, num(0, 1) - 1)
        elif final == "d":
            self.r = min(self.rows - 1, num(0, 1) - 1)
        elif final == "J":
            mode = num(0, 0)
            if mode == 2 or mode == 3:
                self.cells = [[" "] * self.cols for _ in range(self.rows)]
            elif mode == 0:
                self._erase_line(0)
                for r in range(self.r + 1, self.rows):
                    self.cells[r] = [" "] * self.cols
            elif mode == 1:
                for r in range(self.r):
                    self.cells[r] = [" "] * self.cols
                self._erase_line(1)
        elif final == "K":
            self._erase_line(num(0, 0))
        elif final == "L":
            for _ in range(num(0, 1)):
                del self.cells[self.bottom]
                self.cells.insert(self.r, [" "] * self.cols)
        elif final == "M":
            for _ in range(num(0, 1)):
                del self.cells[self.r]
                self.cells.insert(self.bottom, [" "] * self.cols)
        elif final == "P":
            row = self.cells[self.r]
            del row[self.c:self.c + num(0, 1)]
            row.extend([" "] * num(0, 1))
        elif final == "@":
            row = self.cells[self.r]
            for _ in range(num(0, 1)):
                row.insert(self.c, " ")
            del row[self.cols:]
        elif final == "S":
            self._scroll_up(num(0, 1))
        elif final == "T":
            self._scroll_down(num(0, 1))
        elif final == "r":
            self.top = max(0, num(0, 1) - 1)
            self.bottom = min(self.rows - 1, num(1, self.rows) - 1)
            self.r = self.c = 0
        elif final == "s":
            self.saved = (self.r, self.c)
        elif final == "u":
            self.r, self.c = self.saved

    def _erase_line(self, mode):
        row = self.cells[self.r]
        if mode == 0:
            for c in range(self.c, self.cols):
                row[c] = " "
        elif mode == 1:
            for c in range(self.c + 1):
                row[c] = " "
        else:
            self.cells[self.r] = [" "] * self.cols

    def feed(self, text):
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch == "\x1b":
                if i + 1 < n and text[i + 1] == "[":
                    j = i + 2
                    while j < n and not (0x40 <= ord(text[j]) <= 0x7E):
                        j += 1
                    if j < n:
                        self._csi(text[i + 2:j], text[j])
                        i = j + 1
                    else:
                        i = n
                    continue
                if i + 1 < n and text[i + 1] in "]PX^_":
                    kind = text[i + 1]
                    j = i + 2
                    while j < n and text[j] not in "\x07" and text[j] != "\x1b":
                        j += 1
                    if kind == "]":
                        if j < n and text[j] == "\x07":
                            i = j + 1
                        elif j + 1 < n and text[j + 1] == "\\":
                            i = j + 2
                        else:
                            i = j
                    else:
                        if j + 1 < n and text[j] == "\x1b" and text[j + 1] == "\\":
                            i = j + 2
                        else:
                            i = j
                    continue
                if i + 1 < n:
                    esc = text[i + 1]
                    if esc == "7":
                        self.saved = (self.r, self.c)
                    elif esc == "8":
                        self.r, self.c = self.saved
                    elif esc == "D":
                        self._lf()
                    elif esc == "E":
                        self._lf(); self.c = 0
                    elif esc == "M":
                        if self.r == self.top:
                            self._scroll_down(1)
                        elif self.r > 0:
                            self.r -= 1
                    elif esc in "()#":
                        if i + 2 < n:
                            i += 1
                    i += 2
                    continue
                i += 1
                continue
            if ch == "\n":
                self._lf()
            elif ch == "\r":
                self.c = 0
            elif ch == "\x08":
                self.c = max(0, self.c - 1)
            elif ch == "\t":
                self.c = min(self.cols - 1, (self.c // 8 + 1) * 8)
            elif ch >= " " and ch != "\x7f":
                self._put(ch)
            i += 1

    def text(self):
        lines = ["".join(row).rstrip() for row in self.cells]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)
