from __future__ import annotations

import os
import socket
import threading
import time

from ...transports.base import AnalyzerTransport, TransportError, TransportFault
from .framing import ACK, ENQ, EOT, NAK, STX, AstmFrame, AstmFrameError


class AstmTcpError(TransportError):
    pass


class AstmTcpTransport(AnalyzerTransport):
    """CLSI LIS01-A2 (ASTM E1381) session over TCP.

    * One record per frame: every LIS02-A2 record starts a new frame. A record longer than
      ``frame_size`` continues in intermediate frames (ETB); the last frame of a record ends
      with ETX.
    * Persistent link (``keep_connection``): analyzers hold the TCP connection open and run
      ENQ...EOT sessions over it. A link the peer has closed is detected before the next session
      and reopened.
    * Role: ``client`` connects to host:port; ``server`` listens on host:port and waits up to
      ``connect_timeout`` for the LIS to connect.
    * Establishment: ENQ answered by ENQ is line contention - the instrument has priority, waits
      ``contention_wait_seconds`` (1 s in LIS01-A2) and sends ENQ again. ENQ answered by NAK means
      the receiver is busy - wait ``busy_wait_seconds`` (10 s) and retry. Both bounded by
      ``retry_count``.
    * Injected frame faults corrupt only the first transmission; retransmissions after NAK carry
      the correct frame, so a host's recovery path can be tested.
    """

    contention_wait_seconds = 1.0
    busy_wait_seconds = 10.0

    def __init__(
        self,
        host: str,
        port: int,
        connect_timeout: float,
        read_timeout: float,
        frame_size: int,
        retry_count: int,
        checksum: bool,
        role: str = "client",
        keep_connection: bool = True,
        encoding: str = "latin-1",
    ):
        if role not in {"client", "server"}:
            raise ValueError("ASTM TCP role must be 'client' or 'server'")
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.frame_size = max(16, frame_size)
        self.retry_count = max(0, retry_count)
        self.checksum = checksum
        self.role = role
        self.keep_connection = keep_connection
        self.encoding = encoding
        self._connection: socket.socket | None = None
        self._listener: socket.socket | None = None
        # One ENQ...EOT session at a time over the shared link (web runs execute in threads).
        self._lock = threading.Lock()

    # ── link management ──────────────────────────────────────────────────────────────────────

    def close(self) -> None:
        with self._lock:
            self._drop()
            if self._listener is not None:
                try:
                    self._listener.close()
                finally:
                    self._listener = None

    def _drop(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except OSError:
                pass
            finally:
                self._connection = None

    def _open(self) -> socket.socket:
        if self._connection is not None:
            if self._alive(self._connection):
                return self._connection
            self._drop()
        self._connection = self._accept() if self.role == "server" else self._connect()
        return self._connection

    @staticmethod
    def _alive(connection: socket.socket) -> bool:
        """False when the peer closed the link. Pending bytes (an ENQ) count as alive."""
        try:
            connection.setblocking(False)
            try:
                return connection.recv(1, socket.MSG_PEEK) != b""
            except BlockingIOError:
                return True
            except OSError:
                return False
        finally:
            try:
                connection.setblocking(True)
            except OSError:
                pass

    def _connect(self) -> socket.socket:
        try:
            return socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        except OSError as error:
            raise AstmTcpError(
                f"ASTM connection failed to {self.host}:{self.port}: {error}"
            ) from error

    def _accept(self) -> socket.socket:
        if self._listener is None:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if os.name != "nt":  # on Windows SO_REUSEADDR lets two processes bind the same port
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind((self.host, self.port))
                listener.listen(1)
            except OSError as error:
                listener.close()
                raise AstmTcpError(
                    f"ASTM server could not listen on {self.host}:{self.port}: {error}"
                ) from error
            self._listener = listener
        self._listener.settimeout(self.connect_timeout)
        try:
            connection, _ = self._listener.accept()
        except TimeoutError as error:
            raise AstmTcpError(
                f"no LIS connected to {self.host}:{self.port} "
                f"within {self.connect_timeout}s (server role)"
            ) from error
        return connection

    # ── session ──────────────────────────────────────────────────────────────────────────────

    def exchange(
        self, message: str, fault: TransportFault | None = None, expect_response: bool = True
    ) -> str:
        fault = fault or TransportFault()
        if fault.kind == "unavailable":
            raise AstmTcpError("simulated ASTM host unavailable")
        with self._lock:
            try:
                reused = self._connection is not None
                connection = self._open()
                connection.settimeout(self.read_timeout)
                try:
                    self._establish(connection, fault)
                except (OSError, AstmTcpError) as error:
                    # The peer may close a held link just after the liveness check (LIS restart,
                    # idle
                    # timeout). Nothing has been sent yet, so reopen once and start the session
                    # again.
                    if not reused or not self._link_down(error):
                        raise
                    self._drop()
                    connection = self._open()
                    connection.settimeout(self.read_timeout)
                    self._establish(connection, fault)
                response = self._transmit(connection, message, fault, expect_response)
            except AstmTcpError:
                self._drop()
                raise
            except (OSError, TimeoutError) as error:
                self._drop()
                raise AstmTcpError(
                    f"ASTM connection failed to {self.host}:{self.port}: {error}"
                ) from error
            # An injected fault can leave stray bytes on the link; start the next session clean.
            if not self.keep_connection or fault.kind != "normal":
                self._drop()
            return response

    @staticmethod
    def _link_down(error: Exception) -> bool:
        if isinstance(error, (ConnectionError, BrokenPipeError)):
            return True
        return isinstance(error, AstmTcpError) and "closed the connection" in str(error)

    def _transmit(
        self, connection: socket.socket, message: str, fault: TransportFault, expect_response: bool
    ) -> str:
        """Frames, EOT and (for queries) the host's reply session, once ENQ/ACK holds the line."""
        if fault.kind == "unexpected-eot":
            connection.sendall(EOT)
            raise AstmTcpError("unexpected EOT injected before ASTM frames")
        if fault.kind == "invalid-record-sequence":
            message = message.replace("H|", "R|", 1)
        self._send_frames(connection, message, fault)
        if fault.kind == "missing-eot":
            return self._receive_message(connection, fault) if expect_response else ""
        if fault.kind == "disconnect":
            raise AstmTcpError("ASTM connection reset after frame transmission")
        connection.sendall(EOT)
        # Result sessions have no application-level reply in LIS01-A2: frame ACKs are the receipt.
        return self._receive_message(connection, fault) if expect_response else ""

    def _establish(self, connection: socket.socket, fault: TransportFault) -> None:
        attempts = 0
        while True:
            connection.sendall(ENQ)
            if fault.kind == "enq-collision" and attempts == 0:
                connection.sendall(ENQ)
            if fault.kind in {"missing-ack", "no-ack"}:
                raise AstmTcpError("ASTM ENQ acknowledgement was intentionally omitted")
            if fault.delay_seconds:
                time.sleep(fault.delay_seconds)
            reply = self._read_control(connection)
            if reply == ACK:
                return
            if reply not in {ENQ, NAK}:
                raise AstmTcpError(f"ASTM expected ACK during ENQ, received {reply!r}")
            attempts += 1
            if attempts > self.retry_count:
                state = (
                    "line contention (host kept sending ENQ)"
                    if reply == ENQ
                    else "host busy (NAK to ENQ)"
                )
                raise AstmTcpError(
                    f"ASTM session not established after {attempts} attempts: {state}"
                )
            time.sleep(self.contention_wait_seconds if reply == ENQ else self.busy_wait_seconds)

    def _frame_payloads(self, message: str) -> list[tuple[bytes, bool]]:
        """Split into (payload, ends-record) chunks: one record per frame, ETB in long records."""
        payloads: list[tuple[bytes, bool]] = []
        for record in (item for item in message.replace("\r\n", "\r").split("\r") if item):
            data = (record + "\r").encode(self.encoding, errors="replace")
            chunks = [
                data[index : index + self.frame_size]
                for index in range(0, len(data), self.frame_size)
            ]
            payloads.extend((chunk, index == len(chunks) - 1) for index, chunk in enumerate(chunks))
        return payloads or [(b"", True)]

    def _send_frames(self, connection: socket.socket, message: str, fault: TransportFault) -> None:
        for index, (chunk, final) in enumerate(self._frame_payloads(message)):
            number = (index + 1) % 8
            good = AstmFrame(number, chunk, final).encode(self.checksum)
            first = good
            if index == 0 and fault.kind == "invalid-checksum":
                first = good[:-4] + (b"00" if good[-4:-2] != b"00" else b"FF") + good[-2:]
            elif index == 0 and fault.kind == "wrong-frame-number":
                first = AstmFrame((number + 1) % 8, chunk, final).encode(self.checksum)
            elif index == 0 and fault.kind == "corrupted-frame":
                first = good[:3] + b"X" + good[4:]
            elif index == 0 and fault.kind == "oversized-frame":
                first = AstmFrame(number, chunk + b"X" * (self.frame_size + 1), final).encode(
                    self.checksum
                )
            self._send_frame_with_retry(connection, first, good, number, fault)

    def _send_frame_with_retry(
        self,
        connection: socket.socket,
        first: bytes,
        good: bytes,
        number: int,
        fault: TransportFault,
    ) -> None:
        attempts = 0
        while True:
            encoded = first if attempts == 0 else good
            if fault.kind == "connection-reset-mid-frame" and number == 1 and attempts == 0:
                connection.sendall(encoded[: max(1, len(encoded) // 2)])
                raise AstmTcpError("connection reset mid ASTM frame")
            connection.sendall(encoded)
            duplicated = fault.kind == "duplicate-frame" and attempts == 0
            if duplicated:
                connection.sendall(encoded)
            if fault.kind == "connection-reset-between-frames" and number == 2:
                raise AstmTcpError("connection reset between ASTM frames")
            response = self._read_control(connection)
            if response == ACK:
                if duplicated:
                    self._read_control(
                        connection
                    )  # the receiver also acknowledges the repeated frame
                return
            if response != NAK:
                raise AstmTcpError(f"ASTM expected ACK/NAK, received {response!r}")
            attempts += 1
            if attempts > self.retry_count:
                raise AstmTcpError(f"ASTM frame {number} exceeded retry count")

    # ── receiving ────────────────────────────────────────────────────────────────────────────

    def _read_control(self, connection: socket.socket) -> bytes:
        try:
            value = connection.recv(1)
        except TimeoutError as error:
            raise AstmTcpError("ASTM control response timed out") from error
        if not value:
            raise AstmTcpError("ASTM host closed the connection")
        return value

    def _receive_message(self, connection: socket.socket, fault: TransportFault) -> str:
        first = self._read_control(connection)
        if first == EOT:
            return ""
        if first != ENQ:
            if first == STX:
                raise AstmTcpError("unexpected ASTM frame before ENQ")
            raise AstmTcpError(f"unexpected ASTM response control {first!r}")
        connection.sendall(ACK)
        return self._receive_frames(connection, fault)

    def _receive_frames(self, connection: socket.socket, fault: TransportFault) -> str:
        payload = bytearray()
        expected_number = 1
        retries = 0
        while True:
            first = self._read_control(connection)
            if first == EOT:
                return bytes(payload).decode(self.encoding, errors="replace")
            if first != STX:
                if first == ENQ:
                    connection.sendall(ACK)
                    continue
                raise AstmTcpError(f"unexpected ASTM response byte {first!r}")
            frame_bytes = self._read_frame_after_stx(connection)
            try:
                frame = AstmFrame.decode(STX + frame_bytes, self.checksum)
                if frame.number != expected_number:
                    if frame.number == (expected_number - 1) % 8:
                        connection.sendall(ACK)  # retransmission of a frame already accepted
                        continue
                    connection.sendall(NAK)
                    retries += 1
                    if retries > self.retry_count:
                        raise AstmTcpError(
                            f"unexpected ASTM frame number {frame.number}, "
                            f"expected {expected_number}"
                        )
                    continue
                payload.extend(frame.payload)
                connection.sendall(ACK)
                expected_number = (expected_number + 1) % 8
                retries = 0
                if fault.kind == "missing-eot" and frame.final:
                    time.sleep(self.read_timeout + 0.05)
            except AstmFrameError as error:
                connection.sendall(NAK)
                retries += 1
                if retries > self.retry_count:
                    raise AstmTcpError(str(error)) from error

    def _read_frame_after_stx(self, connection: socket.socket) -> bytes:
        data = bytearray()
        while True:
            try:
                byte = connection.recv(1)
            except TimeoutError as error:
                raise AstmTcpError("ASTM frame timed out") from error
            if not byte:
                raise AstmTcpError("connection closed mid ASTM frame")
            data.extend(byte)
            if data.endswith(b"\r\n"):
                return bytes(data)
