import unittest

from lab_analyzer_simulator.domain import ResultValue, Settings, Worklist
from lab_analyzer_simulator.protocols.astm.protocol import AstmProtocol
from lab_analyzer_simulator.protocols.astm.records import AstmProtocolError, parse_records


def record(message: str, record_type: str):
    return next(item for item in parse_records(message) if item.record_type == record_type)


class AstmProtocolTests(unittest.TestCase):
    """Field positions are asserted with the standard's own numbering (CLSI LIS02-A2), never by
    re-reading the simulator's output with the simulator's parser alone."""

    def setUp(self) -> None:
        self.protocol = AstmProtocol(
            Settings(protocol="astm", analyzer_name="SIM-CHEM", receiver_application="STM.LIS")
        )

    # ── query ────────────────────────────────────────────────────────────────────────────────

    def test_query_uses_lis02_positions(self) -> None:
        message = self.protocol.build_query("SAMPLE-001", "QRY-1")
        self.assertEqual(["H", "Q", "L"], [item.record_type for item in parse_records(message)])
        query = message.split("\r")[1].split("|")
        self.assertEqual("^SAMPLE-001", query[2], "Q-3 = patient ID^specimen ID")
        self.assertEqual("^^^ALL", query[4], "Q-5 = universal test ID")
        self.assertEqual("O", query[12], "Q-13 = O (request orders)")

    def test_header_uses_lis02_positions(self) -> None:
        header = self.protocol.build_query("SAMPLE-001", "QRY-1").split("\r")[0].split("|")
        self.assertEqual("\\^&", header[1], "H-2 delimiter definition")
        self.assertEqual("QRY-1", header[2], "H-3 message control ID")
        self.assertTrue(header[4].startswith("SIM-CHEM^"), "H-5 sender name^version")
        self.assertEqual("STM.LIS", header[9], "H-10 receiver ID")
        self.assertEqual(["P", "LIS2-A2"], header[11:13], "H-12 processing ID, H-13 version")
        self.assertRegex(header[13], r"^\d{14}$", "H-14 date/time YYYYMMDDHHMMSS")

    # ── worklist from a conforming host ──────────────────────────────────────────────────────

    def standard_worklist(self, *orders: str) -> str:
        return "\r".join(
            [
                "H|\\^&|||STM.LIS|||||||P|LIS2-A2|20261005103000",
                "P|1||BN0001||NGUYEN^AN||19800101|M",
                *orders,
                "L|1|N",
                "",
            ]
        )

    def test_worklist_reads_tests_from_o5_and_specimen_from_o3(self) -> None:
        message = self.standard_worklist(
            "O|1|SAMPLE-001^R1^3||^^^GLU\\^Creatinine^^CREA|S||||||A||||SERUM||||||||||O"
        )
        worklist = self.protocol.parse_worklist(message, "SAMPLE-001")
        self.assertEqual(["GLU", "CREA"], [test.code for test in worklist.ordered_tests])
        self.assertEqual(
            "Creatinine", worklist.ordered_tests[1].name, "component 2 is the test name"
        )
        self.assertEqual(
            "SAMPLE-001", worklist.accession_number, "O-3 component 1, rack/position ignored"
        )
        self.assertEqual("BN0001", worklist.patient_id, "P-3")
        self.assertEqual("NGUYEN AN", worklist.patient_name, "P-6 last^first")
        self.assertEqual("S", worklist.metadata["priority"], "O-6")
        self.assertEqual("SERUM", worklist.specimen, "O-16")

    def test_worklist_merges_one_o_record_per_test(self) -> None:
        message = self.standard_worklist(
            "O|1|SAMPLE-001||^^^GLU|R", "O|2|SAMPLE-001||^^^ALT|R", "O|3|SAMPLE-001||^^^GLU|R"
        )
        worklist = self.protocol.parse_worklist(message, "SAMPLE-001")
        self.assertEqual(["GLU", "ALT"], [test.code for test in worklist.ordered_tests])

    def test_worklist_for_another_specimen_is_rejected(self) -> None:
        with self.assertRaisesRegex(AstmProtocolError, "O-3=OTHER-SAMPLE"):
            self.protocol.parse_worklist(
                self.standard_worklist("O|1|OTHER-SAMPLE||^^^GLU|R"), "SAMPLE-001"
            )

    def test_host_without_orders_answers_q13_x(self) -> None:
        message = "\r".join(["H|\\^&|||STM.LIS", "Q|1|^SAMPLE-001||^^^ALL||||||||X", "L|1|N", ""])
        with self.assertRaisesRegex(AstmProtocolError, "no orders"):
            self.protocol.parse_worklist(message, "SAMPLE-001")

    def test_empty_response_session_is_reported(self) -> None:
        with self.assertRaisesRegex(AstmProtocolError, "empty session"):
            self.protocol.parse_worklist("", "SAMPLE-001")

    def test_tests_outside_o5_are_not_guessed(self) -> None:
        # Regression: v0.2.2 read tests from O-6, so a standard "R" priority became a test named
        # "R".
        # Its own non-standard layout (tests in O-6) must now be refused, not guessed at.
        with self.assertRaisesRegex(AstmProtocolError, "O-5"):
            self.protocol.parse_worklist(
                self.standard_worklist("O|1|SAMPLE-001|ORDER-1||WBC^White Blood Cell|R"),
                "SAMPLE-001",
            )
        # A host that sends the plain code in O-5 (no ^^^) is still read: component 1 is the
        # fallback.
        worklist = self.protocol.parse_worklist(
            self.standard_worklist("O|1|SAMPLE-001||WBC^White Blood Cell|R"), "SAMPLE-001"
        )
        self.assertEqual(
            [("WBC", "White Blood Cell")],
            [(test.code, test.name) for test in worklist.ordered_tests],
        )

    def test_custom_delimiters_declared_in_h2_are_honoured(self) -> None:
        message = "\r".join(
            ["H!@#$!!!STM.LIS", "P!1!!BN0001", "O!1!SAMPLE-001!!###GLU@###ALT!R", "L!1!N", ""]
        )
        worklist = self.protocol.parse_worklist(message, "SAMPLE-001")
        self.assertEqual(["GLU", "ALT"], [test.code for test in worklist.ordered_tests])

    # ── result ───────────────────────────────────────────────────────────────────────────────

    def result_message(self, *results: ResultValue, **worklist_fields) -> str:
        worklist = Worklist.from_dict(
            {
                "sample_identifier": "SAMPLE-001",
                "accession_number": "SAMPLE-001",
                "patient_id": "BN0001",
                "patient_name": "NGUYEN AN",
                "specimen": "SERUM",
                "ordered_tests": [{"code": item.code, "name": item.name} for item in results],
                **worklist_fields,
            }
        )
        message, _ = self.protocol.build_result(worklist, list(results))
        return message

    def test_result_records_use_lis02_positions(self) -> None:
        message = self.result_message(
            ResultValue("GLU", "Glucose", "5.4", "mmol/L", "3.9-6.1", "N", "F"),
            ResultValue("ALT", "ALT", ">1500", "U/L", "0-40", ">", "F"),
        )
        self.assertEqual(
            ["H", "P", "O", "R", "R", "L"], [item.record_type for item in parse_records(message)]
        )
        order = next(line for line in message.split("\r") if line.startswith("O|")).split("|")
        self.assertEqual("SAMPLE-001", order[2], "O-3 specimen ID")
        self.assertEqual("^^^GLU\\^^^ALT", order[4], "O-5 universal test IDs")
        self.assertEqual("R", order[5], "O-6 priority")
        self.assertEqual("SERUM", order[15], "O-16 specimen descriptor")
        self.assertEqual("F", order[25], "O-26 report type F = final results")
        first, second = [line.split("|") for line in message.split("\r") if line.startswith("R|")]
        self.assertEqual(["^^^GLU", "5.4", "mmol/L", "3.9 to 6.1", "N"], first[2:7], "R-3..R-7")
        self.assertEqual("F", first[8], "R-9 result status")
        self.assertRegex(first[12], r"^\d{14}$", "R-13 date/time completed")
        self.assertEqual("SIM-CHEM", first[13], "R-14 instrument identification")
        self.assertEqual(
            [">1500", ">"], [second[3], second[6]], "comparator kept in R-4, flag in R-7"
        )

    def test_delimiters_inside_values_are_escaped(self) -> None:
        message = self.result_message(ResultValue("GLU", "GLU", "5.4"), patient_name="A|B^C\\D&E")
        patient_line = next(line for line in message.split("\r") if line.startswith("P|"))
        self.assertEqual("A&F&B&S&C&R&D&E&E", patient_line.split("|")[5])
        self.assertEqual("A|B^C\\D&E", record(message, "P").component(6, 1))

    def test_acknowledgement_is_session_level(self) -> None:
        self.assertEqual(("AA", "ASTM-FRAME-ACK"), self.protocol.parse_acknowledgement(""))


if __name__ == "__main__":
    unittest.main()
