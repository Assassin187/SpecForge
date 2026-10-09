[PROMPT]
Release the reactor: close the owned epoll descriptor exactly once and free the loop object, leaving the borrowed broker and the borrowed listener descriptor untouched.

[RELY]
STRUCT:
  - NAME:
struct loop
    ROLE:
reactor being released; only epfd is a resource the loop owns
FUNC:
  - NAME:
close
    KIND:
CALL
    ROLE:
release the owned epoll descriptor
  - NAME:
free
    KIND:
CALL
    ROLE:
release the loop object
VAR:
  - NAME:
struct loop
    ROLE:
l->epfd is set to -1 after close so a repeated destroy cannot close a recycled descriptor number

[GUARANTEE]
RAW:
void loop_destroy(struct loop *l)
NAME:
loop_destroy
RETURN:
void
PARAMS:
  - TYPE:
struct loop *
    NAME:
l
    NULLABLE:
true
    OWNERSHIP:
OWNED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
l: the reactor returned by loop_create() and not yet destroyed (NULL tolerated).
  ACTION:
1. If l == NULL return. 2. If l->epfd >= 0, call close(l->epfd) and set l->epfd = -1 (guarding against a repeated call). 3. free(l). The borrowed broker is not destroyed and the borrowed listener is not closed here: main() calls broker_destroy() and net_close_fd(&listener) afterwards, in that documented order.
  OUTPUT:
void. Afterwards no epoll descriptor created by loop_create() remains open and the loop object is released; the broker and all sessions (and the listener descriptor) are still alive and owned by the caller.
  INVARIANTS_USED:
    - the loop owns exactly one resource, its epoll descriptor; closing it drops every registration without touching the registered descriptors themselves
    - session and listener descriptors are owned by the broker, by session objects and by main, never by the loop
    - teardown order is loop_destroy() then the listener close then broker_destroy(), so no callback can run after the reactor is gone
  PRECONDITION:
loop_run() has returned and no further event dispatch will occur for this loop.
  POSTCONDITION:
l is released and no longer dereferenceable; the broker, its sessions and the listener descriptor are unchanged.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Called from the main thread after loop_run() returns; no concurrent access.
