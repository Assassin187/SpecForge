[PROMPT]
Publish n bytes written through the borrowed slot returned by buffer_tail by advancing the valid length; no bytes are copied.

[RELY]
STRUCT:
  - NAME:
struct byte_buf
    ROLE:
buffer whose length advances
FUNC:

VAR:


[GUARANTEE]
RAW:
void buffer_commit(struct byte_buf *buf, size_t n)
NAME:
buffer_commit
RETURN:
void
PARAMS:
  - TYPE:
struct byte_buf *
    NAME:
buf
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
size_t
    NAME:
n
    NULLABLE:
false
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
buf (NULL allowed) and n, the number of bytes that were written starting at the pointer most recently returned by buffer_tail for this buffer.
  ACTION:
If buf is NULL return. If n > buf->cap - buf->len the precondition was violated: change nothing (defensive, so a misbehaving caller cannot make len exceed cap). Otherwise add n to buf->len. n == 0 is a permitted no-op.
  OUTPUT:
len increased by n (or unchanged on a violated precondition); no value returned.
  INVARIANTS_USED:
    - len <= cap
    - commit is the only way bytes written through buffer_tail become visible
  PRECONDITION:
n bytes were written at buffer_tail(buf) and n <= cap - len.
  POSTCONDITION:
len <= cap and the first len bytes are valid
  IDEMPOTENT:
false
  THREAD_SAFETY:
Not thread safe; the caller owns the buffer exclusively.
