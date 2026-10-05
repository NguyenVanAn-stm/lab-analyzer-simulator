# Changelog

## Unreleased

- ASTM records now use CLSI LIS02-A2 field positions: tests in O-5 as `^^^code` repeats, Q-3
  `^specimen`, Q-5 `^^^ALL`, Q-13 `O`, result status in R-9, completion time in R-13, instrument in
  R-14, sender in H-5 and message time in H-14. 0.2.2 read tests from O-6, so a conforming host's
  worklist was misread (the priority `R` became a test named `R`).
- Worklist parsing honours H-2 delimiters and escape sequences, merges several O records, rejects
  orders for another specimen, and reports Q-13 `X`/`I` and empty replies as "no orders".
- ASTM transport sends one record per frame, keeps the TCP link open across sessions (reopening a
  link the host closed), supports a server role, handles ENQ contention and busy NAK, sends correct
  frames on retransmission after an injected fault, and supports a configurable byte encoding.
- HL7: QRD-8 now carries the specimen ID; OBX carries status in OBX-11, time in OBX-14, analyzer in
  OBX-18, and `NM`/`SN`/`ST` value types.
- The ASTM fake host used by the tests is rewritten from the standard instead of mirroring the
  simulator's own layout.

## 0.2.2 - 2026-09-27

- Allow manual entry and scanner input for arbitrary live specimen barcodes.
- Keep fixture samples available as datalist suggestions for local tests.
- Document the difference between fixture samples and real live worklists.

## 0.2.1 - 2026-09-27

- Fix HL7 query and result messages so the configured protocol profile is emitted in MSH-21.
- Add regression coverage for the HL7 profile field position.

## 0.2.0 - 2026-09-27

- Initial standalone project.
- HL7 v2.5 over MLLP query, result, and ACK flow.
- ASTM record adapter and real ASTM TCP session/framing implementation.
- Protocol registry/factory, asynchronous run polling, persisted scenario selection, and optional browser camera scanning.
- CBC critical and extended multi-discipline profiles.
- ASTM fake-host wire-level tests and optional browser E2E tests.
- Fixture and live modes.
- Generic analyzer profiles and synthetic worklists.
- Browser UI, REST API, CLI, fault scenarios, Docker packaging, and tests.
