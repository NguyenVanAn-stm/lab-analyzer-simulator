# Protocol contract

## MLLP framing

Every message is sent as:

```text
0x0B + HL7 segments separated by CR + 0x1C 0x0D
```

The transport rejects missing start/end blocks, over-sized responses, socket failures, and read timeouts.

## Query

The simulator sends `QRY^R02` with the scanned sample identifier in the query detail. A host may answer with a `DSR^Q03` containing a worklist. The parser also accepts an ORC/OBR/PID-shaped worklist response for simple test hosts.

## Result

The simulator sends `ORU^R01` with PID, ORC, OBR, and one OBX per ordered test. OBX carries the value type in OBX-2 (`NM` number, `SN` comparator value such as `>^1500`, `ST` text), the value in OBX-5, units/range/flag in OBX-6..8, the result status in OBX-11, the observation time in OBX-14 and the analyzer name in OBX-18. The query's QRD puts the quantity limit `1^RD` in QRD-7, the specimen ID in QRD-8 and `RES` in QRD-9. Each normal result has a unique message-control ID. The `duplicate` scenario intentionally reuses the prior result ID so duplicate handling can be tested explicitly.

## Acknowledgement

`AA` and `CA` are accepted. Other ACK codes are treated as result rejection. Missing or invalid ACK messages are transport/protocol failures.

This is a focused analyzer contract, not a complete HL7 v2 implementation. ASTM is documented separately in `docs/astm.md`. Add a versioned contract test before relying on fields outside the documented message shapes.
