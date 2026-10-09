[PROMPT]
Hand out a borrowed write slot directly after the last valid byte together with the number of bytes that may be written there; storage is unchanged.

[RELY]
STRUCT:
  - NAME:
struct byte_buf
    ROLE:
buffer whose spare tail is exposed
FUNC:

VAR:


[GUARANTEE]
RAW:
uint8_t *buffer_tail(struct byte_buf *buf, size_t *avail)
NAME:
buffer_tail
RETURN:
uint8_t *
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
size_t *
    NAME:
avail
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
buf (NULL allowed) and an optional size_t out-parameter avail.
  ACTION:
If buf is NULL, write 0 through avail when avail is not NULL and return NULL. Otherwise compute spare = buf->cap - buf->len and write it through avail when avail is not NULL. Return buf->data + buf->len when buf->data is not NULL; return NULL when buf->data is NULL (no storage allocated yet), so the caller must call buffer_reserve before writing. The returned pointer borrows storage owned by buf and stays valid only until the next buffer operation that reallocates or consumes bytes.
  OUTPUT:
A borrowed pointer to the first unwritten byte plus the writable byte count through *avail; NULL when no storage is allocated.
  INVARIANTS_USED:
    - len <= cap
    - the tail is never part of the valid length until buffer_commit
    - pointer arithmetic is performed only on non-NULL data
  PRECONDITION:
buf is NULL or a valid buffer.
  POSTCONDITION:
*avail == cap - len and the returned pointer is data + len or NULL; data/len/cap unchanged.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Not thread safe; the caller owns the buffer exclusively.
