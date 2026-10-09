[PROMPT]
Close the descriptor owned by a session exactly once by routing the release through net_close_fd, which invalidates the slot.

[RELY]
STRUCT:
  - NAME:
struct session
    ROLE:
owner of the descriptor being released
FUNC:
  - NAME:
net_close_fd
    KIND:
CALL
    ROLE:
close s->fd when it is >= 0 and set it to -1
VAR:


[GUARANTEE]
RAW:
static void release_fd(struct session *s)
NAME:
release_fd
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
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
s: borrowed session pointer, possibly NULL.
  ACTION:
1) If s is NULL return. 2) net_close_fd(&s->fd). That helper returns immediately when s->fd < 0 and otherwise closes the descriptor and stores -1, so the descriptor is released at most once no matter how often this function runs (immediate close, flush completion, destroy, or a repeated teardown). 3) Return. This is the only place in src/session.c that closes a descriptor, which keeps the ownership sequence uniform: the descriptor enters the session in session_create() and leaves here.
  OUTPUT:
No return value. After the call s->fd is -1 and any descriptor previously owned by s has been closed exactly once.
  INVARIANTS_USED:
    - a session closes its own descriptor and never another session's
    - the descriptor field is -1 exactly when the descriptor is already released
    - closing twice would close a descriptor that accept reused, so the slot must be invalidated after the close
  PRECONDITION:
s is NULL or points to a live session whose descriptor field holds either -1 or a descriptor owned by that session.
  POSTCONDITION:
s->fd == -1; no other session state is touched.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Not thread safe with respect to concurrent teardown of the same session.
