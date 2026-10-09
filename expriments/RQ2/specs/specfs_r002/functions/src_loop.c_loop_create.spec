[PROMPT]
Allocate the reactor state and obtain its epoll descriptor, publishing the only owning loop pointer; the broker is borrowed for the lifetime of the loop.

[RELY]
STRUCT:
  - NAME:
struct loop
    ROLE:
reactor object being created: epfd, borrowed broker, listener_fd, stop_flag, stopped
  - NAME:
struct broker
    ROLE:
registry the reactor will dispatch for; borrowed, created by the caller with broker_create
FUNC:
  - NAME:
calloc
    KIND:
TYPE_REF
    ROLE:
allocate the zeroed loop object
  - NAME:
epoll_create1
    KIND:
CALL
    ROLE:
create the owned epoll descriptor
  - NAME:
free
    KIND:
CALL
    ROLE:
release the loop object when epoll_create1 fails
VAR:
  - NAME:
EPOLL_CLOEXEC
    ROLE:
flag so the epoll descriptor is not inherited across exec
  - NAME:
struct loop
    ROLE:
out_loop receives the only owning pointer to the new reactor

[GUARANTEE]
RAW:
int loop_create(struct broker *broker, struct loop **out_loop)
NAME:
loop_create
RETURN:
int
PARAMS:
  - TYPE:
struct broker *
    NAME:
broker
    NULLABLE:
false
    OWNERSHIP:
BORROWED
  - TYPE:
struct loop **
    NAME:
out_loop
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
broker: the already-created registry the reactor will serve (borrowed, must outlive the loop). out_loop: caller output slot for the single owning reactor pointer.
  ACTION:
1. If broker == NULL or out_loop == NULL return -1. 2. *out_loop = NULL so a caller that ignores the result cannot use an indeterminate pointer. 3. l = calloc(1, sizeof *l); if l == NULL return -1. 4. l->epfd = epoll_create1(EPOLL_CLOEXEC); if (l->epfd < 0) { free(l); return -1; } (calloc already left the descriptor field 0, which is a valid fd, so assign the epoll result before any other use of the object). 5. l->broker = broker (borrowed; the loop never creates, destroys or replaces it). 6. l->listener_fd = -1 (no listener registered and no descriptor owned yet). 7. l->stop_flag = NULL (no signal flag published) and l->stopped = 0 (no internal stop request). 8. *out_loop = l; return 0.
  OUTPUT:
0 with *out_loop pointing to a reactor owning exactly one epoll descriptor, borrowing broker, with no listener registered and no stop request. -1 with *out_loop == NULL when broker is NULL, out_loop is NULL, the loop object could not be allocated, or epoll_create1 failed; in every failure case any descriptor or allocation made by the failing attempt has already been released.
  INVARIANTS_USED:
    - the loop owns its epoll descriptor and borrows the broker; loop_destroy() closes only the descriptor and frees only the loop object
    - the listener descriptor is supplied later by loop_add_listener() and stays owned by main()
    - a signal handler may only write the sig_atomic_t object later published with loop_set_stop_flag()
  PRECONDITION:
broker is a live registry returned by broker_create() and out_loop points to writable storage of type struct loop *.
  POSTCONDITION:
either the caller owns a reactor with an open epoll descriptor and no listener, or no reactor and no descriptor exist.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Called once before loop_run(); no concurrent access.
