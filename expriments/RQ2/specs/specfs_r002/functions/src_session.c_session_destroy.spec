[PROMPT]
Release a session completely: descriptor first when still open, then the two buffers, the identifier copy, the subscriptions and finally the object itself.

[RELY]
STRUCT:
  - NAME:
struct session
    ROLE:
the object being destroyed
  - NAME:
struct byte_buf
    ROLE:
receive and output buffers released here
FUNC:
  - NAME:
release_fd
    KIND:
CALL
    ROLE:
close the descriptor if it is still open
  - NAME:
buffer_free
    KIND:
CALL
    ROLE:
release the storage of both buffers
  - NAME:
free_subscriptions
    KIND:
CALL
    ROLE:
release every filter copy and the slot array
  - NAME:
free
    KIND:
TYPE_REF
    ROLE:
release the identifier copy and the session object
VAR:


[GUARANTEE]
RAW:
void session_destroy(struct session *s)
NAME:
session_destroy
RETURN:
void
PARAMS:
  - TYPE:
struct session *
    NAME:
s
    NULLABLE:
true
    OWNERSHIP:
TRANSFER

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
s: the session to destroy, whose ownership is transferred to this function; NULL is allowed and ignored.
  ACTION:
1) If s is NULL return. 2) release_fd(s) FIRST, so the descriptor stops generating events before the buffers holding its pending bytes are freed; this calls net_close_fd(&s->fd) which is a no-op when the session already closed itself. 3) buffer_free(&s->input) — unconsumed stream bytes, including a trailing partial frame, are dropped. 4) buffer_free(&s->output) — if the session was closed immediately this is already empty; if it is destroyed while CLOSING (loop or broker shutdown) any unflushed queued packets are dropped here, which is the documented shutdown behaviour. 5) free(s->client_id); s->client_id = NULL; s->client_id_len = 0. 6) free_subscriptions(s) releases each filter copy and the slot array. 7) free(s). Every owned resource is reached through an owning pointer that is either NULL or a live allocation, so a session created but never used destroys cleanly, and a session that was closed first destroys cleanly because every release is idempotent.
  OUTPUT:
No return value. The descriptor is closed if still open and all session-owned heap storage is released; the pointer s must not be used again.
  INVARIANTS_USED:
    - every owned allocation is reachable from the session through an owning pointer
    - releasing one session must not touch another session's descriptor or storage
    - clearing a length does not release storage, so each owner is freed explicitly
  PRECONDITION:
s is NULL or a pointer to a session created by session_create that has not already been destroyed.
  POSTCONDITION:
no descriptor and no heap block owned by s remains reachable; the session object itself is freed.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Not thread safe; the caller is the single owner of s and must have removed it from any registry already.
