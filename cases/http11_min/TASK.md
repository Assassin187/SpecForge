# HTTP/1.1 minimum origin server

Build an independent multi-file HTTP/1.1 origin server in C99 for Linux IPv4 TCP.
Use the accompanying REQUIREMENTS.md to select the protocol subset, and
the supplied RFC 9112 / RFC 9110 text bundle as the only source of protocol rules.
Deliver implementation sources, public headers, Makefile, README.md,
development tests and the executable http_server. Choose the project layout.
Start it as ./http_server <port>. It must build using GCC and make.

Public types, interfaces, ownership and processing paths must be planned
before implementing the project. Private helpers may be chosen during coding.

This task does not request a full HTTP implementation. Do not import an
existing server or depend on an HTTP parser/server library as the implementation.
