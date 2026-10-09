[PROMPT]
Release the storage owned by a buffer and return it to the canonical empty state; safe to call repeatedly and after a failed reserve/append.

[RELY]
STRUCT:
  - NAME:
struct byte_buf
    ROLE:
buffer whose storage is released
FUNC:
  - NAME:
buffer_init
    KIND:
CALL
    ROLE:
Reset the fields after the release
VAR:


[GUARANTEE]
RAW:
void buffer_free(struct byte_buf *buf)
NAME:
buffer_free
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
buf, a caller-owned buffer, or NULL. Precondition: any storage reachable through buf->data was allocated by buffer_reserve/buffer_append and has not been transferred to another owner.
  ACTION:
If buf is NULL, return. Otherwise call free(buf->data) exactly once (free(NULL) is permitted and is the path taken by a buffer that never allocated storage), then set data = NULL, len = 0, cap = 0 by calling buffer_init(buf). The length is not simply cleared: the allocation itself is released so that no memory is retained after the call.
  OUTPUT:
The allocation is returned to the allocator and buf is empty; no value is returned.
  INVARIANTS_USED:
    - only the owner frees storage
    - consume/shorten never frees storage, so free() must target buf->data, not a suffix
  PRECONDITION:
buf is NULL or holds a valid struct byte_buf; no aliasing owner of buf->data remains.
  POSTCONDITION:
data == NULL, len == 0, cap == 0
  IDEMPOTENT:
true
  THREAD_SAFETY:
Not thread safe; the caller owns the buffer exclusively.
