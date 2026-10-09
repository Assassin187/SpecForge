# ICMP RFC 792 reference message-construction project (calibration fixture)

This hand-written C99 project is **not** a generation input and **not** a
SpecForge product. It exists only to calibrate the independent acceptance
suite `../../icmp_check.py`: a correct implementation must pass every
scenario, and each deliberate mutant produced by `../run_calibration.py`
must fail.

The public API lives in `include/icmp_messages.h`. Every constructor
serializes one complete RFC 792 message (starting at the Type field) into
the caller's buffer, returns the serialized length, and returns 0 when the
supplied capacity is insufficient.

Build and self-test:

```sh
make            # builds ./icmp_selftest
make test       # runs the development tests
make sanitize   # ASan/UBSan build of ./icmp_selftest
make clean
```
