[PROMPT]
Enter CLOSING so the session stops accepting protocol input but keeps its queued output, allowing a final PUBLISH fan-out and the responses already queued to reach the peer before the descriptor is closed.

[RELY]
STRUCT:
  - NAME:
struct session
    ROLE:
owner of the lifecycle state and queued output
  - NAME:
struct byte_buf
    ROLE:
queued output that must be preserved
FUNC:

VAR:
  - NAME:
SESSION_AWAITING_CONNECT
    ROLE:
may also enter CLOSING (EOF before CONNECT)
  - NAME:
SESSION_READY
    ROLE:
normal source state
  - NAME:
SESSION_CLOSING
    ROLE:
target state; repeated calls are no-ops
  - NAME:
SESSION_CLOSED
    ROLE:
terminal, untouched

[GUARANTEE]
RAW:
void session_close_after_flush(struct session *s)
NAME:
session_close_after_flush
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
1) If s is NULL return. 2) If s->state == SESSION_CLOSED return (terminal: the descriptor is already closed and the queue was already discarded by the immediate path, so there is nothing left to flush). 3) If s->state == SESSION_CLOSING return (already flushing; repeated EOF/error notifications must not restart or clear anything). 4) For SESSION_AWAITING_CONNECT or SESSION_READY set s->state = SESSION_CLOSING. 5) Do NOT touch the output buffer: its length and bytes are exactly what the write pump must drain, and keeping them is what makes a response already queued for this peer survive a read-side EOF. Do not close the descriptor here either; the descriptor is closed by session_close_immediately() when the queue has drained (called by broker_reap) or when the write side fails, and by session_destroy() during shutdown. The receive buffer is likewise left alone; its remaining bytes were already processed by the caller (a trailing partial packet is simply never parsed again because the read pump only reads while the state is AWAITING_CONNECT or READY).
  OUTPUT:
No return value. A session in AWAITING_CONNECT or READY becomes CLOSING with its queued output untouched; a CLOSING or CLOSED session is unchanged.
  INVARIANTS_USED:
    - CLOSING means no further protocol input is accepted and the queue must drain
    - queued complete packets are preserved until written or until an immediate close
    - CLOSED is terminal
    - the readiness mask must keep EPOLLOUT set while queued output is non-empty, which is how a CLOSING session finishes without sending more data
  PRECONDITION:
s is NULL or points to a live session whose queued output may be empty or non-empty.
  POSTCONDITION:
s->state is SESSION_CLOSING when the session was AWAITING_CONNECT or READY; the descriptor, both buffers, the identifier and the subscriptions are unchanged.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Not thread safe with respect to concurrent transitions on s.
