[PROMPT]
Run the level-triggered readiness loop: one bounded epoll_wait batch, dispatch listener and session events under their readiness preconditions, reap finished sessions, re-synchronize every session's interest mask, and return when the stop flag or loop_stop() requests it or a fatal reactor error occurs.

[RELY]
STRUCT:
  - NAME:
struct loop
    ROLE:
reactor state: epfd, borrowed broker, listener_fd, stop_flag, stopped
  - NAME:
struct epoll_event
    ROLE:
batch of readiness records; data.ptr NULL means the listener, otherwise the session that was registered
  - NAME:
struct session
    ROLE:
event target dispatched through dispatch_event()
  - NAME:
struct broker
    ROLE:
registry iterated by is_registered()/mask_sync() and compacted by broker_reap()
FUNC:
  - NAME:
epoll_wait
    KIND:
CALL
    ROLE:
collect up to LOOP_MAX_EVENTS ready descriptors with the LOOP_WAIT_MS timeout
  - NAME:
is_registered
    KIND:
CALL
    ROLE:
same-file: skip events whose session was already removed from the registry in this batch
  - NAME:
accept_ready
    KIND:
CALL
    ROLE:
same-file: service the listener event (data.ptr == NULL)
  - NAME:
dispatch_event
    KIND:
CALL
    ROLE:
same-file: apply the read/write readiness gates for one session event
  - NAME:
mask_sync
    KIND:
CALL
    ROLE:
same-file: recompute and apply interest masks after each batch
  - NAME:
broker_reap
    KIND:
CALL
    ROLE:
close drained CLOSING sessions and destroy CLOSED sessions after the batch
VAR:
  - NAME:
LOOP_MAX_EVENTS
    ROLE:
size of the on-stack event array, bounding work per batch
  - NAME:
LOOP_WAIT_MS
    ROLE:
epoll_wait timeout so the stop flag and the reap/mask pass run even with no traffic
  - NAME:
struct loop
    ROLE:
the reactor is borrowed; loop_run never closes it or the broker

[GUARANTEE]
RAW:
int loop_run(struct loop *l)
NAME:
loop_run
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

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
l: reactor from loop_create() with the listener registered by loop_add_listener() (l->broker is never NULL for a reactor created that way).
  ACTION:
1. If l == NULL or l->epfd < 0 return -1 (nothing can be waited on and no cleanup is performed here). 2. Declare struct epoll_event events[LOOP_MAX_EVENTS] on the stack. 3. Loop: (a) if l->stopped != 0 or (l->stop_flag != NULL && *l->stop_flag != 0) break out and return 0. (b) n = epoll_wait(l->epfd, events, LOOP_MAX_EVENTS, LOOP_WAIT_MS); if n < 0 { if errno == EINTR continue (an uninterruptible-by-SA_RESTART signal must not kill the broker); otherwise return -1 as a fatal reactor error }. if n == 0 continue (timeout: the stop flag is re-checked and the reap/mask pass still runs). (c) For i = 0 .. n-1: ptr = events[i].data.ptr; if ptr == NULL call accept_ready(l) (a listener registration); else if is_registered(l, (struct session *)ptr) call dispatch_event(l, (struct session *)ptr, events[i].events); an event for a session that was destroyed earlier in the same batch is dropped without dereferencing it. (d) broker_reap(l->broker) so sessions finished in this batch release their descriptors and storage immediately. (e) mask_sync(l) so every remaining session's EPOLLIN/EPOLLOUT interest matches its new state and queued output (this is what lets queued output reach a recipient that sends nothing further). 4. Repeat until break, then return 0.
  OUTPUT:
0 after a clean stop: the caller (main) then destroys the loop, closes the listener and destroys the broker. -1 when l is NULL, no epoll descriptor exists, or epoll_wait failed for a reason other than EINTR; sessions and queued output are not flushed by this function on that path, the caller's teardown releases them.
  INVARIANTS_USED:
    - descriptors are nonblocking, so every dispatched pump is bounded and cannot block the reactor
    - a listener event is always tagged with data.ptr == NULL and a session event always with a borrowed session pointer
    - interest masks are recomputed after every batch, so a session with queued output is registered for EPOLLOUT even if its peer never sends anything
    - broker_reap() invalidates previously borrowed session pointers, so registry lookups happen through is_registered()/mask_sync() rather than cached pointers
    - signal handlers only store into the sig_atomic_t flag; epoll_wait returns EINTR and the loop continues, so cleanup stays in ordinary code
  PRECONDITION:
l is a live reactor whose epoll descriptor is open and whose broker is valid; the listener and all session descriptors are nonblocking.
  POSTCONDITION:
loop_run has returned and no descriptor or buffer was leaked: all session teardown is complete except for CLOSING sessions whose queued output was still non-empty, which remain registered for EPOLLOUT so the caller may flush before exit.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Single-threaded reactor; the only asynchronous writer is a signal handler writing the published sig_atomic_t flag.
