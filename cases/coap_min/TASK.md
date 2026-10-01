# CoAP minimum request/response server

Build an independent multi-file CoAP server in C99 for Linux IPv4 UDP.
REQUIREMENTS.md selects the subset. The supplied RFC 7252 text is the only
source of protocol rules. Do not import an existing server or use libcoap
as the implementation. Choose the project structure, types and interfaces.

Deliver sources, public headers, Makefile, README.md, development tests and
the executable coap_server. Start as ./coap_server <port>. Build with GCC
and make. Specify the public interfaces and behavior before implementation.
Private implementation helpers may be chosen while coding.

This is a bounded local server task, not complete CoAP compliance. The
resources and stricter duplicate-request policy below are application task
choices; distinguish these from normative protocol facts.
