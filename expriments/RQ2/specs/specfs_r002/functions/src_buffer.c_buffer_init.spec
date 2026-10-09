[PROMPT]
Bring a byte_buf into the canonical empty state (data NULL, len 0, cap 0) without touching any storage.

[RELY]
STRUCT:
  - NAME:
struct byte_buf
    ROLE:
buffer that becomes the empty buffer
FUNC:

VAR:


[GUARANTEE]
RAW:
void buffer_init(struct byte_buf *buf)
NAME:
buffer_init
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

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
buf, a caller-owned buffer object, or NULL.
  ACTION:
If buf is NULL return immediately. Otherwise set buf->data = NULL, buf->len = 0 and buf->cap = 0. No allocation, no free: any storage previously reachable from buf->data must already have been released by the caller (buffer_free), because init is used by owners on freshly created objects only.
  OUTPUT:
buf denotes an empty buffer with no owned storage; the function returns no value.
  INVARIANTS_USED:
    - len <= cap
    - data == NULL implies cap == 0
    - a zero cap means no storage may be read or written
  PRECONDITION:
buf points to writable storage of sizeof(struct byte_buf) or is NULL; buf->data is not owned by any other live object.
  POSTCONDITION:
data == NULL, len == 0, cap == 0
  IDEMPOTENT:
true
  THREAD_SAFETY:
Not thread safe: the caller owns the buffer exclusively.
