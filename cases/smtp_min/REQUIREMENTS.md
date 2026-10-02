# Required SMTP RFC 5321 subset

- R01: Linux C99 IPv4 TCP capture server, independent multi-file project, concurrent clients, ./smtp_server <port> <mail-dir>, clean make build and accurate README. mail-dir is an existing writable directory.
- R02: Send a 220 greeting identifying smtp.example.test. Use CRLF-terminated command and reply lines, correct three-digit replies and case-insensitive command names; reply wording may be chosen by the implementation.
- R03: Accept EHLO/HELO with a valid domain or IPv4 address literal, return 250 and initialize the session without DNS lookup. Repeated EHLO/HELO clears an unfinished transaction; advertise no service extensions.
- R04: Accept MAIL FROM with an ASCII dot-string local-part and valid domain, or a null reverse-path and return 250. MAIL requires an initialized session with no unfinished transaction; otherwise return 503 without replacing the current envelope.
- R05: Accept multiple RCPT TO commands after MAIL. Return 250 for alice@example.test, bob@example.test and the qualified or unqualified postmaster forms; ordinary local-parts are case-sensitive, domains and the reserved postmaster name are case-insensitive. Preserve recipient order and duplicates; reject other valid recipients with 550 without losing accepted recipients.
- R06: DATA requires a sender and at least one accepted recipient; return 354. Read through the single-dot terminator, remove one leading transparency dot and preserve other 7-bit content and CRLF, including empty data and blank lines. Command-looking DATA lines remain content.
- R07: Store each completed message as one unique UTF-8 JSON file in mail-dir: mail_from is the sender without angle brackets or an empty string; rcpt_to is an array of accepted address strings without brackets in order; data is a JSON string containing the exact unstuffed content including CRLF but excluding the terminator. Never overwrite captures across clients or restarts. Return 250 only after successful storage; failure returns 451 with no completed capture.
- R08: Handle fragmented commands, DATA and terminators, and process coalesced lines in order, including commands following a DATA terminator. Process complete buffered input before TCP EOF; incomplete commands or DATA must not create a capture.
- R09: RSET returns 250 and clears the transaction while preserving initialization; NOOP returns 250 without changing state; valid VRFY returns 252 without verification. QUIT returns 221, discards an unfinished transaction and closes after the reply. Interpret these commands only outside DATA.
- R10: Reset sender, recipients and data after successful or rejected completed DATA so another transaction can run on the same connection. Keep clients' sessions and captures isolated; new connections never inherit old transaction state.
- R11: Use 500 for unknown commands, 501 for invalid arguments, 503 for invalid order, 502 for recognized unimplemented commands and 555 for unsupported MAIL/RCPT parameters. Enforce line, recipient and content limits: excess recipients receive 452 and oversized commands 500; discard oversized DATA or DATA lines through the terminator, then return 552 and reset. Unrecoverable framing errors close only that client, with 421 when possible, and leave no capture.
- R12: Specify and implement ownership of receive slices, envelope addresses, mail data, capture files, reply buffers and connection objects; handle partial writes and disconnects, and free all resources on normal SIGINT/SIGTERM termination; no ASan, UBSan or leak diagnostics.

Clients use direct loopback TCP, 7-bit ASCII data with CRLF, dot-string
mailboxes, and no negotiated extensions. Accept postmaster@example.test and
RCPT TO:<Postmaster>. Command/reply lines are at most 512 octets including
CRLF; DATA lines are at most 1000 octets including CRLF but excluding an
added transparency dot. Task limits are 100 accepted recipients and 65536
unstuffed DATA octets per transaction. Server identity, local recipients and
JSON capture format are task choices.
Excluded: relay, DNS/MX lookup, outbound delivery, retry queues, delivery-status
notifications, production durability or crash recovery, RFC 5322 validation,
MIME parsing, Received/Return-Path insertion, authentication, TLS/STARTTLS,
SMTPUTF8, 8BITMIME, BINARYMIME/BDAT, negotiated PIPELINING, mailing lists,
quoted or internationalized local-parts, source routes and complete SMTP compliance.
