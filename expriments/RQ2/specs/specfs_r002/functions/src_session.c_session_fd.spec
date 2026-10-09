[PROMPT]
Borrow the descriptor owned by a session, or -1 when there is none, so callers can register/synchronize a descriptor without owning it.

[RELY]
STRUCT:
  - NAME:
struct session
    ROLE:
source of the owned descriptor number
FUNC:

VAR:


[GUARANTEE]
RAW:
int session_fd(const struct session *s)
NAME:
session_fd
RETURN:
int
PARAMS:
  - TYPE:
const struct session *
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
1) If s is NULL return -1. 2) Return s->fd. The value is read only; this function never closes, registers or modifies the descriptor, and it does not take ownership. After session_close_immediately() the field is -1, so the returned value becomes -1 and a caller that later registers the descriptor would use -1 (an invalid epoll target) rather than a recycled descriptor number.
  OUTPUT:
-1 for a NULL session or for a session whose descriptor has already been closed; otherwise the nonnegative descriptor number owned by the session.
  INVARIANTS_USED:
    - a session owns at most one descriptor at a time
    - the descriptor field is -1 exactly when the descriptor is already released
    - reading session state is observation only and must not change ownership
  PRECONDITION:
s is NULL or points to a live session that has not been destroyed.
  POSTCONDITION:
the session is unchanged.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Not thread safe with respect to concurrent mutation of s.
