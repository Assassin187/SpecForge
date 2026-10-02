# Required HTTP/1.1 RFC 9112 / RFC 9110 subset

- R01: Linux C99 IPv4 TCP origin server, independent multi-file project, concurrent clients, ./http_server <port>, clean make build and accurate README.
- R02: Parse HTTP/1.1 request lines, origin-form targets and CRLF-terminated headers. Methods and paths are case-sensitive; header names and applicable token values are case-insensitive. Accept optional whitespace around field values and well-formed unknown fields; require exactly one valid nonempty Host, optionally including a port.
- R03: GET /hello returns 200 OK, Content-Type text/plain and the exact bytes hello, without an added newline. Query strings do not change routing; match paths exactly without percent-decoding or normalization.
- R04: /value is shared across connections and initially empty. PUT stores at most 1024 decoded body bytes and returns 204 with no body, Content-Length or Transfer-Encoding; GET returns 200 and the exact bytes as application/octet-stream. Preserve binary and empty values; commit only a complete valid request.
- R05: POST /echo returns 200, Content-Type application/octet-stream and the exact submitted body, including binary or empty content, up to 1024 decoded bytes. It does not modify /value or create another resource.
- R06: HEAD on /hello and /value returns the corresponding GET status and metadata, including Content-Length, without body bytes; HEAD error responses also omit bodies. Use correct response framing, content types and lengths, and a valid Date based on system time in 2xx and 4xx responses.
- R07: Accept a single valid nonnegative decimal Content-Length, including zero, and consume exactly that many body bytes. Requests without Content-Length or Transfer-Encoding have no body. An incomplete request must not succeed or modify /value.
- R08: Decode chunked request bodies, including hexadecimal sizes, CRLF boundaries, the zero chunk, valid extensions and trailers. Bound chunk metadata; ignore extensions and permitted trailer fields without changing routing, framing or content; apply the 1024-byte limit to decoded bytes.
- R09: Handle fragmented and coalesced requests, including both body framings, and reply to pipelined requests in order. Process complete buffered requests before TCP EOF, including a final PUT or POST followed by write-side shutdown; never apply an incomplete request.
- R10: Use persistent connections by default. Recognize Connection: close among comma-separated options, send the complete response with Connection: close, then close without processing later requests. A disconnected or stalled client must not block other clients.
- R11: Unknown resources return 404; disallowed methods on known resources return 405 with the appropriate Allow list; unimplemented methods return 501. Application errors leave /value unchanged; consume the framed body before continuing with another request on the connection.
- R12: Reject malformed syntax, invalid Host or framing errors with 400 and close, including repeated/list Content-Length and simultaneous Content-Length/Transfer-Encoding. A final transfer coding other than chunked receives 400; unsupported codings preceding a final chunked coding receive 501. Reject oversized bodies with 413, targets with 414, headers/trailers with an appropriate 4xx, and unsupported versions with 505; close without changing stored data.
- R13: Specify and implement ownership of input slices, decoded bodies, stored resource bytes, response buffers and connection objects; handle partial writes and disconnects, and free all resources on normal SIGINT/SIGTERM termination; no ASan, UBSan or leak diagnostics.

Clients use direct loopback TCP and HTTP/1.1 origin-form requests; GET/HEAD
requests have no body. Decoded bodies are at most 1024 bytes, request targets
8192 bytes, and header or trailer sections 16384 bytes each. /value remains
memory-only and resets on restart. The resources, limits and conservative
framing rejection policy are task choices.
Excluded: HTTP/1.0, HTTP/2, HTTP/3, TLS, proxies, CONNECT, protocol upgrades,
WebSocket, 100-continue, chunked responses, other transfer codings,
DELETE/OPTIONS/TRACE application handling, compression, Range/conditional
requests, caching, DNS lookup, virtual-host routing, filesystem serving,
cookies/sessions, authentication, persistent storage and complete HTTP compliance.
