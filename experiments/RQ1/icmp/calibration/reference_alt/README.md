# Alternate-API calibration fixture (not a generation input)

Same RFC 792 construction behavior as `../reference`, written with a
deliberately different public API style to prove the acceptance suite
`../../icmp_check.py` harnesses more than one interface shape:

- status-returning constructors with an out-length parameter (`int ... (..., size_t *out_length)`),
- an Echo Reply constructor that parses the request wire bytes,
- a fill+serialize split for Timestamp messages,
- a config-struct constructor for Destination Unreachable,
- shuffled parameter orders and non-reference naming.

Build with `make`; `make sanitize` for the ASan/UBSan build; the development
entry point is `./nmsg_selftest`.
