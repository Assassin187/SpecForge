[PROMPT]
Borrow the queued-output buffer so the write pump can drain complete encoded packets and the readiness mask can be derived from its length.

[RELY]
STRUCT:
  - NAME:
struct session
    ROLE:
owner of the queued-output buffer
  - NAME:
struct byte_buf
    ROLE:
borrowed view of the bytes waiting to be written
FUNC:

VAR:


[GUARANTEE]
RAW:
struct byte_buf *session_output(struct session *s)
NAME:
session_output
RETURN:
struct byte_buf *
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
1) If s is NULL return NULL. 2) Return &s->output. The buffer holds only complete encoded packets; the writer consumes from the front with buffer_consume(buf, n) after a partial write and must not free it. A non-empty output buffer is the condition that keeps EPOLLOUT set for this session, so an idle recipient with queued output is still drained. session_close_immediately() discards the contents with buffer_free() so a CLOSED session always reports length 0.
  OUTPUT:
Pointer to the session's queued-output buffer, or NULL for a NULL session.
  INVARIANTS_USED:
    - the queue contains complete packets only, so consuming any prefix leaves a valid packet boundary
    - queued bytes are preserved until written or until the session is closed immediately
    - a non-empty queue implies the session needs EPOLLOUT
  PRECONDITION:
s is NULL or points to a live session.
  POSTCONDITION:
the session is unchanged.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Not thread safe; the buffer must be used by one thread at a time.
