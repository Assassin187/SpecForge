[PROMPT]
Store every accepted filter with granted QoS 0 and queue one SUBACK that echoes the nonzero Packet Identifier with exactly one QoS 0 result code per filter in filter order.

[RELY]
STRUCT:
  - NAME:
struct broker
    ROLE:
unused by this handler except for call symmetry
  - NAME:
struct session
    ROLE:
owner of the subscription list and the output queue
  - NAME:
struct wire_subscribe
    ROLE:
decoded packet identifier plus the borrowed filter/QoS payload
  - NAME:
struct wire_span
    ROLE:
borrowed filter bytes of one pair
  - NAME:
struct byte_buf
    ROLE:
queue receiving SUBACK
FUNC:
  - NAME:
wire_subscribe_next
    KIND:
CALL
    ROLE:
walk the validated filter/QoS pair list by index
  - NAME:
session_add_subscription
    KIND:
CALL
    ROLE:
store each filter with granted QoS 0
  - NAME:
session_output
    KIND:
CALL
    ROLE:
borrow the queue that receives SUBACK
  - NAME:
session_close_immediately
    KIND:
CALL
    ROLE:
close the connection on allocation failure
  - NAME:
wire_encode_suback
    KIND:
CALL
    ROLE:
append the SUBACK packet bytes
  - NAME:
malloc
    KIND:
TYPE_REF
    ROLE:
allocate the granted-code array (one byte per filter)
  - NAME:
memset
    KIND:
TYPE_REF
    ROLE:
fill every granted code with GRANTED_QOS
  - NAME:
free
    KIND:
TYPE_REF
    ROLE:
release the temporary granted-code array
VAR:
  - NAME:
GRANTED_QOS
    ROLE:
granted QoS value 0 reported in every SUBACK result code
  - NAME:
WIRE_MAX_STRING_LENGTH
    ROLE:
65535; upper bound for the number of result codes
  - NAME:
BROKER_CONTINUE
    ROLE:
session may keep running
  - NAME:
BROKER_CLOSE
    ROLE:
session is or must be closed

[GUARANTEE]
RAW:
static enum broker_result handle_subscribe(struct broker *b, struct session *s, const struct wire_subscribe *sub)
NAME:
handle_subscribe
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
  - TYPE:
const struct wire_subscribe *
    NAME:
sub
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
b: broker (unused, kept for handler symmetry). s: a READY session that sent SUBSCRIBE. sub: decoded SUBSCRIBE with a guaranteed nonzero packet_id, an at-least-one-pair payload whose filters and QoS bytes were validated by the decoder, and payload bytes borrowed from the receive buffer.
  ACTION:
1) Count the pairs: n = 0; while wire_subscribe_next(sub, n, &span, &qos) == 1 n++. The decoder guarantees n >= 1 and n <= 65535 (each pair needs a 2-byte length plus at least one filter byte plus one QoS byte, and payload_len <= remaining_length <= 268435455, so n is far below the 65535 bound enforced by the encoder). 2) codes = malloc(n); if codes is NULL, session_close_immediately(s) and return BROKER_CLOSE: a SUBACK cannot be produced, and answering with fewer codes than filters is forbidden. memset(codes, GRANTED_QOS, n) so every result is the granted QoS 0 — for a requested QoS of 0, 1 or 2 the granted value is 0, which is always <= the requested value. 3) For i = 0 .. n-1: wire_subscribe_next(sub, i, &span, &qos) (returns 1 by construction); if session_add_subscription(s, (const char *)span.data, span.len, GRANTED_QOS) != 0, free(codes), session_close_immediately(s) and return BROKER_CLOSE (the session's subscription list keeps exactly the entries stored before the failing one; the connection is closed so no SUBACK is expected). Note the granted QoS is GRANTED_QOS regardless of qos, and the filter bytes are copied, so nothing borrowed from the packet survives the call. 4) out = session_output(s); if wire_encode_suback(out, sub->packet_id, codes, n) != 0, free(codes), session_close_immediately(s) and return BROKER_CLOSE. The encoder writes 0x90, the Remaining Length 2 + n, the echoed identifier MSB first and the n codes in filter order, so result i belongs to filter i. 5) free(codes) (the temporary is not retained). 6) Return BROKER_CONTINUE. The SUBACK is queued after any bytes already in the queue, so a client that pipelined several packets receives responses in the order it sent the requests.
  OUTPUT:
BROKER_CONTINUE with every filter stored (granted QoS 0) and one complete SUBACK queued: 90, Remaining Length 2 + n, the SUBSCRIBE packet identifier, then n bytes equal to 00. For one filter with identifier 1 that is exactly 90 03 00 01 00; for two filters with identifier 42 it is 90 04 00 2a 00 00. BROKER_CLOSE when the temporary code array, a filter copy or the queue could not be allocated, in which case no SUBACK is queued and the session is closed.
  INVARIANTS_USED:
    - SUBACK has fixed flags 0, an echoed nonzero Packet Identifier and one result code per filter in the order the filters appeared
    - the granted QoS is always 0 and never exceeds the requested value
    - a stored subscription is a session-owned copy that dies with the connection
    - queued responses are written in the order they were queued
    - allocating a temporary must release it on every path, including failures
  PRECONDITION:
b and s are non-NULL and s is READY; sub is a successfully decoded SUBSCRIBE with packet_id != 0 and at least one filter/QoS pair.
  POSTCONDITION:
either the session stores the filter list with granted QoS 0 and exactly one SUBACK is appended to its queue, or the session is CLOSED and no SUBACK was queued; in both cases codes is released and no other session changes.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Single-threaded reactor.
