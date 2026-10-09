[PROMPT]
Drop n leading bytes that have been fully processed, shifting the remaining bytes to the front while keeping the allocation (no shrink, no free).

[RELY]
STRUCT:
  - NAME:
struct byte_buf
    ROLE:
buffer whose prefix is dropped
FUNC:

VAR:


[GUARANTEE]
RAW:
void buffer_consume(struct byte_buf *buf, size_t n)
NAME:
buffer_consume
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
buf (NULL allowed) and n, the number of leading bytes to discard.
  ACTION:
If buf is NULL return. If n > buf->len set n = buf->len (clamping; a larger request drains the buffer). If n == 0 return. Otherwise memmove(buf->data, buf->data + n, buf->len - n) and set buf->len -= n. cap and the allocation are unchanged: clearing a length never releases storage, and dropping bytes never invalidates another owner because the buffer owns its storage alone.
  OUTPUT:
The first n bytes are gone, the remaining bytes start at data[0], len is reduced by n and cap is unchanged.
  INVARIANTS_USED:
    - len <= cap
    - storage is released only by buffer_free
    - consume keeps the allocation so a stream pump can reuse it
  PRECONDITION:
buf is NULL or a valid buffer.
  POSTCONDITION:
len' == len - min(n, len) and bytes [0, len') equal the old bytes [min(n,len), len)
  IDEMPOTENT:
false
  THREAD_SAFETY:
Not thread safe; the caller owns the buffer exclusively.
