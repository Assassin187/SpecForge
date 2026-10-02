# Required MQTT 3.1.1 subset

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

Clients used for acceptance have Clean Session=1, no Will, no authentication,
QoS 0 and RETAIN=0. Excluded: QoS 1/2, retained messages, Will, persistent
sessions, UNSUBSCRIBE, authentication, TLS and complete MQTT compliance.
PING exchange is required; idle keepalive timeout scheduling is excluded.

