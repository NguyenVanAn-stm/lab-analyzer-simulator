import socket
import unittest

from lab_analyzer_simulator.domain import ResultValue, Settings, Worklist
from lab_analyzer_simulator.protocols.astm.protocol import AstmProtocol
from lab_analyzer_simulator.protocols.astm.records import parse_records
from lab_analyzer_simulator.transports.astm.tcp import AstmTcpError, AstmTcpTransport
from lab_analyzer_simulator.transports.base import TransportFault
from tests.support.astm_fake_host import AstmFakeHost

# A worklist exactly as a conforming LIS sends it (CLSI LIS02-A2 positions): specimen ID in O-3,
# tests in O-5 as "^^^code" repeats, priority in O-6, specimen descriptor in O-16, O-26 = O (order).
STANDARD_WORKLIST = [
    "H|\\^&|||STM.LIS|||||||P|LIS2-A2|20261005103000",
    "P|1||BN0001||NGUYEN^AN||19800101|M",
    "O|1|SAMPLE-001||^^^GLU\\^^^CREA\\^^^ALT|R||||||A||||SERUM||||||||||O",
    "L|1|N",
]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class AstmTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(protocol="astm", analyzer_name="SIM-CHEM")
        self.protocol = AstmProtocol(self.settings)

    def host(self, **options) -> AstmFakeHost:
        host = AstmFakeHost(STANDARD_WORKLIST, **options)
        host.start()
        self.addCleanup(host.close)
        return host

    def transport(self, host: AstmFakeHost, **options) -> AstmTcpTransport:
        settings = {
            "connect_timeout": 2,
            "read_timeout": 2,
            "frame_size": 240,
            "retry_count": 2,
            "checksum": True,
        }
        settings.update(options)
        transport = AstmTcpTransport(host.host, host.port, **settings)
        self.addCleanup(transport.close)
        return transport

    def query_and_result(self, transport: AstmTcpTransport) -> Worklist:
        response = transport.exchange(self.protocol.build_query("SAMPLE-001"), expect_response=True)
        worklist = self.protocol.parse_worklist(response, "SAMPLE-001")
        result, _ = self.protocol.build_result(
            worklist, [ResultValue("GLU", "Glucose", "5.4", "mmol/L", "3.9-6.1")]
        )
        transport.exchange(result, expect_response=False)
        return worklist

    @staticmethod
    def wait_results(host: AstmFakeHost, count: int = 1) -> None:
        if not host.wait_for(lambda: len(host.received_results) >= count):
            raise AssertionError(
                f"host recorded {len(host.received_results)} result sessions, expected {count}"
            )

    def test_query_and_result_share_one_persistent_link(self) -> None:
        host = self.host()
        worklist = self.query_and_result(self.transport(host))
        self.wait_results(host)
        self.assertIsNone(host.error)
        self.assertEqual(["GLU", "CREA", "ALT"], [test.code for test in worklist.ordered_tests])
        self.assertEqual((1, 1), (len(host.received_queries), len(host.received_results)))
        self.assertEqual(1, host.connections, "analyzers keep the TCP link open between sessions")

    def test_each_record_starts_a_new_frame(self) -> None:
        host = self.host()
        self.query_and_result(self.transport(host))
        self.wait_results(host)
        result_records = [record for record in host.received_results[0].split("\r") if record]
        result_frames = host.frames[-len(result_records) :]
        self.assertEqual(len(result_records), len(result_frames))
        for frame, record in zip(result_frames, result_records, strict=True):
            self.assertTrue(frame.final, "a record that fits in one frame ends with ETX")
            self.assertEqual((record + "\r").encode(), frame.payload)

    def test_long_record_continues_in_etb_frames_and_numbers_wrap(self) -> None:
        host = self.host()
        transport = self.transport(host, frame_size=16)
        worklist = Worklist.from_dict(
            {
                "sample_identifier": "SAMPLE-001",
                "ordered_tests": [{"code": f"T{i}"} for i in range(6)],
            }
        )
        result, _ = self.protocol.build_result(
            worklist, [ResultValue(f"T{i}", f"T{i}", "1.0") for i in range(6)]
        )
        transport.exchange(result, expect_response=False)
        self.wait_results(host)
        self.assertIsNone(host.error)
        self.assertTrue(
            any(not frame.final for frame in host.frames), "long records need ETB frames"
        )
        for frame in host.frames:
            # A frame either ends a record (ETX, payload ends with CR) or continues one (ETB).
            self.assertEqual(frame.final, frame.payload.endswith(b"\r"))
        numbers = [frame.number for frame in host.frames]
        self.assertEqual([(index + 1) % 8 for index in range(len(numbers))], numbers)
        self.assertIn(0, numbers, "frame numbers wrap 7 -> 0")
        self.assertEqual(result, host.received_results[0])

    def test_link_closed_by_host_is_reopened_for_the_next_session(self) -> None:
        host = self.host(close_after_session=True)
        self.query_and_result(self.transport(host))
        self.wait_results(host)
        self.assertIsNone(host.error)
        self.assertEqual(2, host.connections)
        self.assertEqual(1, len(host.received_results))

    def test_keep_connection_false_opens_a_link_per_session(self) -> None:
        host = self.host()
        self.query_and_result(self.transport(host, keep_connection=False))
        self.wait_results(host)
        self.assertEqual(2, host.connections)

    def test_host_nak_causes_retransmission(self) -> None:
        host = self.host(nak_first_frame=True)
        response = self.transport(host).exchange(
            self.protocol.build_query("SAMPLE-001"), expect_response=True
        )
        self.assertIn("O|1|SAMPLE-001", response)
        self.assertIn("TX NAK (requested)", host.events)
        self.assertIsNone(host.error)

    def test_injected_bad_checksum_is_recovered_by_a_correct_retransmission(self) -> None:
        host = self.host()
        response = self.transport(host).exchange(
            self.protocol.build_query("SAMPLE-001"),
            TransportFault("invalid-checksum"),
            expect_response=True,
        )
        self.assertIn("RX bad frame -> NAK", host.events)
        self.assertEqual(
            1, len(host.received_queries), "the retransmitted frame must carry a valid checksum"
        )
        self.assertIn("O|1|SAMPLE-001", response)

    def test_line_contention_instrument_waits_and_resends_enq(self) -> None:
        host = self.host(contention_once=True)
        transport = self.transport(host)
        transport.contention_wait_seconds = 0.05
        response = transport.exchange(self.protocol.build_query("SAMPLE-001"), expect_response=True)
        self.assertIn("RX ENQ -> TX ENQ (contention)", host.events)
        self.assertIn("O|1|SAMPLE-001", response)

    def test_server_role_waits_for_the_lis_to_connect(self) -> None:
        port = free_port()
        transport = AstmTcpTransport("127.0.0.1", port, 3, 2, 240, 2, True, role="server")
        self.addCleanup(transport.close)
        host = AstmFakeHost(STANDARD_WORKLIST, connect_to=("127.0.0.1", port))
        host.start()
        self.addCleanup(host.close)
        worklist = self.query_and_result(transport)
        self.wait_results(host)
        self.assertIsNone(host.error)
        self.assertEqual("SAMPLE-001", worklist.accession_number)
        self.assertEqual((1, 1), (len(host.received_queries), len(host.received_results)))

    def test_server_role_reports_when_no_lis_connects(self) -> None:
        transport = AstmTcpTransport("127.0.0.1", free_port(), 0.3, 1, 240, 1, True, role="server")
        self.addCleanup(transport.close)
        with self.assertRaisesRegex(AstmTcpError, "no LIS connected"):
            transport.exchange(self.protocol.build_query("SAMPLE-001"))

    def test_utf8_encoding_carries_vietnamese_names(self) -> None:
        host = self.host(encoding="utf-8")
        transport = self.transport(host, encoding="utf-8")
        worklist = Worklist.from_dict(
            {
                "sample_identifier": "SAMPLE-001",
                "patient_name": "Nguyễn Văn A",
                "ordered_tests": [{"code": "GLU"}],
            }
        )
        result, _ = self.protocol.build_result(worklist, [ResultValue("GLU", "GLU", "5.4")])
        transport.exchange(result, expect_response=False)
        self.wait_results(host)
        self.assertIn("Nguyễn Văn A".encode(), host.raw_payloads[0])
        patient = next(
            record
            for record in parse_records(host.received_results[0])
            if record.record_type == "P"
        )
        self.assertEqual("Nguyễn Văn A", patient.component(6, 1))


if __name__ == "__main__":
    unittest.main()
