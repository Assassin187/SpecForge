# ICMP RFC 792 message construction

Build an independent multi-file ICMPv4 message-construction project in C99
for Linux. Use the accompanying REQUIREMENTS.md to select the scope, and
the supplied complete RFC 792 text as the only source of protocol rules.

Implement the message structures and packet-filling logic for all eight
RFC 792 message families, including both request and reply forms. Provide
public construction interfaces that produce complete ICMP wire bytes for
use by a caller's IPv4 transport. Choose the project layout, public type
names and function signatures.

Deliver implementation sources, public headers, Makefile, README.md,
development tests and the executable icmp_selftest. It must build using
GCC and make. Running ./icmp_selftest must exercise the construction
interfaces, report test results and exit without waiting for network input;
return zero only when every development test passes. No startup arguments,
network access or elevated privileges are required.

Public types, interfaces, ownership and processing paths must be planned
before implementing the project. Private helpers may be chosen during coding.
Implement the ICMP checksum in the generated project; no checksum helper or
existing ICMP implementation is supplied.

The requested behavior is message construction. IPv4 transport, operating
system packet interception, routing decisions and a live ICMP responder
are outside this task.
