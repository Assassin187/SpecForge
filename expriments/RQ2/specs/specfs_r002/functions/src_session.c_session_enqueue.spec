[PROMPT]
Append one complete encoded packet to the session's queued output with all-or-nothing failure semantics, so a fan-out recipient either gets the whole packet or is closed without a truncated frame.

[RELY]
STRUCT:
  - NAME:
struct session
    ROLE:
owner of the queued-output buffer
  - NAME:
struct byte_buf
    ROLE:
destination of the appended packet bytes
FUNC:
  - NAME:
buffer_append
    KIND:
CALL
    ROLE:
reserve space and copy the whole packet in one operation
VAR:


[GUARANTEE]
RAW:
int session_enqueue(struct session *s, const void *data, size_t len)
NAME:
session_enqueue
RETURN:
int
PARAMS:
  - TYPE:
struct session *
    NAME:
s
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
const void *
    NAME:
data
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
size_t
    NAME:
len
    NULLABLE:
false
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
s: borrowed session pointer. data: borrowed pointer to the complete encoded packet (or any byte range) to append; len: number of bytes, possibly 0.
  ACTION:
1) If s is NULL return -1. 2) If len != 0 and data is NULL return -1 (a non-empty range without a source cannot be appended). 3) Return buffer_append(&s->output, data, len). buffer_append reserves the extra capacity first and copies in a single memcpy, so a failure yields -1 with the previously queued bytes untouched: no half packet can enter the queue. When len == 0 the call succeeds and changes nothing. Appending never replaces or frees existing queued bytes; the queue can hold several complete packets in the order they were produced, which is exactly the order the write pump drains them. A successful append makes the queue non-empty, which is what makes the readiness mask set EPOLLOUT for this session.
  OUTPUT:
0 when the len bytes were appended after the bytes already queued; -1 when s is NULL, data is NULL with len != 0, or the buffer could not be grown. On -1 the previous queue contents and length are unchanged.
  INVARIANTS_USED:
    - the queue holds complete packets only, so appending one packet at a time preserves packet boundaries
    - queued bytes are preserved across appends and across the READY -> CLOSING transition
    - a non-empty queue implies the session must be registered for EPOLLOUT
  PRECONDITION:
s is NULL or points to a live, non-CLOSED session; data points to len readable bytes when len > 0.
  POSTCONDITION:
on success the queue length grew by exactly len; on failure nothing changed.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Not thread safe; one thread owns a session's queue at a time.
