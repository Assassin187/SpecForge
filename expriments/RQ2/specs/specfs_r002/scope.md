# Project scope

MQTT 3.1.1; Server (broker) accepting multiple concurrent client network connections
C99; Linux (POSIX sockets, GCC, GNU make); TCP transport

binary_name:
./mqtt_broker
argv_contract:
./mqtt_broker <port>

## Requirements
- R01: Linux C99 TCP broker, independent multi-file project, concurrent clients, ./mqtt_broker <port>, clean make build and accurate README.
- R02: Accept anonymous CONNECT with nonempty ASCII client ID, Clean Session=1 and no Will; return the exact successful CONNACK. CONNECT must be first; duplicate CONNECT closes that client.
- R03: Accept SUBSCRIBE with one or more filters and requested QoS 0; echo its nonzero Packet Identifier in SUBACK, with one ordered QoS 0 result per filter.
- R04: Route QoS 0 PUBLISH from a connected client to matching connected subscribers, preserving topic and payload and correct wire framing.
- R05: Match case-sensitive exact filters and MQTT + and # wildcards, including empty levels and # matching zero levels; respect topic level boundaries.
- R06: Preserve arbitrary binary payloads, including NUL bytes and empty payloads; do not treat payload as a C string.
- R07: Handle partial TCP frames and multiple coalesced frames in order. Decode outcomes distinguish incomplete, complete and invalid input and account for bytes consumed.
- R08: Process all complete frames already received before handling TCP EOF, including a final PUBLISH immediately followed by shutdown of the sender's write side.
- R09: Respond to a valid PINGREQ with exact PINGRESP; subsequent business traffic continues. Keepalive timeout scheduling is excluded.
- R10: Handle DISCONNECT and TCP disconnect by removing subscriptions and freeing connection state without affecting other clients; new connections never inherit stale subscriptions.
- R11: Validate lengths, required fixed-header flags, packet-specific fields and connection state for in-scope packets. Malformed input closes only the offending connection; the broker remains available.
- R12: Specify and implement ownership of input slices, copied subscriptions, output buffers and connection objects; free all resources on normal SIGINT/SIGTERM termination; no ASan, UBSan or leak diagnostics.

## Capabilities
- TCP listener accepting concurrent client connections on a port given on the command line
- MQTT 3.1.1 fixed-header parsing with variable-length Remaining Length decoding and stream framing that handles partial and coalesced frames
- CONNECT handling: anonymous connection, nonempty ASCII client ID, Clean Session=1, no Will, exact successful CONNACK, CONNECT-first enforcement, duplicate CONNECT closes that client
- SUBSCRIBE handling with one or more Topic Filters and requested QoS 0, producing one ordered QoS 0 SUBACK return code per filter with the echoed Packet Identifier
- Topic Filter matching: case-sensitive exact levels, single-level '+' and multi-level '#' wildcards, empty levels and '/' level boundaries
- QoS 0 PUBLISH routing to all connected subscribers with matching subscriptions, preserving topic and binary payload bytes and re-framing correctly
- PINGREQ/PINGRESP exchange
- DISCONNECT and TCP-disconnect cleanup: removing that connection's subscriptions and state without affecting other clients
- Input validation for lengths, fixed-header flags, packet-specific fields and connection state; malformed input closes only the offending connection while the broker stays available
- Deterministic resource ownership and release of input slices, subscriptions, output buffers and connection objects, including shutdown on SIGINT/SIGTERM

## Exclusions
- QoS 1 and QoS 2 delivery (PUBACK, PUBREC, PUBREL, PUBCOMP flows)
- Retained messages
- Will messages
- Persistent sessions / CleanSession=0 session resumption
- UNSUBSCRIBE / UNSUBACK
- Authentication (User Name / Password)
- TLS transport
- Idle keepalive timeout scheduling
- Complete MQTT compliance (only the subset required by REQUIREMENTS.md)

## Engineering decisions
- Startup contract is the project-relative executable ./mqtt_broker invoked as ./mqtt_broker <port>, where <port> is the TCP listening port passed as the first positional argument.
- Build with GCC and make; multi-file C99 project layout and file names are chosen by the implementer.
- Acceptance clients always use Clean Session=1, no Will, no authentication, QoS 0 and RETAIN=0, so only that combination must be accepted successfully.
- PING exchange is implemented and idle keepalive timeout scheduling is not implemented.
- CONNECT is treated as anonymous; no user name or password validation is performed because authentication is excluded.
- Resources are released on normal SIGINT/SIGTERM termination; no sanitizer or leak-diagnostic tooling is required.
- A client connection that commits a malformed in-scope packet is closed individually while the listener and all other connections remain operational.
