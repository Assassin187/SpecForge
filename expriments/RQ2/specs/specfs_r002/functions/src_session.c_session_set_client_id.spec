[PROMPT]
Store a session-owned copy of the client identifier, replacing any previous copy only after the new allocation succeeded, so same-identifier takeover compares stable length-delimited bytes.

[RELY]
STRUCT:
  - NAME:
struct session
    ROLE:
owner of the previous and new identifier copy
FUNC:
  - NAME:
malloc
    KIND:
TYPE_REF
    ROLE:
allocate the new copy before the old one is released
  - NAME:
memcpy
    KIND:
TYPE_REF
    ROLE:
copy the identifier bytes (they are not NUL terminated)
  - NAME:
free
    KIND:
TYPE_REF
    ROLE:
release the previous copy after the new one is installed
VAR:


[GUARANTEE]
RAW:
int session_set_client_id(struct session *s, const char *id, size_t id_len)
NAME:
session_set_client_id
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
const char *
    NAME:
id
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
size_t
    NAME:
id_len
    NULLABLE:
false
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
s: borrowed session pointer. id: borrowed identifier bytes, not NUL terminated; id_len: their length in bytes (0 is allowed and stores no copy).
  ACTION:
1) If s is NULL return -1. 2) If id_len != 0 and id is NULL return -1. 3) Allocate the replacement FIRST: if id_len == 0 the new copy is the empty state (copy = NULL); otherwise copy = malloc(id_len) and if copy is NULL return -1 with the previously stored identifier untouched (allocate-before-release avoids losing the old copy on failure). 4) memcpy(copy, id, id_len) — an exact byte copy, so an identifier containing bytes that look like string terminators is preserved byte for byte and no strlen is used anywhere. 5) free(s->client_id) — the previous copy, which the session owned; the old owner releases it here and the new owner is the session. 6) s->client_id = copy and s->client_id_len = id_len, published together so getters never see a new pointer with an old length. 7) Return 0. The broker calls this once per accepted CONNECT, so the repeated-operation path is: previous copy released, new copy adopted, no aliasing and no leak of the old allocation.
  OUTPUT:
0 with the identifier stored (client_id_len == id_len, client_id NUL-free) and any previous copy released. -1 for a NULL s, a NULL id with id_len > 0, or allocation failure, in which case the previously stored identifier is unchanged.
  INVARIANTS_USED:
    - a stored identifier is length-delimited and never treated as a C string
    - the session owns exactly one identifier copy; replacing it releases the previous one
    - the size field and the pointer are updated together
  PRECONDITION:
s is NULL or points to a live session; id points to id_len readable bytes when id_len > 0.
  POSTCONDITION:
on success the session owns the new copy and no longer owns the previous one; on failure the session keeps exactly its previous identifier.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Not thread safe; single-threaded per session.
