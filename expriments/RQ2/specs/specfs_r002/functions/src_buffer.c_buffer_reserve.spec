[PROMPT]
Guarantee that at least extra further bytes can be appended without another allocation, growing geometrically, with overflow and allocation failure reported and contents untouched on failure.

[RELY]
STRUCT:
  - NAME:
struct byte_buf
    ROLE:
buffer whose capacity is grown
FUNC:

VAR:
  - NAME:
SIZE_MAX
    ROLE:
Upper bound used for overflow detection (stdint.h)

[GUARANTEE]
RAW:
int buffer_reserve(struct byte_buf *buf, size_t extra)
NAME:
buffer_reserve
RETURN:
int
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
extra
    NULLABLE:
false
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
buf (NULL allowed) and extra, the number of additional bytes that must become writable above buf->len.
  ACTION:
1) If buf is NULL return -1. 2) If extra > SIZE_MAX - buf->len return -1 (the request cannot be represented; contents unchanged). 3) needed = buf->len + extra; if needed <= buf->cap return 0 (no allocation). 4) Choose newcap = buf->cap ? buf->cap : 64, then double newcap while newcap < needed, but if doubling would overflow (newcap > SIZE_MAX / 2) set newcap = needed and stop, so newcap >= needed always. 5) tmp = realloc(buf->data, newcap); if tmp is NULL return -1 and leave data/len/cap exactly as they were (the old allocation stays owned by the buffer). 6) Store buf->data = tmp and buf->cap = newcap; len is unchanged so the existing contents are preserved. Return 0.
  OUTPUT:
0 when buf has capacity for at least len+extra bytes and all previously stored bytes are intact; -1 on overflow or allocation failure with the buffer completely unchanged.
  INVARIANTS_USED:
    - len <= cap after every successful call
    - failed reserve leaves contents and ownership unchanged
    - capacity never shrinks
  PRECONDITION:
buf is NULL or a valid buffer; buf->data is either NULL or a live allocation of buf->cap bytes.
  POSTCONDITION:
On success cap >= len + extra; on failure the triple (data,len,cap) is bitwise unchanged.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Not thread safe; the caller owns the buffer exclusively.
