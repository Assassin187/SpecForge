[PROMPT]
Encode exactly one QoS 0 PUBLISH (DUP 0, RETAIN 0) into the broker scratch buffer and append those bytes to the output queue of every READY session whose subscription matches the topic, including the publisher itself, closing only a recipient whose queue could not grow.

[RELY]
STRUCT:
  - NAME:
struct broker
    ROLE:
owns the session pointer array and the reusable scratch buffer
  - NAME:
struct session
    ROLE:
candidate recipient iterated by index
  - NAME:
struct wire_publish
    ROLE:
topic and payload bytes borrowed from the publisher's receive buffer
  - NAME:
struct byte_buf
    ROLE:
scratch holds the single encoded copy; each recipient's queue receives a copy of the same bytes
FUNC:
  - NAME:
buffer_consume
    KIND:
CALL
    ROLE:
empty the scratch buffer before and after encoding while keeping its storage
  - NAME:
wire_encode_publish
    KIND:
CALL
    ROLE:
append the outgoing QoS 0 PUBLISH packet to scratch
  - NAME:
session_state
    KIND:
CALL
    ROLE:
only READY sessions receive routed messages
  - NAME:
session_matches_topic
    KIND:
CALL
    ROLE:
subscription match test for the published topic
  - NAME:
session_enqueue
    KIND:
CALL
    ROLE:
copy the encoded bytes into each matching recipient's queue
  - NAME:
session_close_immediately
    KIND:
CALL
    ROLE:
close only a recipient whose enqueue failed
VAR:
  - NAME:
qos
    ROLE:
0 in the encoded copy (this broker delivers QoS 0 only)
  - NAME:
struct broker
    ROLE:
b->count is the iteration bound; b->sessions[i] is the slot taken while the loop runs

[GUARANTEE]
RAW:
static void route_publish(struct broker *b, const struct wire_publish *pub)
NAME:
route_publish
RETURN:
void
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
const struct wire_publish *
    NAME:
pub
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
b: broker whose sessions array, count and scratch buffer are used. pub: decoded PUBLISH; pub->topic/topic_len and pub->payload/payload_len are borrowed slices into the publishing client's receive buffer and stay valid for the whole call because the caller consumes the frame bytes only after this returns.
  ACTION:
1. Clear scratch: buffer_consume(&b->scratch, b->scratch.len) so the retained capacity holds exactly the next encoded packet (this releases no storage, it only sets len to 0). 2. rc = wire_encode_publish(&b->scratch, pub->topic, pub->topic_len, pub->payload, pub->payload_len, 0, 0, 0). 3. If rc != 0 (invalid argument or allocation failure) return immediately without closing or modifying any session: a QoS 0 message that cannot be encoded is dropped, and no recipient loses its connection for the broker's own memory failure. 4. n = b->count captured once; for i = 0 .. n-1: t = b->sessions[i]; skip t == NULL; skip session_state(t) != SESSION_READY; skip session_matches_topic(t, (const char *)pub->topic, pub->topic_len) == 0; then if session_enqueue(t, b->scratch.data, b->scratch.len) != 0 call session_close_immediately(t) so only the failing recipient is closed while every later recipient is still visited. The loop never calls broker_reap, so the array is not compacted and index i stays valid; closed recipients stay in their slots with state SESSION_CLOSED until the execution layer reaps them. 5. After the loop, buffer_consume(&b->scratch, b->scratch.len) so the scratch buffer is left empty and ready for reuse.
  OUTPUT:
void. On success every READY matching session - including the publisher when its own subscriptions match - has one copy of 30 <RL> <2-byte topic length> <topic> <payload> appended to its queue, in registry order, and the scratch buffer is empty with its storage retained. On an encode failure no session is touched. A recipient whose enqueue failed is SESSION_CLOSED (descriptor closed, queue released) and the remaining recipients are unaffected.
  INVARIANTS_USED:
    - one QoS 0 copy per matching READY session, with topic and payload bytes preserved exactly (payload is length-delimited and may contain 0x00, and may be empty)
    - the publisher receives its own copy when one of its subscriptions matches, because it is an ordinary registry entry
    - matching is case sensitive and uses MQTT level semantics via session_matches_topic
    - the registry array is not compacted during fan-out, so a failing recipient cannot invalidate the iteration
    - error isolation: only the recipient whose queue could not grow is closed
  PRECONDITION:
b and pub are non-NULL; the caller has already rejected pub->qos != 0; the receive buffer holding pub's slices stays alive until this function returns.
  POSTCONDITION:
scratch.len == 0 (intact capacity) and no session other than a failed recipient changed state; each matching READY session's queue grew by exactly the encoded packet length.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Single-threaded reactor; no session is created or destroyed while the fan-out loop runs.
