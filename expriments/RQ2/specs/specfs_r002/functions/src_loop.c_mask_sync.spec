[PROMPT]
For every live session recompute the interest mask from its state and queued output, register a session that has no registration yet, and drop the registration of a completed one, so that queued output always reaches an idle recipient and input is read exactly while the session may still accept it.

[RELY]
STRUCT:
  - NAME:
struct loop
    ROLE:
owns the epoll descriptor and borrows the broker registry
  - NAME:
struct broker
    ROLE:
session registry iterated in index order
  - NAME:
struct session
    ROLE:
state, descriptor and queue that determine the mask
  - NAME:
struct byte_buf
    ROLE:
queue length that turns EPOLLOUT on and off
  - NAME:
struct epoll_event
    ROLE:
registration record carrying the interest mask and the session pointer
FUNC:
  - NAME:
broker_session_count
    KIND:
CALL
    ROLE:
scan bound for the registry
  - NAME:
broker_session_at
    KIND:
CALL
    ROLE:
borrow each session in turn
  - NAME:
session_state
    KIND:
CALL
    ROLE:
decide whether EPOLLIN applies
  - NAME:
session_fd
    KIND:
CALL
    ROLE:
descriptor to register
  - NAME:
session_output
    KIND:
CALL
    ROLE:
decide whether EPOLLOUT applies
  - NAME:
session_close_immediately
    KIND:
CALL
    ROLE:
close a session whose registration cannot be updated
  - NAME:
epoll_ctl
    KIND:
CALL
    ROLE:
apply EPOLL_CTL_MOD, falling back to EPOLL_CTL_ADD when the descriptor is not registered yet
VAR:
  - NAME:
EPOLL_CTL_ADD
    ROLE:
first registration of a descriptor
  - NAME:
EPOLL_CTL_MOD
    ROLE:
update of an already registered descriptor
  - NAME:
EPOLLIN
    ROLE:
interest set while the session may still receive protocol input
  - NAME:
EPOLLOUT
    ROLE:
interest set while queued output is non-empty
  - NAME:
ENOENT
    ROLE:
epoll_ctl(MOD) reports it when the descriptor has no registration yet
  - NAME:
SESSION_AWAITING_CONNECT
    ROLE:
read interest is set
  - NAME:
SESSION_READY
    ROLE:
read interest is set
  - NAME:
SESSION_CLOSING
    ROLE:
no read interest; write interest only while output remains
  - NAME:
SESSION_CLOSED
    ROLE:
no interest at all; the entry is about to be reaped

[GUARANTEE]
RAW:
static void mask_sync(struct loop *l)
NAME:
mask_sync
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
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
l: the reactor whose interest masks are refreshed (NULL, or a NULL broker, is tolerated). The registry is read through the broker API; nothing is passed in from a call site other than the loop itself.
  ACTION:
1. If l == NULL or l->broker == NULL or l->epfd < 0, return. 2. n = broker_session_count(l->broker); for i = 0 to n-1: s = broker_session_at(l->broker, i); if s == NULL, continue. 3. state = session_state(s); fd = session_fd(s). 4. If state == SESSION_CLOSED or fd < 0, continue: the descriptor is already closed, its registration disappeared with it, and broker_reap removes the entry in this same iteration. 5. interest = 0. If state == SESSION_AWAITING_CONNECT or state == SESSION_READY: interest |= EPOLLIN (both states may still deliver protocol input; a session that has not sent CONNECT yet must be able to do so). 6. out = session_output(s); if out != NULL and out->len > 0: interest |= EPOLLOUT (this is what delivers queued output to a recipient that sends nothing further). 7. If interest == 0 (a CLOSING session whose queue is drained), continue: it owes nothing more, and the reap that runs before the next wait closes it. 8. Build ev with ev.events = interest and ev.data.ptr = s, then rc = epoll_ctl(l->epfd, EPOLL_CTL_MOD, fd, &ev). 9. If rc == 0, the mask is applied; continue. 10. If errno == ENOENT, the descriptor has no registration yet (a session accepted in this iteration, or a descriptor number reused after an earlier close): rc = epoll_ctl(l->epfd, EPOLL_CTL_ADD, fd, &ev); if rc != 0, diagnose on stderr and call session_close_immediately(s) so the loop does not keep a descriptor it cannot observe. 11. If rc was -1 with any other errno (EBADF, ENOMEM, ...), diagnose and call session_close_immediately(s): a session that cannot be armed would silently stop working, so closing only that session is the isolated failure response. 12. Continue with the next index. Registrations are level-triggered; no EPOLLET, EPOLLONESHOT or edge machinery is used, so a descriptor whose bytes were not all consumed is reported again by the next epoll_wait.
  OUTPUT:
void. Post-state: every live session with readable input is registered with EPOLLIN, every session with non-empty queued output is registered with EPOLLOUT, no session in SESSION_CLOSED or with a released descriptor keeps a registration, and a session whose registration could not be applied was closed instead of being left unobserved.
  INVARIANTS_USED:
    - queued output must keep the descriptor registered for EPOLLOUT until len reaches 0, so an idle recipient still drains
    - EPOLLIN is armed exactly while the session may accept protocol input
    - closing a descriptor removes its epoll registration automatically, so a closed session never receives events
    - a failing registration is a per-connection failure, never a reactor failure
    - mod-then-add-on-ENOENT is the only registration state machine, so no per-session bookkeeping has to be kept in the loop
  PRECONDITION:
l is a live loop with an open epoll descriptor; the broker registry is stable for the duration of the call (no session is destroyed while iterating).
  POSTCONDITION:
the kernel interest masks agree with the broker state: EPOLLIN iff state is AWAITING_CONNECT/READY, EPOLLOUT iff queued output is non-empty; no descriptor is registered twice (adding twice would fail with EEXIST and close an innocent session).
  IDEMPOTENT:
true
  THREAD_SAFETY:
Single-threaded reactor; called between epoll_wait batches.
