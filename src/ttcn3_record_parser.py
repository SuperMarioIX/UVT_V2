# ttcn_record.py

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

# ============================================================
# Tokenizer
# ============================================================

@dataclass
class Token:
    kind: str
    value: str

    def __repr__(self) -> str:
        return f"Token({self.kind!r}, {self.value!r})"


class TTCNRecordLexer:
    def __init__(self, text: str):
        self.text = text
        self.n = len(text)
        self.i = 0

    def _peek(self) -> str:
        return self.text[self.i] if self.i < self.n else ""

    def _advance(self, k: int = 1) -> None:
        self.i += k

    def _consume_while(self, predicate) -> str:
        start = self.i
        while self.i < self.n and predicate(self.text[self.i]):
            self.i += 1
        return self.text[start:self.i]

    def tokens(self) -> List[Token]:
        toks: List[Token] = []
        while self.i < self.n:
            ch = self._peek()

            # whitespace
            if ch.isspace():
                self._advance()
                continue

            # punctuation / delimiters
            if ch == "{":
                toks.append(Token("LBRACE", ch))
                self._advance()
                continue
            if ch == "}":
                toks.append(Token("RBRACE", ch))
                self._advance()
                continue
            if ch == ",":
                toks.append(Token("COMMA", ch))
                self._advance()
                continue

            # := operator
            if ch == ":" and self.i + 1 < self.n and self.text[self.i + 1] == "=":
                toks.append(Token("COLONEQ", ":="))
                self._advance(2)
                continue

            # string: " .... "
            if ch == '"':
                toks.append(self._read_string())
                continue

            # number: 123, -12.3, 1.2E10 (simplified)
            if ch.isdigit() or (ch == "-" and self.i + 1 < self.n and self.text[self.i + 1].isdigit()):
                toks.append(self._read_number())
                continue

            # identifier: a_done, enable, dn, obj, g_m, etc.
            if ch.isalpha() or ch == "_":
                ident = self._consume_while(lambda c: c.isalnum() or c == "_")
                toks.append(Token("IDENT", ident))
                continue

            # placeholders: ? or *
            if ch in "?*":
                toks.append(Token("PLACEHOLDER", ch))
                self._advance()
                continue

            # anything else: skip
            self._advance()

        return toks

    def _read_string(self) -> Token:
        # initial '"'
        self._advance()
        buf: List[str] = []
        while self.i < self.n:
            ch = self._peek()
            if ch == '"':
                self._advance()
                break
            buf.append(ch)
            self._advance()
        return Token("STRING", "".join(buf))

    def _read_number(self) -> Token:
        start = self.i
        self._advance()
        while self.i < self.n and (self.text[self.i].isdigit() or self.text[self.i] in ".eE+-"):
            self._advance()
        return Token("NUMBER", self.text[start:self.i])


# ============================================================
# Parser
# ============================================================

class TTCNRecordParser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.i = 0

    def _peek(self) -> Optional[Token]:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def _advance(self) -> Optional[Token]:
        tok = self._peek()
        if tok is not None:
            self.i += 1
        return tok

    def _expect(self, kind: str) -> Token:
        tok = self._peek()
        if tok is None or tok.kind != kind:
            raise ValueError(f"Expected {kind}, got {tok}")
        self.i += 1
        return tok

    # ---------- public entry point ----------

    def parse_record(self) -> Any:
        """
        Parse a TTCN-3 record/union/set literal that starts with '{' and ends with '}'.

        Returns either:
          - dict (named fields: key := value, ...)
          - list (positional elements: {value1, value2, ...})
        """
        tok = self._peek()
        if tok is None or tok.kind != "LBRACE":
            raise ValueError(f"Record must start with '{{', got {tok}")
        self._advance()  # consume '{'

        # Empty record
        if self._peek() and self._peek().kind == "RBRACE":
            self._advance()
            return {}

        # Decide: dict-style or list-style based on lookahead
        # If we see IDENT COLONEQ at top level -> dict
        is_dict = self._lookahead_for_coloneq_before_rbrace()

        if is_dict:
            result = self._parse_dict_body()
        else:
            result = self._parse_list_body()

        self._expect("RBRACE")
        return result

    # ---------- helpers ----------

    def _lookahead_for_coloneq_before_rbrace(self) -> bool:
        depth = 0
        j = self.i
        while j < len(self.tokens):
            tok = self.tokens[j]
            if tok.kind == "LBRACE":
                depth += 1
            elif tok.kind == "RBRACE":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and tok.kind == "COLONEQ":
                return True
            j += 1
        return False

    def _parse_dict_body(self) -> dict:
        result: dict = {}
        while True:
            key_tok = self._peek()
            if key_tok is None or key_tok.kind != "IDENT":
                raise ValueError(f"Expected IDENT for key, got {key_tok}")
            key = key_tok.value
            self._advance()
            self._expect("COLONEQ")
            value = self._parse_value()
            result[key] = value

            tok = self._peek()
            if tok is None or tok.kind == "RBRACE":
                break
            if tok.kind == "COMMA":
                self._advance()
                continue
            raise ValueError(f"Unexpected token in dict: {tok}")
        return result

    def _parse_list_body(self) -> list:
        items: list = []
        while True:
            items.append(self._parse_value())
            tok = self._peek()
            if tok is None or tok.kind == "RBRACE":
                break
            if tok.kind == "COMMA":
                self._advance()
                continue
            raise ValueError(f"Unexpected token in list: {tok}")
        return items

    def _parse_value(self) -> Any:
        tok = self._peek()
        if tok is None:
            raise ValueError("Unexpected end of tokens while parsing value")

        if tok.kind == "LBRACE":
            return self.parse_record()

        if tok.kind == "STRING":
            self._advance()
            return tok.value

        if tok.kind == "NUMBER":
            self._advance()
            txt = tok.value
            # simple numeric conversion
            if any(c in txt for c in ".eE"):
                try:
                    return float(txt)
                except ValueError:
                    return txt
            else:
                try:
                    return int(txt)
                except ValueError:
                    return txt

        if tok.kind == "PLACEHOLDER":
            self._advance()
            # return raw placeholder, pruning will handle skipping
            return tok.value

        if tok.kind == "IDENT":
            self._advance()
            ident = tok.value
            low = ident.lower()
            if low == "true":
                return True
            if low == "false":
                return False
            if low == "omit":
                # a TTCN special value; pruning decides what to do with it
                return "omit"
            return ident

        raise ValueError(f"Unexpected token in value: {tok}")


# ============================================================
# Public APIs
# ============================================================

def parse_ttcn_record(text: str) -> Any:
    """
    Parse a TTCN-3 record-like literal (e.g. '{dn:="/X", obj:={...}}')
    into nested Python structures (dicts, lists, scalars).

    Example:
      '{a:=1,b:="x"}' -> {"a": 1, "b": "x"}
    """
    lexer = TTCNRecordLexer(text)
    tokens = lexer.tokens()
    parser = TTCNRecordParser(tokens)
    return parser.parse_record()


# ============================================================
# Pruning helpers (remove ?, *, omit)
# ============================================================

PLACEHOLDER_VALUES = {"?", "*", "omit"}


def prune_placeholders(value: Any) -> Any:
    """
    Recursively remove TTCN placeholders (?, *, omit) from a parsed record.

    Rules:
      - dict: drop entries whose value is a placeholder or pruned to None/empty
      - list: drop items that prune to None/empty
      - scalar: if it's a placeholder, return None; else keep

    If a container becomes empty, returns None.
    """
    # scalars
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and value in PLACEHOLDER_VALUES:
            return None
        return value

    # dict
    if isinstance(value, dict):
        new_d: dict = {}
        for k, v in value.items():
            pruned = prune_placeholders(v)
            if pruned is None:
                continue
            # also drop empty containers
            if isinstance(pruned, (dict, list)) and not pruned:
                continue
            new_d[k] = pruned
        return new_d or None

    # list
    if isinstance(value, list):
        new_l: list = []
        for item in value:
            pruned = prune_placeholders(item)
            if pruned is None:
                continue
            if isinstance(pruned, (dict, list)) and not pruned:
                continue
            new_l.append(pruned)
        return new_l or None

    # unknown type: keep as is
    return value
