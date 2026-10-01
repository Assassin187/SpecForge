# SMTP minimum local capture server

Build an independent multi-file SMTP server in C99 for Linux IPv4 TCP.
REQUIREMENTS.md selects the subset. The supplied RFC 5321 text is the source
of SMTP protocol rules. Do not import an existing mail server or use an SMTP
server library as the implementation. Choose the project structure, types,
interfaces and development tests.

Deliver sources, public headers, Makefile, README.md, development tests and
the executable smtp_server. Start as ./smtp_server <port> <mail-dir>, where
mail-dir is an existing writable directory. Build with GCC and make. Plan
public interfaces, transaction state, ownership and behavior before coding;
private implementation helpers may be chosen while coding.

This is a bounded local mail-capture task, not a production mail transfer
agent or complete SMTP compliance. The server identity, local recipients,
capture format and storage limits in REQUIREMENTS.md are application task
choices, not additional protocol rules. Capture the original DATA content
after SMTP transparency processing; do not validate RFC 5322 message headers
or insert Received or Return-Path headers into this inspection record.

The unmodified standard text is from
[RFC Editor, RFC 5321](https://www.rfc-editor.org/rfc/rfc5321.txt).
