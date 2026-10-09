[PROMPT]
Apply the CONNECT acceptance policy, store the client identifier, queue the exact successful CONNACK and evict an already-connected session that used the same identifier, so exactly one live connection owns an identifier.

[RELY]
STRUCT:
  - NAME:
struct broker
    ROLE:
registry scanned for a same-identifier session to evict
  - NAME:
struct session
    ROLE:
connection that sent CONNECT
  - NAME:
struct wire_connect
    ROLE:
decoded CONNECT fields; client_id is a borrowed slice into the packet
  - NAME:
struct byte_buf
    ROLE:
output queue receiving the CONNACK
  - NAME:
struct wire_span
    ROLE:
borrowed client identifier bytes
FUNC:
  - NAME:
session_state
    KIND:
CALL
    ROLE:
select READY sessions that already own the identifier
  - NAME:
session_mark_ready
    KIND:
CALL
    ROLE:
AWAITING_CONNECT -> READY after a successful CONNACK queue
  - NAME:
session_set_client_id
    KIND:
CALL
    ROLE:
store a session-owned copy of the identifier
  - NAME:
session_client_id
    KIND:
CALL
    ROLE:
borrow the identifier of a candidate session for comparison
  - NAME:
session_output
    KIND:
CALL
    ROLE:
borrow the queue that receives CONNACK
  - NAME:
session_close_after_flush
    KIND:
CALL
    ROLE:
flush a rejection CONNACK before closing
  - NAME:
session_close_immediately
    KIND:
CALL
    ROLE:
close a rejected or evicted connection
  - NAME:
wire_encode_connack
    KIND:
CALL
    ROLE:
append the CONNACK packet bytes
  - NAME:
broker_reap
    KIND:
CALL
    ROLE:
same-file: remove an evicted session from the registry
VAR:
  - NAME:
CONNACK_UNACCEPTABLE_PROTOCOL_LEVEL
    ROLE:
return code 0x01
  - NAME:
CONNACK_IDENTIFIER_REJECTED
    ROLE:
return code 0x02
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
static enum broker_result handle_connect(struct broker *b, struct session *s, const struct wire_connect *c)
NAME:
handle_connect
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
const struct wire_connect *
    NAME:
c
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
b: broker registry. s: the session in AWAITING_CONNECT that sent the CONNECT (the caller has already rejected a second CONNECT on a READY session). c: decoded CONNECT; c->client_id is a borrowed slice into the packet bytes and stays valid for the whole call.
  ACTION:
Policy checks run in this order so a mandatory rejection is never hidden by the unsupported-feature policy: (1) protocol level: if c->protocol_level != 4, queue CONNACK(ack_flags 0x00, return_code 0x01) into session_output(s) and then session_close_after_flush(s) so the 4 bytes are flushed before the close; if the encoder returns nonzero (allocation failure) session_close_immediately(s) instead. Return BROKER_CLOSE. (2) identifier syntax: if c->client_id.len == 0, or any byte is 0x00, or any byte is >= 0x80 (not ASCII), queue CONNACK(0x00, 0x02) with the same flush-then-close behaviour and return BROKER_CLOSE. (3) unsupported but well-formed features: if c->has_will or c->clean_session == 0, close the connection silently with session_close_immediately(s) and return BROKER_CLOSE: this broker stores no session state across connections and has no CONNACK return code that truthfully describes those refusals (0x01 is 'unacceptable protocol version' and 0x02 is 'identifier rejected'), so it neither accepts them as clean sessions nor lies about the reason. (4) store the identifier: if session_set_client_id(s, (const char *)c->client_id.data, c->client_id.len) != 0 return the same silent close (BROKER_CLOSE) — the CONNACK cannot be queued without the identifier and the allocation that failed is the same one the queue would need. (5) queue the successful response: session_output(s) gives the queue, and wire_encode_connack(out, 0x00, 0x00) appends exactly 20 02 00 00 (Session Present 0 because Clean Session = 1 gives no stored state, return code 0). On nonzero, session_close_immediately(s) and return BROKER_CLOSE. (6) session_mark_ready(s) so the session may queue and receive protocol traffic. (7) same-identifier takeover: walk i = 0 .. count-1 over b->sessions; skip NULL and s itself; when session_state(t) == SESSION_READY and session_client_id(t, &tlen) returns non-NULL with tlen == c->client_id.len and memcmp equal, session_close_immediately(t) — the old owner's descriptor, queue and subscriptions are released and its identifier stops resolving to a live session. (8) broker_reap(b) to drop every newly CLOSED session (only those) from the registry, so the registry never keeps a closed connection; reap does not touch s. (9) Return BROKER_CONTINUE. The client identifier is copied, never referenced: the packet bytes belong to the receive buffer and are irrelevant after this call.
  OUTPUT:
BROKER_CONTINUE when the CONNECT was accepted: a session-owned identifier is stored, exactly 20 02 00 00 is queued after any earlier queued bytes, the session is READY and any previous READY session with the same identifier has been closed and removed. BROKER_CLOSE for protocol level != 4 (CONNACK 0x01 queued then flushed), an empty or non-ASCII identifier (CONNACK 0x02 queued then flushed), a Will or Clean Session = 0 (silent close), or an allocation failure while storing the identifier or queueing CONNACK.
  INVARIANTS_USED:
    - CONNECT must be the first packet; the caller guarantees this session was in AWAITING_CONNECT
    - the successful CONNACK is exactly 20 02 00 00 with Session Present 0 and return code 0
    - the identifier is a length-delimited byte string copied into session storage
    - exactly one live connection owns an identifier: a second use evicts the older connection
    - a rejection that must be reported to the peer (0x01, 0x02) is queued and flushed before the close; a refusal with no truthful return code closes without CONNACK
    - closing one session never affects another session's descriptor or subscriptions
  PRECONDITION:
b and s are non-NULL and s is in SESSION_AWAITING_CONNECT; c is a successfully decoded CONNECT whose slices point into a live packet buffer.
  POSTCONDITION:
either s is READY with the identifier stored and the CONNACK queued, or s is CLOSED/CLOSING with the documented rejection bytes queued or no bytes queued; no other session's queued output is modified.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Single-threaded reactor; the registry is not modified concurrently.
