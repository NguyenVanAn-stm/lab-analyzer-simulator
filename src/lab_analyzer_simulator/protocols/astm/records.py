from __future__ import annotations

from dataclasses import dataclass

# CLSI LIS02-A2 (formerly ASTM E1394) record types. M (manufacturer) and S (scientific) are accepted
# so a host that sends them does not break parsing; the simulator never generates them.
RECORD_TYPES = frozenset({"H", "P", "O", "R", "C", "Q", "L", "M", "S"})


class AstmProtocolError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class Delimiters:
    """Delimiters declared in H-2. The defaults are the ones LIS02-A2 recommends: ``|\\^&``."""

    field: str = "|"
    repeat: str = "\\"
    component: str = "^"
    escape: str = "&"

    @classmethod
    def from_header(cls, line: str) -> Delimiters:
        # "H|\^&" -> the four characters after the record type define field, repeat, component,
        # escape.
        if len(line) >= 5 and line[0] == "H":
            return cls(line[1], line[2], line[3], line[4])
        return cls()

    def escape_text(self, value: object) -> str:
        """Escape delimiter characters inside a data value (LIS02-A2 section 5.6)."""
        text = str(value).replace("\r", " ").replace("\n", " ")
        e = self.escape
        return (
            text.replace(e, f"{e}E{e}")
            .replace(self.field, f"{e}F{e}")
            .replace(self.repeat, f"{e}R{e}")
            .replace(self.component, f"{e}S{e}")
        )

    def unescape_text(self, value: str) -> str:
        e = self.escape
        return (
            value.replace(f"{e}F{e}", self.field)
            .replace(f"{e}R{e}", self.repeat)
            .replace(f"{e}S{e}", self.component)
            .replace(f"{e}E{e}", e)
        )


DEFAULT_DELIMITERS = Delimiters()


@dataclass(slots=True, frozen=True)
class AstmRecord:
    """One LIS02-A2 record. ``fields[0]`` is field 2 of the record (field 1 is the record type)."""

    record_type: str
    fields: tuple[str, ...]
    delimiters: Delimiters = DEFAULT_DELIMITERS

    def __str__(self) -> str:
        return self.delimiters.field.join((self.record_type, *self.fields))

    @classmethod
    def at(
        cls, record_type: str, values: dict[int, str], delimiters: Delimiters = DEFAULT_DELIMITERS
    ) -> AstmRecord:
        """Build a record from 1-based standard positions, e.g. ``{3: "SAMPLE", 5: "^^^GLU"}``.

        Positions follow the numbering of the standard so a reader can check them against the spec
        without counting empty fields by hand. Missing positions are emitted as empty fields.
        """
        if not values:
            return cls(record_type, (), delimiters)
        last = max(values)
        return cls(
            record_type,
            tuple(values.get(position, "") for position in range(2, last + 1)),
            delimiters,
        )

    def get(self, position: int) -> str:
        """Raw value of the field at the standard 1-based ``position`` (``""`` when absent)."""
        index = position - 2
        return self.fields[index] if 0 <= index < len(self.fields) else ""

    def repeats(self, position: int) -> list[str]:
        raw = self.get(position)
        return [item for item in raw.split(self.delimiters.repeat)] if raw else []

    def components(self, value: str) -> list[str]:
        return [
            self.delimiters.unescape_text(part) for part in value.split(self.delimiters.component)
        ]

    def component(self, position: int, number: int) -> str:
        """Unescaped component ``number`` (1-based) of the first repeat of field ``position``."""
        first = self.repeats(position)[:1]
        if not first:
            return ""
        parts = self.components(first[0])
        return parts[number - 1] if 0 < number <= len(parts) else ""

    @classmethod
    def parse(cls, line: str, delimiters: Delimiters = DEFAULT_DELIMITERS) -> AstmRecord:
        clean = line.rstrip("\r\n")
        if not clean or delimiters.field not in clean:
            raise AstmProtocolError(f"invalid ASTM record: {line!r}")
        record_type, *fields = clean.split(delimiters.field)
        # Some instruments prefix the record type with a frame/sequence digit; the type is the last
        # letter.
        record_type = record_type.strip()[-1:].upper() if record_type.strip() else ""
        if record_type not in RECORD_TYPES:
            raise AstmProtocolError(f"unsupported ASTM record type: {record_type!r}")
        return cls(record_type, tuple(fields), delimiters)


def parse_records(message: str) -> list[AstmRecord]:
    lines = [
        line
        for line in message.replace("\r\n", "\r").replace("\n", "\r").split("\r")
        if line.strip()
    ]
    if not lines:
        raise AstmProtocolError("ASTM payload contains no records")
    header = next((line for line in lines if line.startswith("H")), None)
    delimiters = Delimiters.from_header(header) if header else DEFAULT_DELIMITERS
    return [AstmRecord.parse(line, delimiters) for line in lines]


def serialize_records(records: list[AstmRecord] | tuple[AstmRecord, ...]) -> str:
    if not records:
        raise AstmProtocolError("cannot serialize an empty ASTM record list")
    return "\r".join(str(record) for record in records) + "\r"
