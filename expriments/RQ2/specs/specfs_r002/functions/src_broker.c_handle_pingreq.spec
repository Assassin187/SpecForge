[PROMPT]
Answer a valid PINGREQ with exactly one PINGRESP queued behind any bytes already queued, keeping the session READY so later business traffic continues.

[RELY]
STRUCT:
  - NAME:
struct session
    ROLE:
connected session whose queue receives PINGRESP
  - NAME:
struct byte_buf
    ROLE:
queued-output buffer receiving the 2 response bytes
FUNC:
  - NAME:
session_output
    KIND:
CALL
    ROLE:
borrow the queue that receives PINGRESP
  - NAME:
wire_encode_pingresp
    KIND:
CALL
    ROLE:
append the exact 2-byte PINGRESP
  - NAME:
session_close_immediately
    KIND:
CALL
    ROLE:
close on queueing failure
VAR:
  - NAME:
BROKER_CONTINUE
    ROLE:
session stays usable after a queued PINGRESP
  - NAME:
BROKER_CLOSE
    ROLE:
session could not queue its response and is closed

[GUARANTEE]
RAW:
static enum broker_result handle_pingreq(struct broker *b, struct session *s)
NAME:
handle_pingreq
RETURN:
enum broker_result
PARAMS:
  - TYPE:
struct broker *
    NAME:
b
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
struct session *
    NAME:
s
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
b: broker registry (not dereferenced; kept for signature symmetry with the other handlers). s: the READY session whose received bytes were the 2-byte PINGREQ packet already validated by wire_decode_empty(WIRE_PACKET_PINGREQ); the packet bytes have no payload, so nothing from the input buffer is needed.
  ACTION:
1. out = session_output(s); if out is NULL, session_close_immediately(s) and return BROKER_CLOSE. 2. rc = wire_encode_pingresp(out). 3. If rc != 0: the response could not be queued (allocation failure), so the required PINGRESP cannot be sent; call session_close_immediately(s) and return BROKER_CLOSE. 4. Otherwise return BROKER_CONTINUE: exactly d0 00 was appended after every byte already queued, the session stays SESSION_READY and the next complete frame in the receive buffer is handled normally (no keepalive bookkeeping or timeout scheduling exists in this broker).
  OUTPUT:
BROKER_CONTINUE with exactly one d0 00 appended to the session output queue (never replacing already queued bytes) when encoding succeeded; BROKER_CLOSE with the session CLOSED when the output buffer could not be grown.
  INVARIANTS_USED:
    - a valid PINGREQ is answered with exactly the 2 bytes d0 00 (PINGRESP, Remaining Length 0)
    - queued output is only appended to, so a PINGRESP never discards an earlier queued packet
    - keepalive timeout scheduling is excluded from scope, so no timer is armed
    - a response that cannot be queued closes the connection instead of silently dropping it
  PRECONDITION:
s is non-NULL and in SESSION_READY; the caller already validated the PINGREQ fixed header (type 12, flags 0, Remaining Length 0).
  POSTCONDITION:
either the session queue ends with d0 00 and the session is still READY, or the session is CLOSED with its queue released.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Single-threaded reactor; the session is not touched concurrently.
