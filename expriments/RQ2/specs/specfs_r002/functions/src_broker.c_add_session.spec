[PROMPT]
Create a session for an accepted descriptor and append it to the registry, growing the slot array only when it is full, with ownership of the descriptor moving to the broker exactly when this function succeeds.

[RELY]
STRUCT:
  - NAME:
struct broker
    ROLE:
registry receiving the new owned session pointer
  - NAME:
struct session
    ROLE:
newly created connection, owned by the broker after success
FUNC:
  - NAME:
session_create
    KIND:
CALL
    ROLE:
allocate the session and adopt the descriptor
  - NAME:
session_destroy
    KIND:
CALL
    ROLE:
undo the creation when the registry could not grow, which also closes the adopted descriptor
  - NAME:
realloc
    KIND:
CALL
    ROLE:
grow the session pointer array without losing the existing pointers
VAR:
  - NAME:
BROKER_INITIAL_SESSION_CAP
    ROLE:
slots requested when the array is still empty (guards against a zero-size growth request)
  - NAME:
struct broker
    ROLE:
b->count, b->cap and b->sessions are updated together so the array never holds an unowned pointer

[GUARANTEE]
RAW:
static int add_session(struct broker *b, int fd, struct session **out_session)
NAME:
add_session
RETURN:
int
PARAMS:
  - TYPE:
struct broker *
    NAME:
b
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
int
    NAME:
fd
    NULLABLE:
false
    OWNERSHIP:
OWNED_BY_CALLER
  - TYPE:
struct session **
    NAME:
out_session
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
b: broker registry (non-NULL). fd: an accepted descriptor already set to nonblocking and close-on-exec by the accept step; it is borrowed until the session adopts it. out_session: optional output slot for the created session pointer.
  ACTION:
1. s = NULL; rc = session_create(fd, &s). 2. If rc != 0 return -1: no session was created and fd is still open and still the caller's responsibility, so the execution layer closes it and other sessions are unaffected. 3. If b->count == b->cap, grow the array: new_cap = (b->cap == 0) ? BROKER_INITIAL_SESSION_CAP : b->cap * 2; if new_cap < b->cap or new_cap > (SIZE_MAX / sizeof *b->sessions) (overflow) or realloc(b->sessions, new_cap * sizeof *b->sessions) returns NULL, then session_destroy(s) to release the creation (which also closes the just-adopted descriptor) and return -1. realloc keeps the existing slot values, so the move never invalidates the sessions themselves - only borrowed slot pointers held elsewhere, which callers do not keep. 4. On success: b->sessions = grown array, b->cap = new_cap. 5. b->sessions[b->count] = s; b->count++. 6. If out_session is non-NULL set *out_session = s (borrowed). 7. Return 0.
  OUTPUT:
0 with the new session appended at index b->count - 1, the broker owning the descriptor and the session starting in SESSION_AWAITING_CONNECT with no identifier and no subscriptions. -1 when the session could not be created or the array could not grow; on -1 the registry is unchanged (same count, same capacity, no slot holds the new session), fd is closed only in the case where the session had already adopted it, and out_session is untouched.
  INVARIANTS_USED:
    - an accepted descriptor belongs to the broker only after the session adopts it; a failure before that leaves the descriptor to the caller
    - every pointer stored in the registry is owned by the broker and destroyed exactly once
    - the array grows geometrically and never shrinks, so a burst of reconnects does not repeatedly reallocate
    - capacity growth is checked against overflow before allocation so the slot count cannot wrap
    - a fresh session never inherits subscriptions or an identifier from an earlier connection
  PRECONDITION:
b is non-NULL with a consistent sessions/count/cap triple (from broker_create); fd is a valid accepted nonblocking descriptor not owned by any other session.
  POSTCONDITION:
either the registry has one more live session and the broker owns fd, or the registry is exactly as before and the descriptor's ownership is unchanged (caller's) or already released by the rolled-back session.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Single-threaded reactor; the registry triple is only mutated here and in remove_session_at.
