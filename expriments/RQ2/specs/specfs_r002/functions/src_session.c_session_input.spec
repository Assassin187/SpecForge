[PROMPT]
Borrow the receive buffer that holds unconsumed stream bytes so the read pump can append and the protocol core can frame and consume from it.

[RELY]
STRUCT:
  - NAME:
struct session
    ROLE:
owner of the receive buffer
  - NAME:
struct byte_buf
    ROLE:
borrowed view of the unconsumed receive bytes
FUNC:

VAR:


[GUARANTEE]
RAW:
struct byte_buf *session_input(struct session *s)
NAME:
session_input
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
1) If s is NULL return NULL. 2) Return &s->input. The buffer's storage is owned by the session; the caller may append through buffer_reserve/buffer_tail/buffer_commit and may consume through buffer_consume, but must not free the buffer. The returned pointer stays valid until session_destroy(); it is NOT invalidated by buffer growth, because the session owns the byte_buf struct and only its data pointer changes.
  OUTPUT:
Pointer to the session's receive buffer, or NULL for a NULL session.
  INVARIANTS_USED:
    - the receive buffer holds only unconsumed bytes: every handled packet is consumed from the front
    - a stream read appends to the tail and never rewrites earlier bytes
    - the byte_buf struct itself is never reallocated, only its data array
  PRECONDITION:
s is NULL or points to a live session.
  POSTCONDITION:
the session is unchanged.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Not thread safe; the buffer must be used by one thread at a time.
