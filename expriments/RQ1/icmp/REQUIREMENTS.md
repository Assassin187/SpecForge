# Required ICMP RFC 792 message-construction scope

- R01: Linux C99 independent multi-file project with reusable public headers and construction functions, clean GCC/make build, accurate README and finite executable ./icmp_selftest. Provide Makefile targets all, clean, test and sanitize; test runs the development tests against the selected normal or ASan/UBSan build.
- R02: Provide message structures and a separate public filling/construction entry point for each of the eleven Type values belonging to RFC 792's eight message families. Produce the complete ICMP message starting at the Type field and report its byte length; IPv4 encapsulation is the caller's responsibility. Choose and document the public types and signatures.
- R03: Construct the common Type, Code and Checksum fields and each message's remaining fields according to RFC 792. Serialize multi-byte fields in network byte order with no host-structure padding in the output. Generate reserved/unused fields as zero.
- R04: Implement the RFC 792 ICMP checksum over the entire serialized ICMP message, including variable data. Clear the checksum field for calculation and handle odd byte lengths using a zero octet for calculation only; do not append that octet to the transmitted message.
- R05: Support Echo and Echo Reply construction (Types 8 and 0, Code 0). Accept caller-supplied Identifier, Sequence Number and arbitrary binary data, including empty, odd-length and even-length data. Form a reply from the request's Identifier, Sequence Number and exact data, change the Type and recompute the checksum.
- R06: Support Destination Unreachable construction (Type 3) with every Code defined in RFC 792 (0 through 5), zero unused field and the original datagram quotation.
- R07: Support Time Exceeded construction (Type 11) with both RFC 792 Codes (0 and 1), zero unused field and the original datagram quotation.
- R08: Support Parameter Problem construction (Type 12, Code 0), caller-supplied Pointer, zero unused bytes and the original datagram quotation.
- R09: Support Source Quench construction (Type 4, Code 0), zero unused field and the original datagram quotation. Include this historical RFC 792 message in the construction scope.
- R10: Support Redirect construction (Type 5) with every RFC 792 Code (0 through 3), caller-supplied Gateway Internet Address and the original datagram quotation.
- R11: For R06-R10, preserve the caller-supplied quotation containing the complete original IPv4 header, including options when present, followed by the first 64 bits of its data. Do not omit the IPv4 header or substitute only eight data bytes. The caller supplies the valid quotation; IPv4 header generation, validation and routing are outside scope.
- R12: Support Timestamp and Timestamp Reply construction (Types 13 and 14, Code 0), Identifier, Sequence Number and all three 32-bit timestamp fields. Accept caller-supplied timestamp values; form replies preserving the request's Identifier, Sequence Number and Originate Timestamp while using the supplied Receive and Transmit Timestamps. Document the RFC 792 timestamp representation, including its nonstandard-time indication; acquiring system time is outside scope.
- R13: Support Information Request and Information Reply construction (Types 15 and 16, Code 0). Form replies preserving the request's Identifier and Sequence Number. Include these historical RFC 792 messages in the construction scope.
- R14: Document buffer capacity, serialized length and ownership for construction inputs and outputs. Do not read outside supplied input storage or write outside output capacity; report insufficient output storage. Release any allocated resources and pass development tests under ASan/UBSan without memory, undefined-behavior or leak diagnostics.
- R15: Development tests must cover all eight message families and all eleven Type values, every required Code, field values and wire lengths, checksum validity, request/reply field preservation, empty/odd/even binary Echo data and datagram quotations with and without IPv4 options. ./icmp_selftest must execute these tests and return a failure status if any check fails.

Protocol baseline: the original September 1981 RFC 792, matching the ICMP
version used in the APG paper. Source Quench and Information Request/Reply
remain in this historical construction experiment. This task does not apply
later RFC updates or claim modern full ICMP conformance.

All construction inputs other than output capacity satisfy their documented
preconditions. No general-purpose received-message decoder is requested.
Excluded: ICMPv6, message types and codes introduced after RFC 792, raw
sockets, NFQUEUE, ping/client/server programs, IPv4 packet generation,
automatic error-trigger detection, forwarding, routing-table changes,
congestion control and live-network interoperability testing.
