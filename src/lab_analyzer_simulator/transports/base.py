from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class TransportError(ConnectionError):
    pass


@dataclass(slots=True, frozen=True)
class TransportFault:
    kind: str = "normal"
    delay_seconds: float = 0.0


class AnalyzerTransport(ABC):
    @abstractmethod
    def exchange(
        self,
        message: str,
        fault: TransportFault | None = None,
        expect_response: bool = True,
    ) -> str:
        raise NotImplementedError

    def close(self) -> None:  # noqa: B027 - optional hook; transports without a held link need nothing
        """Release a held connection or listener. Called when the engine replaces the transport."""
