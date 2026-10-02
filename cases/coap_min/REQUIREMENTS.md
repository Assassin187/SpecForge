# Required CoAP RFC 7252 subset

- R01: Linux C99 IPv4 UDP server, independent multi-file project, concurrent source endpoints, ./coap_server <port>, clean make build and accurate README.
- R02: Handle CoAP version 1 headers, message types and codes, network-order Message IDs, Tokens of 0-8 bytes and payload markers; preserve arbitrary binary and empty payloads.
- R03: Decode option deltas and lengths, including extended forms and repeated Uri-Path options. Match case-sensitive paths and support Uri-Host, Uri-Port and Content-Format for direct requests; ignore unknown elective options and reject unknown critical options according to RFC 7252.
- R04: Respond to CON requests with immediate piggybacked ACK responses carrying the same Message ID and Token.
- R05: Respond to NON requests with NON responses carrying the matching Token and a server-assigned Message ID; send each response to the requesting source endpoint.
- R06: GET /hello returns 2.05 Content, Content-Format 0 and the exact payload hello, without an added newline.
- R07: /value is shared across endpoints and initially empty. PUT /value with Content-Format 42 stores up to 1024 bytes and returns 2.04 Changed; GET returns 2.05 Content, Content-Format 42 and the exact stored bytes. Empty PUT clears the value.
- R08: Replay the original response to duplicate CON requests with the same source endpoint and Message ID throughout EXCHANGE_LIFETIME, without executing the request again. Replaying an old PUT must not overwrite a value changed by a later request.
- R09: Respond to an empty CON with an empty RST carrying the same Message ID; unrelated empty ACK/RST messages do not change resource state.
- R10: Return 4.04 Not Found for unknown resources and 4.05 Method Not Allowed for unsupported methods on known resources; preserve response correlation and wire framing.
- R11: Validate header/version/token bounds, option bounds and reserved values, payload markers and datagram truncation. Malformed input must not crash the server, corrupt state or interrupt service to other endpoints; subsequent valid requests remain usable.
- R12: Specify and implement ownership of received slices, stored resource bytes, reply buffers and duplicate-response storage; free all resources on normal SIGINT/SIGTERM termination; no ASan, UBSan or leak diagnostics.

Clients use direct unicast loopback UDP. Application payloads are at most
1024 bytes and resources remain memory-only. The resources and strict
duplicate-request policy are task choices, including deduplication of PUT.
Excluded: separate responses, actively sent CON responses and their
retransmission, Observe, Block-wise transfer, DTLS/TLS, proxying, multicast,
resource discovery, persistent storage and complete CoAP conformance.
