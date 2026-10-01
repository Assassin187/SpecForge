# HTTP/1.1 minimum origin server

Build an independent multi-file HTTP/1.1 origin server in C99 for Linux
IPv4 TCP. REQUIREMENTS.md selects the subset. The supplied standard bundle
contains RFC 9112 for HTTP/1.1 message syntax, framing and connection handling,
and RFC 9110 for HTTP semantics. Use those texts as the sources of protocol
rules. Do not import an existing HTTP server or use an HTTP parser/server
library as the implementation. Choose the project structure, types,
interfaces and development tests.

Deliver sources, public headers, Makefile, README.md, development tests and
the executable http_server. Start as ./http_server <port>. Build with GCC
and make. Specify public interfaces, parsing outcomes, request lifecycle and
ownership before implementation; private helpers may be chosen while coding.

This is a bounded local resource server, not complete HTTP compliance.
Resource paths, exact content, resource limits and the stricter rejection
policy in REQUIREMENTS.md are application task choices; distinguish those
choices from normative protocol facts. There is no prescribed architecture,
event loop, module count, source layout or public function naming scheme.

The single UTF-8 protocol input contains both complete RFC texts, with source
URLs and document boundaries. Original sources:
[RFC Editor, RFC 9112](https://www.rfc-editor.org/rfc/rfc9112.txt) and
[RFC Editor, RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.txt).
