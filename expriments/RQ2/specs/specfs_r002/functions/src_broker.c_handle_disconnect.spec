[PROMPT]
Honour a valid DISCONNECT by releasing the connection immediately: the descriptor is closed, queued output is discarded without flushing and the session becomes CLOSED while the registry entry waits for broker_reap.

[RELY]
STRUCT:
  - NAME:
struct session
    ROLE:
connection whose subscriptions and descriptor are released when it is destroyed
FUNC:
  - NAME:
session_close_immediately
    KIND:
CALL
    ROLE:
discard queued output, close the descriptor, enter CLOSED
  - NAME:
session_state
    KIND:
TYPE_REF
    ROLE:
the resulting SESSION_CLOSED state observed by the caller
VAR:
  - NAME:
BROKER_CLOSE
    ROLE:
the session is closed and must be reaped by the caller

[GUARANTEE]
RAW:
static enum broker_result handle_disconnect(struct broker *b, struct session *s)
NAME:
handle_disconnect
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
b: broker registry (not dereferenced). s: the READY session whose received bytes were the 2-byte DISCONNECT packet already validated by wire_decode_empty(WIRE_PACKET_DISCONNECT) (type 14, flags 0, Remaining Length 0). No payload is read from the input buffer.
  ACTION:
1. Call session_close_immediately(s): the queued output is released without being flushed, the descriptor is closed exactly once and the session enters SESSION_CLOSED (the call is a no-op if an earlier failure already closed it). 2. Return BROKER_CLOSE so the framing loop stops processing further bytes of this connection and the execution layer reaps the registry.
  OUTPUT:
BROKER_CLOSE; the session is SESSION_CLOSED with no queued output and a closed descriptor. Its subscriptions and identifier remain reachable through the session object only until broker_reap calls session_destroy, which releases them, so no other connection inherits them and freshly accepted connections always start with an empty subscription list.
  INVARIANTS_USED:
    - DISCONNECT is a clean client-initiated teardown: the server closes the connection and keeps no session state
    - this broker stores no state across connections, so the client's subscriptions simply cease to exist
    - closing one session never modifies another session's descriptor, queue or subscriptions
    - queued output of a disconnecting client is discarded rather than flushed
  PRECONDITION:
s is non-NULL and in SESSION_READY; the caller already validated that the frame is a complete, well-formed DISCONNECT.
  POSTCONDITION:
session_state(s) == SESSION_CLOSED, session_fd(s) == -1 and the session contributes no queued bytes; the registry still contains the entry until broker_reap removes it.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Single-threaded reactor; the session is not touched concurrently.
