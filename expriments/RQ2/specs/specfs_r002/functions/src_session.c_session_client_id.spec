[PROMPT]
Borrow the stored client identifier and report its length, so same-identifier takeover can compare two sessions without copying.

[RELY]
STRUCT:
  - NAME:
struct session
    ROLE:
owner of the identifier copy and its length
FUNC:

VAR:


[GUARANTEE]
RAW:
const char *session_client_id(const struct session *s, size_t *out_len)
NAME:
session_client_id
RETURN:
const char *
PARAMS:
  - TYPE:
const struct session *
    NAME:
s
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
size_t *
    NAME:
out_len
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
s: borrowed session pointer, possibly NULL. out_len: optional output slot for the length in bytes.
  ACTION:
1) If out_len is not NULL write 0 into it first, so every return path leaves it defined. 2) If s is NULL return NULL. 3) If s->client_id is NULL (no CONNECT accepted yet, or a failed identifier store) return NULL with *out_len already 0. 4) Otherwise write s->client_id_len into *out_len and return s->client_id. The identifier bytes are owned by the session and are NOT NUL terminated, so a caller must compare id_len bytes explicitly; the returned pointer stays valid until the next session_set_client_id(), session_close_immediately() or session_destroy() on the same session.
  OUTPUT:
Borrowed pointer to the stored identifier bytes plus its byte length, or NULL with *out_len == 0 when no identifier is stored or s is NULL.
  INVARIANTS_USED:
    - the client identifier is a length-delimited byte string, never a C string
    - a stored identifier is owned by exactly one session and released by the same session
    - the identifier pointer is invalidated only by a mutating call on that session
  PRECONDITION:
s is NULL or points to a live session; out_len is NULL or points to a writable size_t.
  POSTCONDITION:
*out_len is 0 or the stored identifier length; the session is otherwise unchanged.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Not thread safe with respect to session_set_client_id() on the same session.
