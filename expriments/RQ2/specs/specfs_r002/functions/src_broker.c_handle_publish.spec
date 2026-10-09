[PROMPT]
Fan out a well-formed QoS 0 PUBLISH to every matching READY session, and close the publishing connection when it asks for a QoS this broker does not implement.

[RELY]
STRUCT:
  - NAME:
struct broker
    ROLE:
routing registry handed to the fan-out
  - NAME:
struct session
    ROLE:
publisher; its state decides whether the connection survives
  - NAME:
struct wire_publish
    ROLE:
decoded topic, payload and flags borrowed from the publisher's receive buffer
FUNC:
  - NAME:
session_close_immediately
    KIND:
CALL
    ROLE:
close a publisher that requested an unsupported QoS
  - NAME:
session_state
    KIND:
CALL
    ROLE:
detect that the publisher closed itself during fan-out
  - NAME:
route_publish
    KIND:
CALL
    ROLE:
same-file: encode one QoS 0 copy and enqueue it to matching READY sessions
VAR:
  - NAME:
BROKER_CONTINUE
    ROLE:
the QoS 0 publish was routed and the publisher stays usable
  - NAME:
BROKER_CLOSE
    ROLE:
the publisher requested QoS 1/2, or was itself closed during fan-out

[GUARANTEE]
RAW:
static enum broker_result handle_publish(struct broker *b, struct session *s, const struct wire_publish *pub)
NAME:
handle_publish
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
b: broker registry. s: the READY publisher (the caller has already rejected PUBLISH before CONNECT and closed the connection). pub: PUBLISH decoded by wire_decode_publish; topic/payload are borrowed slices into s's receive buffer and stay untouched until the caller consumes the frame bytes.
  ACTION:
1. Unsupported quality of service: if pub->qos != 0 the broker cannot honour the delivery guarantee (it never sends PUBACK/PUBREC and stores no in-flight state), so call session_close_immediately(s) and return BROKER_CLOSE without routing anything - a partially routed QoS 1/2 message would violate the requested guarantee. 2. Defensive protocol check: the decoder already returns WIRE_INVALID for the prohibited DUP 1 with QoS 0 combination, so a pub->dup != 0 reaching here with qos == 0 is impossible; should it occur anyway, treat it as a violation and take the same close path (no publish is forwarded). 3. Otherwise call route_publish(b, pub): one QoS 0 copy with DUP 0 and RETAIN 0 is enqueued to every READY session whose subscription matches, including s itself when one of its own filters matches; the inbound RETAIN flag is deliberately not propagated because this broker keeps no retained messages. 4. Afterwards check session_state(s): if the publisher is SESSION_CLOSED (its own queue could not grow during fan-out, or an equivalent failure closed it) return BROKER_CLOSE so the framing loop stops; otherwise return BROKER_CONTINUE.
  OUTPUT:
BROKER_CONTINUE when pub->qos == 0 and the publisher is still not SESSION_CLOSED: exactly one QoS 0 copy has been queued for each matching READY session, with topic and payload bytes preserved. BROKER_CLOSE when pub->qos is 1 or 2 (publisher closed immediately, nothing routed), or when the publisher was closed during fan-out.
  INVARIANTS_USED:
    - only QoS 0 delivery is in scope; PUBLISH with QoS 1 or 2 is refused by closing the offending connection rather than answering with an unimplemented acknowledgement
    - MQTT forbids DUP 1 with QoS 0, and wire_decode_publish already reports that combination as WIRE_INVALID
    - topic and payload are length-delimited byte strings: 0x00 bytes and empty payloads are forwarded unchanged
    - error isolation: closing the publisher does not disturb any other session
    - the publisher is an ordinary participant in fan-out and therefore receives its own copy when it subscribes to the topic
  PRECONDITION:
b, s and pub are non-NULL; s is in SESSION_READY and pub came from a WIRE_COMPLETE wire_decode_publish call on a frame of s's receive buffer.
  POSTCONDITION:
either the message was routed exactly once per matching READY session and s remains READY, or s is SESSION_CLOSED with nothing routed; no other session changed state except recipients closed by an enqueue failure.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Single-threaded reactor; no session is created or destroyed concurrently.
