# SMTP minimum local capture server

Build an independent multi-file SMTP server in C99 for Linux IPv4 TCP.
Use the accompanying REQUIREMENTS.md to select the protocol subset, and
the supplied RFC 5321 text as the only source of protocol rules.
Deliver implementation sources, public headers, Makefile, README.md,
development tests and the executable smtp_server. Choose the project layout.
Start it as ./smtp_server <port> <mail-dir>, where mail-dir is an existing
writable directory. It must build using GCC and make.

Public types, interfaces, ownership and processing paths must be planned
before implementing the project. Private helpers may be chosen during coding.

This task requests local mail capture rather than full SMTP implementation.
Do not import an existing mail server or depend on an SMTP server library
as the implementation.
