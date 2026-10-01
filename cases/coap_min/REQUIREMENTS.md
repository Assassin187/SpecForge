# Required CoAP RFC 7252 subset

- R01: Independent multi-file Linux C99 IPv4 UDP server, concurrent source endpoints, ./coap_server <port>, GCC/make build, accurate README and development tests.
- R02: Implement CoAP version 1 fixed header, message type, code, big-endian Message ID, Token length 0-8 and correct payload marker handling; preserve arbitrary binary and empty payloads.
- R03: Parse delta/length encoded options, including both extended forms and repeated Uri-Path options; route case-sensitive paths, support Uri-Host, Uri-Port and Content-Format as needed for direct unicast requests. Ignore unrecognized elective options; reject unrecognized critical options according to RFC 7252.
- R04: CON requests receive immediate piggybacked ACK responses with the same Message ID and Token. No separate responses or actively sent CON responses are required.
- R05: NON requests receive NON responses with matching Token and a server-assigned Message ID. Responses must be associated with the requesting source endpoint.
- R06: GET /hello returns 2.05 Content, Content-Format 0 and exact payload hello, without an added newline.
- R07: /value is a pre-existing mutable resource initially holding an empty byte sequence. PUT /value with Content-Format 42 stores up to 1024 bytes and returns 2.04 Changed. GET /value returns 2.05 Content, Content-Format 42 and exactly the stored bytes. Empty PUT clears the value.
- R08: Cache and replay duplicate CON responses by source endpoint and Message ID for EXCHANGE_LIFETIME. Apply each accepted request only once: PUT A, PUT B, then retransmit the original A request; the resource must remain B. This strict duplicate policy is selected by this task even where RFC 7252 permits relaxing deduplication for idempotent methods.
- R09: Answer an empty CON message with an empty RST carrying the same Message ID; ignore unrelated empty ACK/RST messages without changing application state.
- R10: Return 4.04 Not Found for unknown resources and 4.05 Method Not Allowed for unsupported methods on known resources; preserve response correlation and correct message framing.
- R11: Validate header/version/token bounds, option bounds and reserved nibble values, payload marker and datagram truncation. Malformed messages must not crash or corrupt the server; continue processing valid traffic from this and other endpoints.
- R12: Specify ownership of received slices, resource bytes, encoded replies and duplicate-cache storage. Stop normally on SIGINT/SIGTERM and release resources without ASan, UBSan or leak diagnostics.

Maximum application payload is 1024 bytes. Direct unicast loopback requests
are sufficient. Excluded: separate responses, active CON response retransmission,
Observe, Block-wise transfer, DTLS/TLS, proxying, multicast, discovery, persistent
storage and complete CoAP conformance. No particular event loop, module layout
or interface naming scheme is required.
