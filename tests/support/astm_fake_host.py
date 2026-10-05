from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass

from lab_analyzer_simulator.protocols.astm.records import parse_records
from lab_analyzer_simulator.transports.astm.framing import (
    ACK,
    ENQ,
    EOT,
    NAK,
    STX,
    AstmFrame,
    AstmFrameError,
)


@dataclass(slots=True, frozen=True)
class ReceivedFrame:
    number: int
    final: bool
    payload: bytes


class AstmFakeHost:
    """In-process LIS speaking CLSI LIS01-A2 framing and LIS02-A2 records, for wire-level tests.

    It is written from the standard, not from the simulator, so a test passing here means the
    simulator interoperates with a conforming host:

    * answers a ``Q`` session with ``worklist_records`` - standard field positions, one record per
      frame, its own ENQ...EOT session;
    * keeps every frame it receives (number, ETB/ETX, payload) so framing can be asserted;
    * serves any number of sessions per connection (persistent link) and any number of connections;
    * ``connect_to=(host, port)`` makes it the LIS that dials an analyzer running in server role.
    """

    def __init__(
        self,
        worklist_records: list[str] | None = None,
        *,
        nak_first_frame: bool = False,
        close_after_session: bool = False,
        contention_once: bool = False,
        connect_to: tuple[str, int] | None = None,
        encoding: str = "latin-1",
    ):
        self.worklist_records = worklist_records or []
        self.nak_first_frame = nak_first_frame
        self.close_after_session = close_after_session
        self.contention_once = contention_once
        self.connect_to = connect_to
        self.encoding = encoding
        self.received_queries: list[str] = []
        self.received_results: list[str] = []
        self.raw_payloads: list[bytes] = []
        self.frames: list[ReceivedFrame] = []
        self.events: list[str] = []
        self.connections = 0
        self.error: Exception | None = None
        self._stop = threading.Event()
        self._nak_pending = nak_first_frame
        self._contention_pending = contention_once
        self._active: socket.socket | None = None
        self._server: socket.socket | None = None
        if connect_to is None:
            self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server.bind(("127.0.0.1", 0))
            self._server.listen(5)
            self._server.settimeout(0.2)
            self.host, self.port = "127.0.0.1", self._server.getsockname()[1]
        else:
            self.host, self.port = connect_to
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def wait_for(self, predicate, timeout: float = 3.0) -> bool:
        """Result sessions end at EOT with no reply, so the analyzer returns before this thread has
        recorded the payload; tests wait for the host instead of racing it."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

    def close(self) -> None:
        self._stop.set()
        for sock in (self._active, self._server):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self._thread.join(3)

    # ── connection handling ──────────────────────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            if self.connect_to is not None:
                self._handle(self._dial())
                return
            while not self._stop.is_set():
                try:
                    connection, _ = self._server.accept()
                except TimeoutError:
                    continue
                except OSError:
                    return
                self._handle(connection)
        except Exception as error:  # surfaced through self.error and asserted by tests
            if not self._stop.is_set():
                self.error = error

    def _dial(self) -> socket.socket:
        # The analyzer binds its listener lazily on its first session; retry until it appears.
        deadline = time.monotonic() + 5
        while True:
            try:
                return socket.create_connection(self.connect_to, timeout=1)
            except OSError:
                if time.monotonic() > deadline or self._stop.is_set():
                    raise
                time.sleep(0.05)

    def _handle(self, connection: socket.socket) -> None:
        self.connections += 1
        self._active = connection
        with connection:
            connection.settimeout(0.2)
            while not self._stop.is_set():
                try:
                    control = connection.recv(1)
                except TimeoutError:
                    continue
                except OSError:
                    return
                if not control:
                    self.events.append("PEER CLOSED")
                    return
                if control != ENQ:
                    raise ValueError(f"fake host expected ENQ to open a session, got {control!r}")
                if self._contention_pending:
                    # Both sides want the line: the host answers ENQ with ENQ, then yields
                    # (LIS01-A2).
                    self._contention_pending = False
                    self.events.append("RX ENQ -> TX ENQ (contention)")
                    connection.sendall(ENQ)
                    continue
                self.events.append("RX ENQ")
                connection.sendall(ACK)
                connection.settimeout(3)
                raw = self._receive_frames(connection)
                connection.settimeout(0.2)
                self.raw_payloads.append(raw)
                payload = raw.decode(self.encoding, errors="replace")
                if any(record.record_type == "Q" for record in parse_records(payload)):
                    self.received_queries.append(payload)
                    self._send_session(connection, self.worklist_records)
                else:
                    self.received_results.append(payload)
                if self.close_after_session:
                    self.events.append("HOST CLOSED")
                    return

    def _receive_frames(self, connection: socket.socket) -> bytes:
        payload = bytearray()
        expected = 1
        while True:
            control = self._read(connection)
            if control == EOT:
                self.events.append("RX EOT")
                return bytes(payload)
            if control != STX:
                raise ValueError(f"unexpected control byte {control!r}")
            encoded = self._read_frame_after_stx(connection)
            try:
                frame = AstmFrame.decode(STX + encoded)
            except AstmFrameError:
                self.events.append("RX bad frame -> NAK")
                connection.sendall(NAK)
                continue
            if frame.number != expected:
                if frame.number == (expected - 1) % 8:
                    connection.sendall(ACK)  # repeated frame: acknowledge, do not append twice
                    continue
                connection.sendall(NAK)
                continue
            if self._nak_pending:
                self._nak_pending = False
                self.events.append("TX NAK (requested)")
                connection.sendall(NAK)
                continue
            self.frames.append(ReceivedFrame(frame.number, frame.final, frame.payload))
            payload.extend(frame.payload)
            expected = (expected + 1) % 8
            connection.sendall(ACK)

    def _send_session(self, connection: socket.socket, records: list[str]) -> None:
        connection.sendall(ENQ)
        if self._read(connection) != ACK:
            raise ValueError("fake host expected ACK for its ENQ")
        for index, record in enumerate(records):
            # One record per frame, as conforming instruments and hosts send them.
            frame = AstmFrame((index + 1) % 8, (record + "\r").encode(self.encoding), True).encode()
            connection.sendall(frame)
            if self._read(connection) != ACK:
                raise ValueError("fake host expected ACK for its frame")
        connection.sendall(EOT)

    @staticmethod
    def _read(connection: socket.socket) -> bytes:
        value = connection.recv(1)
        if not value:
            raise ConnectionError("fake host connection closed")
        return value

    @staticmethod
    def _read_frame_after_stx(connection: socket.socket) -> bytes:
        data = bytearray()
        while True:
            value = connection.recv(1)
            if not value:
                raise ConnectionError("fake host connection closed in frame")
            data.extend(value)
            if data.endswith(b"\r\n"):
                return bytes(data)
