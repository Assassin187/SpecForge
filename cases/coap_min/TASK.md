# CoAP minimum request/response server

Build an independent multi-file CoAP server in C99 for Linux IPv4 UDP.
Use the accompanying REQUIREMENTS.md to select the protocol subset, and
the supplied RFC 7252 text as the only source of protocol rules.
Deliver implementation sources, public headers, Makefile, README.md,
development tests and the executable coap_server. Choose the project layout.
Start it as ./coap_server <port>. It must build using GCC and make.

Public types, interfaces, ownership and processing paths must be planned
before implementing the project. Private helpers may be chosen during coding.

This task does not request a full CoAP implementation. Do not import an
existing server or depend on libcoap as the implementation.
