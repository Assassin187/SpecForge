[PROMPT]
Tear a connection down at once: discard the queued output instead of flushing it, close the descriptor and mark the session CLOSED, so malformed input, DISCONNECT and broker shutdown cannot leave resources or events behind.

[RELY]
STRUCT:
  - NAME:
struct session
    ROLE:
the session being torn down
  - NAME:
struct byte_buf
    ROLE:
queued-output buffer discarded here
FUNC:
  - NAME:
release_fd
    KIND:
CALL
    ROLE:
close the descriptor exactly once
  - NAME:
buffer_free
    KIND:
CALL
    ROLE:
release the queued output's storage rather than only clearing its length
VAR:
  - NAME:
SESSION_CLOSED
    ROLE:
terminal state reached by this function

[GUARANTEE]
RAW:
void session_close_immediately(struct session *s)
NAME:
session_close_immediately
RETURN:
void
PARAMS:
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
s: borrowed session pointer, possibly NULL.
  ACTION:
1) If s is NULL return. 2) If s->state == SESSION_CLOSED return: the descriptor is already closed and the output already freed, so a repeated call (for example the loop closing a session that a protocol error closed a moment earlier, then the reaper destroying it) must not close anything twice. 3) buffer_free(&s->output): queued packets are DELIBERATELY dropped rather than flushed — a malformed packet, an unsupported QoS, a DISCONNECT or a shutdown must not try to keep writing. Clearing the length alone would keep the allocation, so the storage is released here. 4) release_fd(s): net_close_fd(&s->fd) closes the descriptor when it is still open and stores -1; from now on session_fd(s) reports -1 and no readiness event can arrive for this descriptor number, because a closed descriptor is removed from the epoll set by the kernel. 5) s->state = SESSION_CLOSED. 6) The receive buffer is left as it is: no further parsing happens on a CLOSED session (the read pump and the protocol core both stop on CLOSED), and its storage is released by session_destroy() when the reaper drops the session, which avoids freeing memory that an in-progress parse might still be looking at. 7) Return.
  OUTPUT:
No return value. On a session that was not already CLOSED: queued output is released, the descriptor is closed exactly once and the state becomes SESSION_CLOSED. On a NULL or already CLOSED session nothing changes.
  INVARIANTS_USED:
    - CLOSED is terminal: nothing may move a session out of CLOSED
    - a closed descriptor must never be closed again, and the field must be -1 so no later path re-closes a recycled number
    - queued output that will never be written is released, not merely marked
    - closing one connection must not affect another connection's descriptor, buffers or subscriptions
  PRECONDITION:
s is NULL or points to a live session.
  POSTCONDITION:
s->state == SESSION_CLOSED, s->fd == -1 and the output buffer is empty with no storage; input storage, identifier and subscriptions still owned by s until session_destroy().
  IDEMPOTENT:
true
  THREAD_SAFETY:
Not thread safe with respect to concurrent use of the same session.
