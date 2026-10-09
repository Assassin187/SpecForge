[PROMPT]
Drain a bounded burst of pending connections from the listener, handing each accepted descriptor to the broker, and treat every accept or adoption failure as a per-connection event that leaves the listener registered and the reactor running.

[RELY]
STRUCT:
  - NAME:
struct loop
    ROLE:
owns the epfd and borrows the listener descriptor and the broker
FUNC:
  - NAME:
net_accept
    KIND:
CALL
    ROLE:
take one pending peer, already nonblocking and close-on-exec
  - NAME:
broker_accept
    KIND:
CALL
    ROLE:
create and register the session that owns the descriptor
  - NAME:
net_close_fd
    KIND:
CALL
    ROLE:
close the descriptor when the broker refused it
VAR:
  - NAME:
LOOP_ACCEPT_BURST
    ROLE:
maximum number of peers adopted in one listener event (bounded work)
  - NAME:
LOOP_WAIT_MS
    ROLE:
epoll_wait timeout that also bounds the retry rate of a listener that repeatedly fails to accept

[GUARANTEE]
RAW:
static void accept_ready(struct loop *l)
NAME:
accept_ready
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
l: the reactor whose listener_fd became readable (NULL, a NULL broker or a negative listener_fd is tolerated). No other input; the pending connections live in the kernel listen queue.
  ACTION:
1. If l == NULL, l->broker == NULL or l->listener_fd < 0, return. 2. fd = -1. 3. For n = 0 to LOOP_ACCEPT_BURST-1: rc = net_accept(l->listener_fd, &fd). 4. If rc == 0, the queue is empty (or an earlier accept consumed the readiness notification): leave the loop; the listener stays registered so the next readable event resumes accepting. 5. If rc < 0, an accept error occurred (EMFILE/ENFILE/ENOBUFS/ECONNABORTED/...): write a one-line diagnostic naming errno to stderr and leave the loop WITHOUT closing or re-registering the listener, so the broker keeps serving established clients and a later batch retries; the epoll_wait timeout LOOP_WAIT_MS bounds how often a persistently failing listener can be retried, so no sleep is issued. 6. If rc == 1, fd is a freshly accepted nonblocking, close-on-exec descriptor: call broker_accept(l->broker, fd). 7. If broker_accept returns 0, ownership of fd moved to the new session; the new session is AWAITING_CONNECT with an empty output queue, so the mask_sync that follows this dispatch registers it for EPOLLIN (EPOLL_CTL_ADD, because the fd has no registration yet). 8. If broker_accept returns -1 (allocation failure), the descriptor is still ours: call net_close_fd(&fd) so only this connection is dropped, and CONTINUE with the next pending connection instead of aborting the burst, because a single failed allocation must not starve other clients. 9. When the burst limit is reached, leave the loop; the listener is level-triggered, so any connection still queued produces a new EPOLLIN event in the next batch and the bounded work per event keeps one busy listener from delaying other descriptors.
  OUTPUT:
void. Post-state: up to LOOP_ACCEPT_BURST new sessions exist in the broker (each owning its descriptor) or were refused without leaking a descriptor, the listener descriptor is unchanged and still registered, and the accept loop never propagated a fatal error.
  INVARIANTS_USED:
    - every accepted descriptor is nonblocking and close-on-exec before it enters the reactor
    - a failed accept or a failed session creation closes at most the connection involved; the listener and all other sessions are unaffected
    - work per listener event is bounded, so a flood of connections cannot stall unrelated peers
    - a new session has no interest registration yet; the mask synchronization after the dispatch creates it
    - ownership of an accepted descriptor sits with whoever holds it: the session on success, this function on failure
  PRECONDITION:
l is a live loop whose listener_fd is an open, registered listening socket.
  POSTCONDITION:
no descriptor is leaked and no error escapes; the reactor continues regardless of accept failures.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Single-threaded reactor; only one accepting thread exists.
