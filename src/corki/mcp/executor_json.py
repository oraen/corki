"""Executor Value visitor: exact numbers and one shared allocation budget."""

import re
from json.decoder import scanstring

from corki.protocol.wire_numbers import WireNumber

_SPACE = re.compile(r"[ \t\r\n]*")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_NUMBER_KEY = "$serde_json::private::Number"
_RAW_KEY = "$serde_json::private::RawValue"


class ExecutorJSONError(ValueError):
    """Controlled diagnostics never include raw packet content."""


class ExecutorValueReader:
    """Charge before allocating each value, including values decoded from RawValue."""

    def __init__(self, text: str, budget: list[int]) -> None:
        self.text, self.budget = text, budget
        self.position = 0

    def read(self, *, charge_root: bool = True) -> object:
        result = self._value(0, charge=charge_root)
        if self._peek():
            raise ExecutorJSONError("invalid executor JSON trailing content")
        return result

    def _peek(self) -> str:
        self.position = _SPACE.match(self.text, self.position).end()
        return self.text[self.position : self.position + 1]

    def _take(self, expected: str) -> None:
        if self._peek() != expected:
            raise ExecutorJSONError("invalid executor JSON syntax")
        self.position += 1

    def _string(self) -> str:
        if self._peek() != '"':
            raise ExecutorJSONError("executor JSON key or private value must be a string")
        value, self.position = scanstring(self.text, self.position + 1, True)
        value.encode("utf-8")
        return value

    def _value(self, depth: int, *, charge: bool = True) -> object:
        if charge:
            if self.budget[0] == 0:
                raise ExecutorJSONError("executor RPC message exceeds value limit")
            self.budget[0] -= 1
        char = self._peek()
        if char in ("{", "["):
            if depth >= 127:
                raise ExecutorJSONError("executor JSON nesting limit exceeded")
            return self._object(depth) if char == "{" else self._array(depth)
        if char == '"':
            return self._string()
        for token, value in (("true", True), ("false", False), ("null", None)):
            if self.text.startswith(token, self.position):
                self.position += len(token)
                return value
        match = _NUMBER.match(self.text, self.position)
        if match is None:
            raise ExecutorJSONError("invalid executor JSON value")
        self.position = match.end()
        token = match.group()
        # The arbitrary-precision Value visitor parses -0 through i64, unlike
        # serde's raw typed-integer decoder. Do not borrow Responses' raw rule.
        return WireNumber(token).value()

    def _object(self, depth: int) -> object:
        self._take("{")
        result = {}
        if self._peek() == "}":
            self.position += 1
            return result
        while True:
            key = self._string()
            self._take(":")
            if not result and key in (_NUMBER_KEY, _RAW_KEY):
                encoded = self._string()
                self._take("}")
                if key == _NUMBER_KEY:
                    return WireNumber(encoded).value()
                # The wrapper already consumed the root node. Its payload string
                # is not another Value node; decoded children share the outer budget.
                return ExecutorValueReader(encoded, self.budget).read(charge_root=False)
            if key in result:
                raise ExecutorJSONError("duplicate executor JSON key")
            result[key] = self._value(depth + 1)
            if self._peek() == "}":
                self.position += 1
                return result
            self._take(",")

    def _array(self, depth: int) -> list:
        self._take("[")
        result = []
        if self._peek() == "]":
            self.position += 1
            return result
        while True:
            result.append(self._value(depth + 1))
            if self._peek() == "]":
                self.position += 1
                return result
            self._take(",")
