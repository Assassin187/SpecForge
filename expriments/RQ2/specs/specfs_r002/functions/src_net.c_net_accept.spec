[PROMPT]
Accept one pending peer with accept(), then explicitly establish O_NONBLOCK and FD_CLOEXEC on the returned descriptor with fcntl(), mapping would-block to 0 and never treating an accept error as fatal for the listener.

[RELY]
STRUCT:

FUNC:
  - NAME:
accept
    KIND:
CALL
    ROLE:
take one queued connection from the listener
  - NAME:
fcntl
    KIND:
CALL
    ROLE:
set O_NONBLOCK and FD_CLOEXEC on the accepted descriptor
VAR:
  - NAME:
EINTR
    ROLE:
retry the accept after a signal (SIGINT/SIGTERM)
  - NAME:
EAGAIN
    ROLE:
no pending connection: reported as the return value 0
  - NAME:
EWOULDBLOCK
    ROLE:
same meaning as EAGAIN on Linux and also mapped to 0
  - NAME:
F_SETFL
    ROLE:
fcntl command used to add O_NONBLOCK to the peer descriptor
  - NAME:
F_SETFD
    ROLE:
fcntl command used to add FD_CLOEXEC to the peer descriptor
  - NAME:
O_NONBLOCK
    ROLE:
file status flag established explicitly on every accepted descriptor
  - NAME:
FD_CLOEXEC
    ROLE:
descriptor flag established explicitly on every accepted descriptor

[GUARANTEE]
RAW:
int net_accept(int listener_fd, int *out_fd)
NAME:
net_accept
RETURN:
int
PARAMS:
  - TYPE:
int
    NAME:
listener_fd
    NULLABLE:
false
    OWNERSHIP:
BORROWED
  - TYPE:
int *
    NAME:
out_fd
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
listener_fd: an open, nonblocking, close-on-exec listening socket. out_fd: writable slot receiving the accepted descriptor on success. No input buffer is involved.
  ACTION:
1) If out_fd is NULL return -1 (nothing can be reported and a leaked descriptor must be impossible). 2) Set *out_fd = -1 immediately so a caller that ignores the return value can never use an indeterminate descriptor. 3) If listener_fd < 0 return -1. 4) fd = accept(listener_fd, NULL, NULL); if fd < 0: EINTR -> retry step 4 (a signal must not abort an accept that may have completed); EAGAIN or EWOULDBLOCK -> return 0 (an earlier accept in the same batch already consumed the readiness notification); any other errno -> return -1 with errno preserved and the listener untouched. 5) Establish the mode explicitly instead of relying on inheritance: r1 = fcntl(fd, F_SETFL, O_NONBLOCK); r2 = fcntl(fd, F_SETFD, FD_CLOEXEC). 6) If r1 < 0 or r2 < 0, the peer descriptor exists but would be handed to the reactor in the wrong mode; close(fd) once, set errno to the failed fcntl's errno, return -1. Never return a blocking descriptor: net_read_some/net_write_some calls in the readiness loop must be guaranteed not to block. 7) *out_fd = fd; return 1. The listener is never closed, never re-armed and never modified, so an EMFILE burst leaves it registered for the next batch (scope R11: a per-connection failure never stops the broker).
  OUTPUT:
1 with *out_fd set to an owned descriptor that is nonblocking and close-on-exec regardless of the listener's own mode. 0 with *out_fd == -1 when no connection is pending (EAGAIN/EWOULDBLOCK) - not an error. -1 with *out_fd == -1 and errno set for a NULL out_fd, a negative listener_fd, any other accept failure, or a failed fcntl (in which case the half-configured descriptor has already been closed by this call).
  INVARIANTS_USED:
    - the accepted descriptor's mode is established explicitly by fcntl and never inherited from the listener
    - a would-block result is not an error and does not close the listener
    - EMFILE and friends are per-accept failures that must not stop the accept loop
    - each accepted descriptor is owned by exactly one session afterwards
    - a single nonblocking read or write call always returns promptly, so an unread peer cannot stall unrelated peers
  PRECONDITION:
listener_fd is an open listening socket registered in the event loop; out_fd is NULL or writable.
  POSTCONDITION:
On 1 ownership of one open, nonblocking, close-on-exec peer descriptor moved to the caller through *out_fd. On 0 and -1 no descriptor was leaked: either none was created or the half-configured one was closed.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Safe with a single accepting thread; two concurrent accepters would split the queued connections.
