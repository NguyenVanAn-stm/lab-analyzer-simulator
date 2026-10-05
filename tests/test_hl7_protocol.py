import unittest
from pathlib import Path

from lab_analyzer_simulator.domain import ResultValue, Settings, Worklist
from lab_analyzer_simulator.protocols.hl7 import Hl7Protocol, Hl7ProtocolError


class Hl7ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.protocol = Hl7Protocol(Settings())

    def test_query_contains_sample_and_mllp_compatible_segments(self) -> None:
        message = self.protocol.query_message("SAMPLE-CBC-001", "QRY-TEST-001")
        self.assertTrue(message.startswith("MSH|"))
        self.assertIn("QRY^R02", message)
        self.assertIn("SAMPLE-CBC-001", message)
        self.assertTrue(message.endswith("\r"))

    def test_query_places_protocol_profile_in_msh_21(self) -> None:
        message = self.protocol.query_message("SAMPLE-CBC-001", "QRY-TEST-001")
        msh = message.split("\r", 1)[0].split("|")

        self.assertGreaterEqual(len(msh), 21)
        self.assertEqual(self.protocol.settings.protocol_profile, msh[20])

    def test_result_places_result_profile_in_msh_21(self) -> None:
        worklist = Worklist.from_dict(
            {
                "sample_identifier": "SAMPLE-1",
                "ordered_tests": [{"code": "HGB", "name": "Hemoglobin"}],
            }
        )
        message, _ = self.protocol.result_message(
            worklist, [ResultValue("HGB", "Hemoglobin", "14.2", "g/dL", "12-17")]
        )
        msh = message.split("\r", 1)[0].split("|")

        self.assertGreaterEqual(len(msh), 21)
        self.assertEqual("lab.analyzer.result.v1", msh[20])

    def test_result_contains_one_obx_per_result_and_unique_default_id(self) -> None:
        worklist = Worklist.from_dict(
            {
                "sample_identifier": "SAMPLE-1",
                "ordered_tests": [{"code": "HGB", "name": "Hemoglobin"}],
            }
        )
        result, control_id = self.protocol.result_message(
            worklist, [ResultValue("HGB", "Hemoglobin", "14.2", "g/dL", "12-17")]
        )
        self.assertIn("ORU^R01", result)
        self.assertIn("OBX|1|NM|HGB^Hemoglobin", result)
        self.assertTrue(control_id.startswith("RES-"))

    def test_query_puts_specimen_in_qrd_8(self) -> None:
        message = self.protocol.query_message("SAMPLE-CBC-001", "QRY-TEST-001")
        qrd = next(line for line in message.split("\r") if line.startswith("QRD|")).split("|")
        self.assertEqual(["R", "I", "QRY-TEST-001"], qrd[2:5], "QRD-2 format, QRD-3 priority, QRD-4 query ID")
        self.assertEqual("1^RD", qrd[7], "QRD-7 quantity limited request")
        self.assertEqual("SAMPLE-CBC-001", qrd[8], "QRD-8 who subject filter")
        self.assertEqual("RES", qrd[9], "QRD-9 what subject filter")

    def test_obx_uses_hl7_positions_and_value_types(self) -> None:
        worklist = Worklist.from_dict({"sample_identifier": "SAMPLE-1", "ordered_tests": [{"code": "HGB"}]})
        message, _ = self.protocol.result_message(
            worklist,
            [
                ResultValue("HGB", "Hemoglobin", "14.2", "g/dL", "12-17", "N", "F"),
                ResultValue("ALT", "ALT", ">1500", "U/L", "0-40", ">", "F"),
                ResultValue("HBSAG", "HBsAg", "Negative", "", "", "N", "F"),
            ],
        )
        hgb, alt, hbsag = [line.split("|") for line in message.split("\r") if line.startswith("OBX|")]
        self.assertEqual(["NM", "HGB^Hemoglobin", "", "14.2", "g/dL", "12-17", "N"], hgb[2:9], "OBX-2..OBX-8")
        self.assertEqual("F", hgb[11], "OBX-11 observation result status")
        self.assertRegex(hgb[14], r"^\d{14}$", "OBX-14 date/time of the observation")
        self.assertEqual(self.protocol.settings.analyzer_name, hgb[18], "OBX-18 equipment instance identifier")
        self.assertEqual(["SN", ">^1500"], [alt[2], alt[5]], "comparator values are structured numerics")
        self.assertEqual(["ST", "Negative"], [hbsag[2], hbsag[5]], "text results are ST")

    def test_ack_parser_accepts_aa_and_rejects_missing_msa(self) -> None:
        ack = self.protocol.acknowledgement("RES-1")
        self.assertEqual(("AA", "RES-1"), self.protocol.parse_ack(ack))
        with self.assertRaises(Hl7ProtocolError):
            self.protocol.parse_ack("MSH|^~\\&|A|B|C|D|20260101000000||ACK^R01|1|P|2.5\r")

    def test_oru_worklist_parser_reads_patient_and_tests(self) -> None:
        response = "\r".join(
            [
                "MSH|^~\\&|HOST|LAB|SIM|SIM|20260101000000||DSR^Q03|1|P|2.5",
                "MSA|AA|QRY-1|Accepted",
                "PID|1||PAT-1||Synthetic^Patient",
                "ORC|RE|ORDER-1|ACCESSION-1||CM",
                "OBR|1|ORDER-1|ACCESSION-1|HGB^Hemoglobin|||20260101000000||||||||WHOLE_BLOOD_EDTA",
                "",
            ]
        )
        worklist = self.protocol.parse_worklist(response)
        self.assertEqual("PAT-1", worklist.patient_id)
        self.assertEqual("Synthetic Patient", worklist.patient_name)
        self.assertEqual("HGB", worklist.ordered_tests[0].code)

    def test_dsp_worklist_parser_reads_the_common_query_response_shape(self) -> None:
        response = "\r".join(
            [
                "MSH|^~\\&|HOST|LAB|SIM|SIM|20260101000000||DSR^Q03|1|P|2.5",
                "MSA|AA|QRY-1|Accepted",
                "DSP|1||ORDER-2",
                "DSP|2||SAMPLE-CBC-001",
                "DSP|3||ACCESSION-2",
                "DSP|4||Synthetic Patient",
                "DSP|5||PATIENT-2",
                "DSP|7||PANEL^CBC^WBC^White Blood Cell~PANEL^CBC^HGB^Hemoglobin",
                "",
            ]
        )
        worklist = self.protocol.parse_worklist(response)
        self.assertEqual("ORDER-2", worklist.order_number)
        self.assertEqual("SAMPLE-CBC-001", worklist.sample_identifier)
        self.assertEqual(["WBC", "HGB"], [test.code for test in worklist.ordered_tests])


if __name__ == "__main__":
    unittest.main()
