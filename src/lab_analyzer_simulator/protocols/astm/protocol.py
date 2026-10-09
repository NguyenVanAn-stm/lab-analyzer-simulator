from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from datetime import datetime

from ... import __version__
from ...domain import ResultValue, Worklist
from ..base import AnalyzerProtocol
from .records import (
    DEFAULT_DELIMITERS,
    AstmProtocolError,
    AstmRecord,
    parse_records,
    serialize_records,
)

# LIS02-A2 field positions used by this adapter (1-based, field 1 is the record type).
#   H-3 message control ID   H-5 sender name^version   H-10 receiver ID   H-12 processing ID
#   H-13 version             H-14 date/time of message
#   P-3 practice-assigned patient ID   P-4 laboratory-assigned patient ID   P-6 patient name
# (last^first)
#   O-3 specimen ID (from the LIS)   O-4 instrument specimen ID   O-5 universal test ID (repeats)
#   O-6 priority (S/A/R)   O-16 specimen descriptor   O-26 report type (O = order, F = final)
#   R-3 universal test ID   R-4 value   R-5 units   R-6 reference range   R-7 abnormal flags
#   R-9 result status   R-11 operator   R-13 date/time completed   R-14 instrument ID
#   Q-3 starting range ID (patient ID^specimen ID)   Q-5 universal test ID   Q-13 request status (O
# = orders)
# Universal test ID components: universal ID^name^type^manufacturer's local code -> "^^^GLU".

_RANGE = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*-\s*(-?\d+(?:[.,]\d+)?)\s*$")


class AstmProtocol(AnalyzerProtocol):
    """CLSI LIS02-A2 (ASTM E1394) record adapter for analyzer testing.

    Field positions follow the standard; vendor dialects are out of scope. Hosts that place data
    in non-standard positions are reported as protocol errors instead of being guessed at.
    """

    protocol_id = "astm"

    @staticmethod
    def timestamp() -> str:
        # LIS02-A2 date/time is YYYYMMDDHHMMSS with no time zone; analyzers send their local clock.
        return datetime.now().strftime("%Y%m%d%H%M%S")

    @staticmethod
    def control_id(prefix: str = "ASTM") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:12]}"

    def _esc(self, value: object) -> str:
        return DEFAULT_DELIMITERS.escape_text(value)

    def _header(self, control_id: str) -> AstmRecord:
        return AstmRecord.at(
            "H",
            {
                2: "\\^&",
                3: self._esc(control_id),
                5: f"{self._esc(self.settings.analyzer_name)}^{__version__}",
                10: self._esc(self.settings.receiver_application),
                12: "P",
                13: "LIS2-A2",
                14: self.timestamp(),
            },
        )

    @staticmethod
    def _test_id(code: str) -> str:
        return f"^^^{DEFAULT_DELIMITERS.escape_text(code)}"

    @staticmethod
    def _reference_range(value: str) -> str:
        # LIS02-A2 R-6: "lower to upper", e.g. "3.9 to 6.1". Profiles use "3.9-6.1"; free text
        # passes through.
        match = _RANGE.match(value or "")
        return f"{match.group(1)} to {match.group(2)}" if match else value

    def build_query(self, sample_identifier: str, control_id: str | None = None) -> str:
        if not sample_identifier.strip():
            raise AstmProtocolError("sample_identifier is required")
        records = [
            self._header(control_id or self.control_id("QRY")),
            # Q-3 is "patient ID^specimen ID": the analyzer knows only the specimen it scanned.
            AstmRecord.at(
                "Q", {2: "1", 3: f"^{self._esc(sample_identifier)}", 5: "^^^ALL", 13: "O"}
            ),
            AstmRecord.at("L", {2: "1", 3: "N"}),
        ]
        return serialize_records(records)

    @staticmethod
    def _specimen_id(order: AstmRecord) -> str:
        # O-3 is the LIS specimen ID; it may carry rack/position components ("ID^rack^pos").
        return order.component(3, 1) or order.component(4, 1)

    def parse_worklist(self, message: str, requested_sample: str | None = None) -> Worklist:
        if not message.strip():
            raise AstmProtocolError("host answered the query with an empty session (no worklist)")
        records = parse_records(message)
        if not any(record.record_type == "H" for record in records):
            raise AstmProtocolError("ASTM worklist has no H record")
        orders = [record for record in records if record.record_type == "O"]
        if not orders:
            query = next((record for record in records if record.record_type == "Q"), None)
            status = query.get(13) if query else ""
            if status in {"X", "I"}:
                # Q-13 X = no information available, I = still in progress.
                raise AstmProtocolError(
                    f"host has no orders for {requested_sample or 'the sample'} (Q-13={status})"
                )
            raise AstmProtocolError("ASTM worklist has no O record")

        if requested_sample:
            matching = [order for order in orders if self._specimen_id(order) == requested_sample]
            if not matching:
                received = ", ".join(
                    sorted({self._specimen_id(order) or "(empty)" for order in orders})
                )
                raise AstmProtocolError(
                    f"host returned orders for O-3={received}, not {requested_sample}"
                )
            orders = matching

        tests: list[dict[str, str]] = []
        seen: set[str] = set()
        for order in orders:
            for item in order.repeats(5):
                parts = order.components(item)
                # Manufacturer's local code is component 4; fall back to component 1 for hosts that
                # send "GLU".
                code = (parts[3] if len(parts) > 3 else "") or parts[0]
                if not code or code in seen:
                    continue
                seen.add(code)
                name = (parts[1] if len(parts) > 1 else "") or code
                specimen = order.component(16, 1) or "SERUM"
                tests.append({"code": code, "name": name, "specimen": specimen})
        if not tests:
            raise AstmProtocolError("ASTM order has no tests in O-5 (universal test ID)")

        first = orders[0]
        accession = self._specimen_id(first)
        patient = next((record for record in records if record.record_type == "P"), None)
        patient_id = "SYNTHETIC-PATIENT"
        patient_name = "Synthetic Patient"
        if patient:
            patient_id = (
                patient.component(3, 1)
                or patient.component(4, 1)
                or patient.component(5, 1)
                or patient_id
            )
            name_parts = patient.components(patient.repeats(6)[0]) if patient.repeats(6) else []
            patient_name = " ".join(part for part in name_parts if part) or patient_name
        return Worklist.from_dict(
            {
                "sample_identifier": requested_sample or accession,
                "order_number": first.component(4, 1) or accession,
                "accession_number": accession,
                "patient_id": patient_id,
                "patient_name": patient_name,
                "specimen": tests[0]["specimen"],
                "ordered_tests": tests,
                "metadata": {"priority": first.component(6, 1) or "R"},
            }
        )

    def build_result(
        self,
        worklist: Worklist,
        results: Iterable[ResultValue],
        control_id: str | None = None,
    ) -> tuple[str, str]:
        control_id = control_id or self.control_id("ASTM")
        completed = self.timestamp()
        tests = DEFAULT_DELIMITERS.repeat.join(
            self._test_id(test.code) for test in worklist.ordered_tests
        )
        records = [
            self._header(control_id),
            AstmRecord.at(
                "P",
                {2: "1", 3: self._esc(worklist.patient_id), 6: self._esc(worklist.patient_name)},
            ),
            AstmRecord.at(
                "O",
                {
                    2: "1",
                    3: self._esc(worklist.accession_number),
                    4: self._esc(worklist.sample_identifier),
                    5: tests,
                    6: self._esc(worklist.metadata.get("priority", "R")),
                    16: self._esc(worklist.specimen),
                    26: "F",
                },
            ),
        ]
        for index, result in enumerate(results, start=1):
            records.append(
                AstmRecord.at(
                    "R",
                    {
                        2: str(index),
                        3: self._test_id(result.code),
                        4: self._esc(result.value),
                        5: self._esc(result.units),
                        6: self._esc(self._reference_range(result.reference_range)),
                        7: self._esc(result.abnormal_flag),
                        9: self._esc(result.result_status),
                        11: self._esc(self.settings.default_operator),
                        13: completed,
                        14: self._esc(self.settings.analyzer_name),
                    },
                )
            )
        records.append(AstmRecord.at("L", {2: "1", 3: "N"}))
        return serialize_records(records), control_id

    def parse_acknowledgement(self, message: str) -> tuple[str, str]:
        if not message:
            return "AA", "ASTM-FRAME-ACK"
        records = parse_records(message)
        if not any(record.record_type == "L" for record in records):
            raise AstmProtocolError("ASTM acknowledgement response has no L record")
        return "AA", "ASTM-SESSION-ACK"

    def validate_message(self, message: str) -> None:
        records = parse_records(message)
        if records[0].record_type != "H" or records[-1].record_type != "L":
            raise AstmProtocolError("ASTM payload must start with H and end with L")

    def expects_response(self, operation: str) -> bool:
        return operation == "query"
