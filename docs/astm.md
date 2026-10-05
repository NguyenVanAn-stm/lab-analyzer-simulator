# ASTM/LIS2 protocol

The simulator implements CLSI LIS01-A2 (ASTM E1381) sessions and CLSI LIS02-A2 (ASTM E1394) records. It does not claim compatibility with a specific analyzer vendor or model.

## Records

Records follow CLSI LIS02-A2 (formerly ASTM E1394) field positions. Positions below use the
standard's 1-based numbering (field 1 is the record type), so they can be checked against the spec.

```text
H|\^&|QRY-1||Generic Analyzer^0.2.2|||||LAB_HOST||P|LIS2-A2|20261005103000
Q|1|^SAMPLE-ID||^^^ALL||||||||O
P|1|PATIENT-ID|||LAST^FIRST
O|1|SAMPLE-ID|SAMPLE-ID|^^^GLU\^^^CREA|R||||||||||SERUM||||||||||F
R|1|^^^GLU|5.4|mmol/L|3.9 to 6.1|N||F||||20261005103500|Generic Analyzer
L|1|N
```

| Field | Content |
| --- | --- |
| H-2 | Delimiter definition (`\^&`: field `\|`, repeat `\`, component `^`, escape `&`) |
| H-3 / H-5 / H-10 | Message control ID / sender name^version / receiver ID |
| H-12 / H-13 / H-14 | Processing ID `P` / version `LIS2-A2` / date-time `YYYYMMDDHHMMSS` (local) |
| Q-3 | Starting range ID `patient ID^specimen ID`; the analyzer sends `^SAMPLE-ID` |
| Q-5 / Q-13 | Universal test ID `^^^ALL` / request status `O` (orders) |
| P-3 / P-6 | Patient ID / name `last^first` |
| O-3 / O-4 | Specimen ID from the LIS / instrument specimen ID |
| O-5 | Universal test IDs repeated with `\`: `^^^GLU\^^^CREA` (code in component 4) |
| O-6 / O-16 / O-26 | Priority `S`/`A`/`R` / specimen descriptor / report type (`O` order, `F` final) |
| R-3 / R-4 / R-5 | Universal test ID `^^^GLU` / value (comparators such as `>1500` kept) / units |
| R-6 / R-7 / R-9 | Reference range `lower to upper` / abnormal flag / result status |
| R-13 / R-14 | Date-time completed / instrument identification |

Queries use `H`, `Q`, `L`. Worklist responses use `H`, `P`, `O`, `L`; several `O` records for the
same specimen are merged. Results use `H`, `P`, `O`, one `R` per result, and `L`.

Worklist parsing reads tests only from O-5 (component 4, falling back to component 1 for hosts that
send a bare code). Orders whose O-3 is not the scanned specimen are rejected, and a `Q` reply with
Q-13 `X` (no information) or `I` (in progress) is reported as "host has no orders". Delimiters
declared in H-2 are honoured, and delimiter characters inside values are escaped as `&F&`, `&S&`,
`&R&`, `&E&`.

Version 0.2.2 placed tests in O-6, the sample in Q-3 without `^`, the result status in R-8 and the
sender in H-3; those layouts are no longer produced or accepted.

## Session and framing

The TCP session uses:

```text
ENQ  0x05  request to transmit
ACK  0x06  positive response
NAK  0x15  negative response / retry
STX  0x02  frame start
ETB  0x17  intermediate frame terminator
ETX  0x03  final frame terminator
EOT  0x04  session end
CRLF 0x0D 0x0A frame suffix
```

A frame is:

```text
STX + frame-number + payload + ETB/ETX + two ASCII checksum characters + CRLF
```

The checksum is the modulo-256 sum of frame-number, payload, and ETB/ETX, formatted as two uppercase hexadecimal characters. Frames are acknowledged individually. NAK causes retransmission up to `astm_retry_count`. Repeated previous frames are acknowledged without duplicating their payload. Query responses begin a second ENQ session after the query EOT.

- **One record per frame.** Every record starts a new frame. A record longer than `astm_frame_size`
  continues in intermediate frames (ETB); the frame that ends a record ends with ETX. Frame numbers
  start at 1 and wrap 7 -> 0 across the whole message.
- **Persistent link.** With `astm_keep_connection=true` (default) the TCP connection stays open and
  each query or result is its own ENQ...EOT session over it, as analyzers do. A link the host has
  closed is detected before the next session; if it drops during ENQ the simulator reopens once.
- **Client or server.** `astm_tcp_role=client` (default) connects to `gateway_host:gateway_port`.
  `astm_tcp_role=server` listens on `gateway_host:gateway_port` and waits up to
  `connect_timeout_seconds` for the LIS to connect; use `0.0.0.0` to accept a remote LIS.
- **Contention and busy.** ENQ answered by ENQ is line contention: the instrument has priority,
  waits 1 s and sends ENQ again. ENQ answered by NAK means the host is busy: wait 10 s and retry.
  Both are bounded by `astm_retry_count`.
- **Fault scenarios** corrupt only the first transmission of a frame; the retransmission after NAK
  is correct, so the host's recovery path can be exercised.
- **Encoding.** LIS02-A2 is 8-bit. `astm_encoding=latin-1` (default) round-trips every byte; set
  `utf-8` when the host exchanges UTF-8 names (for example Vietnamese).

## Configuration

Use environment variables or `config.example.json`:

```text
SIMULATOR_PROTOCOL=astm
LAB_SIM_MODE=live
LAB_SIM_GATEWAY_HOST=127.0.0.1
LAB_SIM_GATEWAY_PORT=5000
LAB_SIM_ASTM_FRAME_SIZE=240
LAB_SIM_ASTM_RETRY_COUNT=3
LAB_SIM_ASTM_CHECKSUM=true
LAB_SIM_ASTM_TCP_ROLE=client      # or server
LAB_SIM_ASTM_KEEP_CONNECTION=true
LAB_SIM_ASTM_ENCODING=latin-1     # or utf-8
```

One Docker image supports both HL7 and ASTM. Use `SIMULATOR_PROTOCOL=hl7` or `SIMULATOR_PROTOCOL=astm`; no separate image is required.

## Troubleshooting

- `ASTM control response timed out`: verify the host sends ACK after ENQ and every frame.
- `checksum mismatch`: capture the complete STX-to-CRLF frame and compare the two checksum characters with the modulo-256 sum.
- `unexpected frame number`: check whether the host retransmitted a prior frame; the simulator ACKs the immediately previous frame and NAKs other unexpected numbers.
- `connection closed mid ASTM frame`: inspect host reset behavior and frame-size limits.
- Empty result response: normal ASTM result sessions may end after frame ACKs and EOT; query sessions must return a second ASTM worklist session.
