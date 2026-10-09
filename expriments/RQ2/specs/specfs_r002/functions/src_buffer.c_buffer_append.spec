[PROMPT]
Append len raw bytes (which may contain NUL and any binary value) to the buffer with all-or-nothing failure semantics.

[RELY]
STRUCT:
  - NAME:
struct byte_buf
    ROLE:
destination buffer
FUNC:
  - NAME:
buffer_reserve
    KIND:
CALL
    ROLE:
Guarantee capacity for the whole append in one step
VAR:


[GUARANTEE]
RAW:
int buffer_append(struct byte_buf *buf, const void *src, size_t len)
NAME:
buffer_append
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
const void *
    NAME:
src
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
buf (NULL allowed), src pointing to len caller-owned bytes (may be NULL only when len is 0), len.
  ACTION:
1) If buf is NULL return -1. 2) If len == 0 return 0 without reading src (an empty append is a successful no-op, which is what preserves an empty PUBLISH payload). 3) If src is NULL return -1. 4) If buffer_reserve(buf, len) != 0 return -1: nothing was written and no byte of the previous contents changed. 5) memcpy(buf->data + buf->len, src, len) then buf->len += len; return 0. src is borrowed and never retained, so a caller may pass a slice of a packet buffer and may free it immediately after the call.
  OUTPUT:
0 when all len bytes are appended in order and len grew by exactly len; -1 when no byte was appended and the previous contents are untouched.
  INVARIANTS_USED:
    - append is all-or-nothing
    - no implicit NUL terminator is added
    - binary payload bytes including 0x00 are stored verbatim
  PRECONDITION:
buf is NULL or a valid buffer; src points to at least len readable bytes when len > 0.
  POSTCONDITION:
On success the last len bytes of the buffer equal src[0..len-1] and len increased by len; on failure the buffer is unchanged.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Not thread safe; the caller owns the buffer exclusively.
