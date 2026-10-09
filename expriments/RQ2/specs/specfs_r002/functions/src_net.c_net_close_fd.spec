[PROMPT]
Close an owned descriptor once and mark it invalid, so any later cleanup cannot close the same descriptor twice (or close an unrelated descriptor that reused the number).

[RELY]
STRUCT:

FUNC:
  - NAME:
close
    KIND:
TYPE_REF
    ROLE:
release the descriptor once
VAR:


[GUARANTEE]
RAW:
void net_close_fd(int *fd)
NAME:
net_close_fd
RETURN:
void
PARAMS:
  - TYPE:
int *
    NAME:
fd
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
fd: pointer to a descriptor slot owned by the caller (a session field or a local in main/loop).
  ACTION:
1) If fd is NULL return. 2) If *fd < 0 return (idempotent no-op: the slot is already invalid). 3) close(*fd) and ignore the return value; save errno only if diagnostics are wanted. 4) *fd = -1 unconditionally, so the slot can be passed here again without closing a recycled descriptor number. 5) Return. This is the single place where the broker releases descriptors, so the ownership sequence is uniform: net_listen returns one descriptor to main, net_accept hands each peer descriptor to a session through broker_accept/session_create, and every release (session teardown, immediate close after a protocol error, loop shutdown, main cleanup, listener teardown) goes through this function.
  OUTPUT:
No return value. After the call *fd is -1 and the underlying descriptor, if any, was closed exactly once. A slot that is already -1 is left at -1.
  INVARIANTS_USED:
    - each descriptor is owned by exactly one slot and closed exactly once
    - clearing a slot's numeric value after closing prevents a double close of a recycled descriptor number
    - releasing a session must never affect another session's descriptor
  PRECONDITION:
fd is NULL or points to an int slot holding either -1 or a descriptor this caller owns.
  POSTCONDITION:
*fd == -1 and no descriptor owned by the previous value remains open (or none existed).
  IDEMPOTENT:
true
  THREAD_SAFETY:
Safe for distinct slots; a slot must not be closed concurrently by two threads.
