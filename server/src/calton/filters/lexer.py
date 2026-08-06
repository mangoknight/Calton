"""Filter string preprocessing and the fexpr lexical scanner.

The preprocessing half is transcribed from ``task_collection_filter.go:177-266``. The
scanner half is a port of ``github.com/ganigeorgiev/fexpr`` v0.6.0 ``scanner.go`` — the
library upstream delegates its whole filter grammar to. Both halves feed
:mod:`calton.filters.parser`.

Error messages are reproduced verbatim from Go rather than rewritten in idiomatic Python,
because they end up on the wire: a parse failure becomes code 4024, whose message
interpolates the parser's own error text (``error.go:1306``).

The filter syntax users write is not the syntax the parser accepts, so two rewrites
happen first:

1. :func:`replace_filter_operators` turns the human operators ``not in`` / ``in`` /
   ``like`` into the sigils ``?!=`` / ``?=`` / ``~``.
2. :func:`preprocess_filter` then quotes bare values, so ``done = false`` becomes
   ``done = 'false'``.

Step 1 is a character scanner rather than a string replace, and that is the whole point:
it must not rewrite an operator that appears **inside a quoted value**, or
``title like 'stuff in progress'`` silently becomes a different filter. Nothing raises
when that goes wrong — the filter still parses, and returns the wrong rows.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

#: Human operator to fexpr sigil, longest first so ``not in`` is not eaten by ``in``.
#: The surrounding spaces are part of the match, exactly as upstream.
FILTER_OPERATOR_SIGILS = (
    (" not in ", " ?!= "),
    (" in ", " ?= "),
    (" like ", " ~ "),
)

#: Field, comparator, then everything up to a boolean operator or bracket.
_COMPARISON = re.compile(r"(\w+)\s*(>=|<=|!=|~|\?=|\?!=|=|>|<)\s*([^&|()]+)")

_QUOTES = ("'", '"')


def _quoted_run_end(filter_string: str, start: int) -> int:
    """Index just past the quoted run opening at ``start``, or -1 if never closed.

    Mirrors fexpr's scanner: both quote characters quote, and a backslash escapes
    whatever follows it.
    """
    quote = filter_string[start]
    index = start + 1
    while index < len(filter_string):
        character = filter_string[index]
        if character == "\\":
            index += 2
            continue
        if character == quote:
            return index + 1
        index += 1
    return -1


def replace_filter_operators(filter_string: str) -> str:
    """Rewrite the human operators to fexpr sigils, leaving quoted runs alone.

    An **unclosed** quote is treated as an ordinary character rather than as the start of
    a run, because bare values may legitimately contain an apostrophe — that is what makes
    ``title = it's cool && done = false`` work. Treating it as an opening quote would make
    the rest of the expression literal and quietly stop rewriting later operators.
    """
    out: list[str] = []
    index = 0

    while index < len(filter_string):
        if filter_string[index] in _QUOTES:
            end = _quoted_run_end(filter_string, index)
            if end > 0:
                out.append(filter_string[index:end])
                index = end
                continue

        for operator, sigil in FILTER_OPERATOR_SIGILS:
            if filter_string.startswith(operator, index):
                out.append(sigil)
                index += len(operator)
                break
        else:
            out.append(filter_string[index])
            index += 1

    return "".join(out)


def _quote_value(match: re.Match[str]) -> str:
    field, comparator, value = match.group(1), match.group(2), match.group(3).strip()

    # Already quoted at both ends with the same character: leave exactly as written.
    #
    # No minimum length here, deliberately. Upstream tests ``HasPrefix && HasSuffix``,
    # and for a value that is a *single* quote character both are true of the same
    # character — so ``title = '`` is passed through unquoted and goes on to fail
    # parsing. Requiring two characters instead escapes it into ``'\''``, a valid
    # expression, and turns Go's 400 into a 200.
    for quote in _QUOTES:
        if value.startswith(quote) and value.endswith(quote):
            return f"{field} {comparator} {value}"

    escaped = value.replace("'", r"\'")
    return f"{field} {comparator} '{escaped}'"


def preprocess_filter(filter_string: str) -> str:
    """Rewrite a user-written filter into the form the parser accepts."""
    return _COMPARISON.sub(_quote_value, replace_filter_operators(filter_string))


# ---------------------------------------------------------------------------
# fexpr scanner (port of scanner.go)
# ---------------------------------------------------------------------------

#: Returned by :meth:`Scanner._read` past the end of the input. Go uses ``rune(0)``; the
#: empty string works better here because it cannot collide with a real character.
_EOF = ""

#: Deepest nesting of function arguments the scanner will follow, as upstream.
MAX_FUNCTION_DEPTH = 3


class FilterExpressionError(Exception):
    """A lexing or parsing failure, carrying Go's message verbatim.

    ``partial_literal`` is the half-scanned literal Go returns alongside the error. Only
    the function-argument scanner reads it, to interpolate it into its wrapped message.
    """

    def __init__(self, message: str, partial_literal: str = "") -> None:
        super().__init__(message)
        self.partial_literal = partial_literal


#: The three sentinel errors parser.go declares by name.
ERR_EMPTY = "empty filter expression"
ERR_INCOMPLETE = "invalid or incomplete filter expression"
ERR_INVALID_COMMENT = "invalid comment"

#: Escapes Go's ``%q`` emits for characters that have a short form.
_GO_SHORT_ESCAPES = {
    "\\": "\\\\",
    "\a": "\\a",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\v": "\\v",
}


def _go_escape(character: str, delimiter: str) -> str:
    if character == delimiter:
        return "\\" + character
    if character in _GO_SHORT_ESCAPES:
        return _GO_SHORT_ESCAPES[character]
    code = ord(character)
    if 0x20 <= code <= 0x7E:
        return character
    if code < 0x100:
        return f"\\x{code:02x}"
    if code < 0x10000:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


def go_quote(value: str) -> str:
    """Render ``value`` the way Go's ``%q`` verb renders a string."""
    return '"' + "".join(_go_escape(character, '"') for character in value) + '"'


def go_quote_rune(character: str) -> str:
    """Render ``character`` the way Go's ``%q`` verb renders a rune — single quotes."""
    return "'" + _go_escape(character, "'") + "'"


class TokenType(StrEnum):
    """Token kinds, with upstream's exact string values (they appear in error text)."""

    UNEXPECTED = "unexpected"
    EOF = "eof"
    WHITESPACE = "whitespace"
    JOIN = "join"
    SIGN = "sign"
    IDENTIFIER = "identifier"
    FUNCTION = "function"
    NUMBER = "number"
    TEXT = "text"
    GROUP = "group"
    COMMENT = "comment"


@dataclass(frozen=True)
class Token:
    """A scanned literal. ``args`` is upstream's ``Meta``, set only for functions."""

    type: TokenType
    literal: str
    args: tuple[Token, ...] | None = None


#: Sign operators the scanner accepts. Calton rejects most of the ``?`` forms later.
SIGN_OPERATORS = frozenset(
    {"=", "!=", "<", "<=", ">", ">=", "~", "!~", "?=", "?!=", "?~", "?!~", "?<", "?<=", "?>", "?>="}
)

JOIN_OPERATORS = frozenset({"&&", "||"})


def _is_whitespace(character: str) -> bool:
    return character in (" ", "\t", "\n")


def _is_letter(character: str) -> bool:
    return ("a" <= character <= "z") or ("A" <= character <= "Z")


def _is_digit(character: str) -> bool:
    return "0" <= character <= "9"


def _is_text_start(character: str) -> bool:
    return character in ("'", '"')


def _is_number_start(character: str) -> bool:
    return character == "-" or _is_digit(character)


def _is_sign_start(character: str) -> bool:
    return character in ("=", "?", "!", ">", "<", "~")


def _is_join_start(character: str) -> bool:
    return character in ("&", "|")


def _is_group_start(character: str) -> bool:
    return character == "("


def _is_comment_start(character: str) -> bool:
    return character == "/"


def _is_identifier_special_start(character: str) -> bool:
    return character in ("@", "_", "#")


def _is_identifier_start(character: str) -> bool:
    return _is_letter(character) or _is_identifier_special_start(character)


def _is_identifier_combine(character: str) -> bool:
    return character in (".", ":")


def _is_valid_identifier(literal: str) -> bool:
    """Reject a trailing ``.``/``:`` and a lone ``@``/``_``/``#``."""
    return (
        len(literal) > 0
        and not _is_identifier_combine(literal[-1])
        and (len(literal) != 1 or not _is_identifier_special_start(literal[0]))
    )


class Scanner:
    """Yields one token per :meth:`scan` call until :attr:`TokenType.EOF`.

    Upstream returns ``(token, error)`` pairs and its parser bails on the first non-nil
    error, so raising at the point of failure is equivalent — with one consequence worth
    knowing: the partially built token Go returns alongside an error is dropped here,
    since nothing ever reads it.
    """

    def __init__(self, data: str) -> None:
        self._data = data
        self._pos = 0
        self._last_rune_size = -1

    def _read(self) -> str:
        if self._pos >= len(self._data):
            self._last_rune_size = -1
            return _EOF
        character = self._data[self._pos]
        self._last_rune_size = 1
        self._pos += 1
        return character

    def _unread_once(self) -> None:
        """Step back one character. A no-op if called twice without an intervening read."""
        if self._last_rune_size < 0 or self._pos < self._last_rune_size:
            return
        self._pos -= self._last_rune_size
        self._last_rune_size = -1

    def scan(self) -> Token:
        character = self._read()

        if character == _EOF:
            return Token(TokenType.EOF, "")
        if _is_whitespace(character):
            self._unread_once()
            return self._scan_whitespace()
        if _is_group_start(character):
            self._unread_once()
            return self._scan_group()
        if _is_identifier_start(character):
            self._unread_once()
            return self._scan_identifier(MAX_FUNCTION_DEPTH)
        if _is_number_start(character):
            self._unread_once()
            return self._scan_number()
        if _is_text_start(character):
            self._unread_once()
            return self._scan_text(preserve_quotes=False)
        if _is_sign_start(character):
            self._unread_once()
            return self._scan_sign()
        if _is_join_start(character):
            self._unread_once()
            return self._scan_join()
        if _is_comment_start(character):
            self._unread_once()
            return self._scan_comment()

        raise FilterExpressionError(f"unexpected character {go_quote_rune(character)}")

    def _scan_whitespace(self) -> Token:
        buffer: list[str] = []
        while True:
            character = self._read()
            if character == _EOF:
                break
            if not _is_whitespace(character):
                self._unread_once()
                break
            buffer.append(character)
        return Token(TokenType.WHITESPACE, "".join(buffer))

    def _scan_number(self) -> Token:
        """Integers and decimals only — no exponents, and ``-`` only in first position."""
        buffer: list[str] = []
        had_dot = False

        while True:
            character = self._read()
            if character == _EOF:
                break
            if (
                not _is_digit(character)
                # a minus sign, but not at the beginning
                and (character != "-" or len(buffer) != 0)
                # a dot, but there was already another dot
                and (character != "." or had_dot)
            ):
                self._unread_once()
                break
            buffer.append(character)
            if character == ".":
                had_dot = True

        literal = "".join(buffer)
        if (len(literal) == 1 and literal[0] == "-") or literal[0] == "." or literal[-1] == ".":
            raise FilterExpressionError(f"invalid number {go_quote(literal)}", literal)

        return Token(TokenType.NUMBER, literal)

    def _scan_text(self, *, preserve_quotes: bool) -> Token:
        """Consume a quoted run. ``preserve_quotes`` keeps the delimiters and the escapes.

        Groups re-scan their contents, so text inside a group is captured with quotes
        intact and unescaped only on that second pass — unescaping twice would turn
        ``'te\\\\'st'`` into something that parses.
        """
        buffer: list[str] = []

        first = self._read()
        if preserve_quotes:
            buffer.append(first)

        escape_next = False
        has_matching_quotes = False

        while True:
            character = self._read()
            if character == _EOF:
                break

            if escape_next:
                escape_next = False
                if not preserve_quotes:
                    if character == "n":
                        buffer.append("\n")
                    elif character == "t":
                        buffer.append("\t")
                    elif character == "r":
                        buffer.append("\r")
                    elif character in ("\\", "'", '"'):
                        buffer.append(character)
                    else:
                        # Not a recognised escape: keep the backslash, which was skipped
                        # rather than written when the escape started.
                        buffer.append("\\")
                        buffer.append(character)
                else:
                    buffer.append(character)
            elif character == "\\":
                escape_next = True
                if preserve_quotes:
                    buffer.append(character)
            elif character == first:
                has_matching_quotes = True
                if preserve_quotes:
                    buffer.append(character)
                break
            else:
                buffer.append(character)

        literal = "".join(buffer)

        if not has_matching_quotes:
            if not preserve_quotes:
                # Put the opening quote back so the message shows what was actually read.
                literal = first + literal
            raise FilterExpressionError(f"invalid quoted text {go_quote(literal)}", literal)

        return Token(TokenType.TEXT, literal)

    def _scan_comment(self) -> Token:
        if not _is_comment_start(self._read()) or not _is_comment_start(self._read()):
            raise FilterExpressionError(ERR_INVALID_COMMENT)

        buffer: list[str] = []
        while True:
            character = self._read()
            if character == _EOF or character == "\n":
                break
            buffer.append(character)

        return Token(TokenType.COMMENT, "".join(buffer).strip())

    def _scan_identifier(self, function_depth: int) -> Token:
        buffer: list[str] = [self._read()]

        while True:
            character = self._read()
            if character == _EOF:
                break

            if character == "(":
                function_name = "".join(buffer)
                if function_depth <= 0:
                    raise FilterExpressionError(
                        f"max nested function arguments reached (max: {MAX_FUNCTION_DEPTH})"
                    )
                if not _is_valid_identifier(function_name):
                    raise FilterExpressionError(f"invalid function name {go_quote(function_name)}")
                self._unread_once()
                return self._scan_function_args(function_name, function_depth)

            if (
                not _is_letter(character)
                and not _is_digit(character)
                and not _is_identifier_combine(character)
                and character != "_"
            ):
                self._unread_once()
                break

            buffer.append(character)

        literal = "".join(buffer)
        if not _is_valid_identifier(literal):
            raise FilterExpressionError(f"invalid identifier {go_quote(literal)}", literal)

        return Token(TokenType.IDENTIFIER, literal)

    def _scan_sign(self) -> Token:
        buffer: list[str] = []
        while True:
            character = self._read()
            if character == _EOF:
                break
            if not _is_sign_start(character):
                self._unread_once()
                break
            buffer.append(character)

        literal = "".join(buffer)
        if literal not in SIGN_OPERATORS:
            raise FilterExpressionError(f"invalid sign operator {go_quote(literal)}")

        return Token(TokenType.SIGN, literal)

    def _scan_join(self) -> Token:
        buffer: list[str] = []
        while True:
            character = self._read()
            if character == _EOF:
                break
            if not _is_join_start(character):
                self._unread_once()
                break
            buffer.append(character)

        literal = "".join(buffer)
        if literal not in JOIN_OPERATORS:
            raise FilterExpressionError(f"invalid join operator {go_quote(literal)}")

        return Token(TokenType.JOIN, literal)

    def _scan_group(self) -> Token:
        """Capture everything between balanced parentheses, without the outer pair.

        Quoted runs are consumed by the text scanner rather than character by character,
        so a ``)`` inside a string does not close the group.
        """
        buffer: list[str] = []

        first = self._read()
        open_groups = 1

        while True:
            character = self._read()
            if character == _EOF:
                break

            if _is_group_start(character):
                open_groups += 1
                buffer.append(character)
            elif _is_text_start(character):
                self._unread_once()
                # A failure here aborts the whole parse, so upstream's "write the errored
                # literal into the buffer and return it" has no observable effect.
                buffer.append(self._scan_text(preserve_quotes=True).literal)
            elif character == ")":
                open_groups -= 1
                if open_groups <= 0:
                    break
                buffer.append(character)
            else:
                buffer.append(character)

        if not _is_group_start(first) or open_groups > 0:
            raise FilterExpressionError(
                f"invalid formatted group - missing {open_groups} closing bracket(s)"
            )

        return Token(TokenType.GROUP, "".join(buffer))

    def _scan_argument(self, scan: Callable[[], Token], kind: str, function_name: str) -> Token:
        """Run an argument scanner, wrapping a failure the way upstream reports it."""
        try:
            return scan()
        except FilterExpressionError as error:
            raise FilterExpressionError(
                f"invalid {kind} argument {go_quote(error.partial_literal)} in function "
                f"{go_quote(function_name)}: {error}"
            ) from error

    def _scan_function_args(self, function_name: str, function_depth: int) -> Token:
        args: list[Token] = []
        expect_comma = False
        is_closed = False

        if self._read() != "(":
            raise FilterExpressionError(
                f"invalid or incomplete function call {go_quote(function_name)}"
            )

        while True:
            character = self._read()
            if character == _EOF:
                break

            if character == ")":
                is_closed = True
                break

            if _is_whitespace(character):
                self._scan_whitespace()
                continue

            if _is_comment_start(character):
                self._unread_once()
                try:
                    self._scan_comment()
                except FilterExpressionError as error:
                    raise FilterExpressionError(
                        f"failed to scan comment in function {go_quote(function_name)}: {error}"
                    ) from error
                continue

            is_comma = character == ","

            if expect_comma and not is_comma:
                raise FilterExpressionError(
                    f"expected comma after the last argument in function {go_quote(function_name)}"
                )
            if not expect_comma and is_comma:
                raise FilterExpressionError(
                    f"unexpected comma in function {go_quote(function_name)}"
                )

            expect_comma = False
            if is_comma:
                continue

            if _is_identifier_start(character):
                self._unread_once()
                args.append(
                    self._scan_argument(
                        lambda: self._scan_identifier(function_depth - 1),
                        "identifier",
                        function_name,
                    )
                )
            elif _is_number_start(character):
                self._unread_once()
                args.append(self._scan_argument(self._scan_number, "number", function_name))
            elif _is_text_start(character):
                self._unread_once()
                args.append(
                    self._scan_argument(
                        lambda: self._scan_text(preserve_quotes=False), "text", function_name
                    )
                )
            else:
                raise FilterExpressionError(
                    f"unsupported argument character {go_quote_rune(character)} in "
                    f"function {go_quote(function_name)}"
                )
            expect_comma = True

        if not is_closed:
            raise FilterExpressionError(
                f"invalid or incomplete function {go_quote(function_name)} (expected ')')"
            )

        return Token(TokenType.FUNCTION, function_name, tuple(args))
