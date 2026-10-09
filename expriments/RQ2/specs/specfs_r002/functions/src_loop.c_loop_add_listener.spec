[PROMPT]
Register the nonblocking listener descriptor in the epoll set for EPOLLIN only, tagged with a NULL event data pointer so loop_run() can tell listener events from session events.

[RELY]
STRUCT:
  - NAME:
struct loop
    ROLE:
reactor receiving the registration; stores the borrowed descriptor number and uses it for accepting
  - NAME:
struct epoll_event
    ROLE:
registration record: events mask plus the NULL data pointer tag used for the listener
FUNC:
  - NAME:
epoll_ctl
    KIND:
CALL
    ROLE:
EPOLL_CTL_ADD the listener for EPOLLIN
VAR:
  - NAME:
EPOLL_CTL_ADD
    ROLE:
epoll operation code for the first registration
  - NAME:
EPOLLIN
    ROLE:
only interest requested for the listener: readable pending connections
  - NAME:
EPOLL_CLOEXEC
    ROLE:
not used here; the listener already carries O_NONBLOCK|FD_CLOEXEC from net_listen and its mode is not changed

[GUARANTEE]
RAW:
int loop_add_listener(struct loop *l, int listener_fd)
NAME:
loop_add_listener
RETURN:
int
PARAMS:
  - TYPE:
struct loop *
    NAME:
l
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
int
    NAME:
listener_fd
    NULLABLE:
false
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
l: reactor created by loop_create(). listener_fd: the already-nonblocking, already-FD_CLOEXEC listening socket returned by net_listen(); borrowed and never closed here.
  ACTION:
1. If l == NULL or listener_fd < 0 return -1. 2. Build struct epoll_event ev; zero it first so no uninitialised bytes reach the kernel: ev.events = EPOLLIN (level-triggered; EPOLLET is never set) and ev.data.ptr = NULL (the listener tag; session events carry a struct session pointer instead). 3. rc = epoll_ctl(l->epfd, EPOLL_CTL_ADD, listener_fd, &ev). 4. If rc != 0 return -1 without touching l->listener_fd. 5. On success l->listener_fd = listener_fd and return 0.
  OUTPUT:
0 with the listener registered for EPOLLIN: loop_run() will receive events whose data.ptr is NULL and will call accept_ready(). -1 when l is NULL, listener_fd is negative, or epoll_ctl failed (descriptor already registered, bad descriptor, allocation failure); on -1 no registration was added and l->listener_fd keeps its previous value (-1 before the first successful call).
  INVARIANTS_USED:
    - the listener is a nonblocking descriptor, so dispatching a read-side accept loop can never block the reactor
    - listener events are identified by data.ptr == NULL; session events always carry a non-NULL session pointer
    - the loop never closes or modifies the listener: mode changes are made by net_listen() before this call
  PRECONDITION:
l is a live reactor with an open epoll descriptor; listener_fd is a listening socket already in nonblocking mode.
  POSTCONDITION:
either the listener is unambiguously registered for EPOLLIN with the NULL tag, or the epoll set is exactly as it was before the call.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Called once before loop_run(); no concurrent access.
