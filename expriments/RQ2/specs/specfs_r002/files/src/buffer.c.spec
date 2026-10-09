[PROMPT]
LANG:
C99
ROLE:
Growable byte buffer primitive used for receive buffers, queued output and scratch encoding; explicit-length binary semantics only.

[RELY]
DEPENDENCY:
  - src/buffer.h
SYSTEM_DEPENDENCY:
  - stdlib.h
  - string.h
  - stdint.h
  - stddef.h

[GUARANTEE]
PATH:
src/buffer.h
DEPENDENCY:

SYSTEM_DEPENDENCY:
  - stddef.h
  - stdint.h
DATA:
  - NAME:
struct byte_buf
    KIND:
TYPE
    VISIBILITY:
PUBLIC
    ROLE:
Byte container with owned storage; len is the valid byte count, cap the allocation size.
    TYPE_SPEC:
      TYPE_KIND:
STRUCT
      FIELDS:
        - NAME:
data
          TYPE:
uint8_t *
          ROLE:
Owned allocation; NULL while empty and unallocated
        - NAME:
len
          TYPE:
size_t
          ROLE:
Number of valid bytes
        - NAME:
cap
          TYPE:
size_t
          ROLE:
Allocated capacity in bytes
INTERFACE:
  - SIGNATURE:
void buffer_init(struct byte_buf *buf)
    NAME:
buffer_init
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Set buffer to empty unallocated state
    VISIBILITY:
public
  - SIGNATURE:
void buffer_free(struct byte_buf *buf)
    NAME:
buffer_free
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Release owned storage and reset to empty state
    VISIBILITY:
public
  - SIGNATURE:
int buffer_reserve(struct byte_buf *buf, size_t extra)
    NAME:
buffer_reserve
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Ensure capacity for extra appended bytes
    VISIBILITY:
public
  - SIGNATURE:
uint8_t *buffer_tail(struct byte_buf *buf, size_t *avail)
    NAME:
buffer_tail
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Borrow append position and writable space
    VISIBILITY:
public
  - SIGNATURE:
void buffer_commit(struct byte_buf *buf, size_t n)
    NAME:
buffer_commit
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Advance length after writing through buffer_tail
    VISIBILITY:
public
  - SIGNATURE:
int buffer_append(struct byte_buf *buf, const void *src, size_t len)
    NAME:
buffer_append
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Append bytes with unchanged-on-failure guarantee
    VISIBILITY:
public
  - SIGNATURE:
void buffer_consume(struct byte_buf *buf, size_t n)
    NAME:
buffer_consume
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Drop leading bytes while retaining storage
    VISIBILITY:
public

[SPECIFICATION]
SOURCE:
  PATH:
src/buffer.c
  DEPENDENCY:
    - src/buffer.h
  SYSTEM_DEPENDENCY:
    - stdlib.h
    - string.h
    - stdint.h
    - stddef.h
  DATA:

  INTERFACE:
    - SIGNATURE:
void buffer_init(struct byte_buf *buf)
      NAME:
buffer_init
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Set data NULL, len 0, cap 0
      VISIBILITY:
public
    - SIGNATURE:
void buffer_free(struct byte_buf *buf)
      NAME:
buffer_free
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
free(data) then reset to empty state; idempotent
      VISIBILITY:
public
    - SIGNATURE:
int buffer_reserve(struct byte_buf *buf, size_t extra)
      NAME:
buffer_reserve
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Grow capacity to at least len+extra with overflow and allocation failure checks
      VISIBILITY:
public
    - SIGNATURE:
uint8_t *buffer_tail(struct byte_buf *buf, size_t *avail)
      NAME:
buffer_tail
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Return data+len and cap-len
      VISIBILITY:
public
    - SIGNATURE:
void buffer_commit(struct byte_buf *buf, size_t n)
      NAME:
buffer_commit
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
len += n for n committed through buffer_tail
      VISIBILITY:
public
    - SIGNATURE:
int buffer_append(struct byte_buf *buf, const void *src, size_t len)
      NAME:
buffer_append
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Reserve then memcpy; failure leaves contents unchanged
      VISIBILITY:
public
    - SIGNATURE:
void buffer_consume(struct byte_buf *buf, size_t n)
      NAME:
buffer_consume
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
memmove remaining bytes to front, len -= n, keep storage
      VISIBILITY:
public
PUBLIC_SYMBOLS:
  - NAME:
struct byte_buf
    KIND:
TYPE
    ROLE:
Byte container
  - NAME:
buffer_init
    KIND:
FUNC
    SIGNATURE:
void buffer_init(struct byte_buf *buf)
    ROLE:
Initialize
  - NAME:
buffer_free
    KIND:
FUNC
    SIGNATURE:
void buffer_free(struct byte_buf *buf)
    ROLE:
Release
  - NAME:
buffer_reserve
    KIND:
FUNC
    SIGNATURE:
int buffer_reserve(struct byte_buf *buf, size_t extra)
    ROLE:
Grow
  - NAME:
buffer_tail
    KIND:
FUNC
    SIGNATURE:
uint8_t *buffer_tail(struct byte_buf *buf, size_t *avail)
    ROLE:
Write slot
  - NAME:
buffer_commit
    KIND:
FUNC
    SIGNATURE:
void buffer_commit(struct byte_buf *buf, size_t n)
    ROLE:
Advance length
  - NAME:
buffer_append
    KIND:
FUNC
    SIGNATURE:
int buffer_append(struct byte_buf *buf, const void *src, size_t len)
    ROLE:
Append
  - NAME:
buffer_consume
    KIND:
FUNC
    SIGNATURE:
void buffer_consume(struct byte_buf *buf, size_t n)
    ROLE:
Consume
