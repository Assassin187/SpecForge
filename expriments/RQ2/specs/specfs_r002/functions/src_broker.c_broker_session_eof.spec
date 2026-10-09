[PROMPT]
Handle a peer read shutdown: first process every complete packet still buffered (so a final PUBLISH is still routed), then discard any partial tail and put the session into CLOSING so already queued output is flushed before the descriptor is closed.

[RELY]
STRUCT:
  - NAME:
struct broker
    ROLE:
registry used by the framing step
  - NAME:
struct session
    ROLE:
connection whose buffered frames are handled and which then drains its queue
  - NAME:
struct byte_buf
    ROLE:
receive buffer whose partial tail is discarded; output queue is left intact
FUNC:
  - NAME:
session_state
    KIND:
CALL
    ROLE:
decide whether frames may still be processed and whether the session is already CLOSED/CLOSING
  - NAME:
session_input
    KIND:
CALL
    ROLE:
borrow the receive buffer to discard the trailing partial bytes
  - NAME:
buffer_consume
    KIND:
CALL
    ROLE:
drop the partial tail (len bytes) while keeping the storage
  - NAME:
session_close_after_flush
    KIND:
CALL
    ROLE:
enter CLOSING with the queued output preserved
  - NAME:
broker_process_input
    KIND:
CALL
    ROLE:
same-file: handle every complete buffered frame
VAR:
  - NAME:
SESSION_AWAITING_CONNECT
    ROLE:
state in which buffered bytes are still framed (a CONNECT may be pending)
  - NAME:
SESSION_READY
    ROLE:
state in which buffered bytes are still framed
  - NAME:
SESSION_CLOSING
    ROLE:
state that already preserves the queue and must not be overwritten
  - NAME:
SESSION_CLOSED
    ROLE:
terminal state; nothing to do

[GUARANTEE]
RAW:
void broker_session_eof(struct broker *b, struct session *s)
NAME:
broker_session_eof
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
b: broker registry. s: the session whose peer shut down its write side or whose read failed; s is still registered and owns its descriptor and buffers.
  ACTION:
1. If s is NULL return. 2. st = session_state(s); if st == SESSION_CLOSED return (the connection is already closed and its resources are released by the normal teardown). 3. If st == SESSION_AWAITING_CONNECT or st == SESSION_READY, call broker_process_input(b, s) once: it frames and handles every complete packet still in the receive buffer, in order, including a final PUBLISH immediately followed by the peer's shutdown, and returns either 0 (only an incomplete tail or nothing remains) or -1 (a frame or handler closed the session). 4. st = session_state(s); if st == SESSION_CLOSED return: the input already closed the connection (DISCONNECT, a malformed final frame or a rejected CONNECT) and its queue was deliberately released, so there is nothing to flush. 5. Discard the partial tail: in = session_input(s); if in is non-NULL and in->len > 0 call buffer_consume(in, in->len). Any bytes that do not form a complete packet can never complete now that the peer stopped sending, and keeping them would make a later teardown copy pointless data. This step is harmless when the buffer already ended on a frame boundary (len == 0). 6. If st != SESSION_CLOSING call session_close_after_flush(s) so the session enters CLOSING with any queued output preserved: the writer pumps the remaining bytes out and only then is the descriptor closed. A session in AWAITING_CONNECT (peer closed before CONNECT) also becomes CLOSING with an empty queue, which broker_reap closes immediately on the next pass.
  OUTPUT:
void. After the call the session is either SESSION_CLOSED (its input already closed it) or SESSION_CLOSING with its receive buffer emptied and its queued output preserved for the writer; no frames remain buffered, and any final complete PUBLISH has been routed to matching sessions.
  INVARIANTS_USED:
    - all complete frames already received are handled before EOF is acted upon
    - a partial trailing frame is dropped and never interpreted
    - entering CLOSING never reorders or discards queued output, so a response already produced for this client is flushed first
    - the descriptor is closed exactly once, by the writer path or by the teardown, never by both
    - closing one connection leaves other sessions untouched
  PRECONDITION:
b and s are non-NULL and s is owned by b; the execution layer observed recv returning 0 or a read error on s's descriptor and has not called broker_reap since.
  POSTCONDITION:
session_state(s) is SESSION_CLOSED or SESSION_CLOSING; s's receive buffer has len 0; queued output is either empty (input already closed the session) or preserved.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Single-threaded reactor; the session is not accessed concurrently.
