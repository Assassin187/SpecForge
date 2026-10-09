[PROMPT]
Drain the session's queued output with a bounded burst of nonblocking writes, consuming exactly the bytes each call reports so partial writes preserve byte order, and close only this session when the descriptor fails.

[RELY]
STRUCT:
  - NAME:
struct loop
    ROLE:
provides the borrowed broker used for the post-write reap decision
  - NAME:
struct session
    ROLE:
owns the descriptor and the queued-output buffer
  - NAME:
struct byte_buf
    ROLE:
queue whose leading bytes are consumed as they are written
FUNC:
  - NAME:
session_fd
    KIND:
CALL
    ROLE:
descriptor to write to
  - NAME:
session_output
    KIND:
CALL
    ROLE:
borrow the queue that is drained
  - NAME:
net_write_some
    KIND:
CALL
    ROLE:
one bounded nonblocking write
  - NAME:
buffer_consume
    KIND:
CALL
    ROLE:
drop exactly the consumed prefix, keeping the unsent tail in order
  - NAME:
session_close_immediately
    KIND:
CALL
    ROLE:
close this session when the descriptor fails
VAR:
  - NAME:
LOOP_MAX_WRITE_CALLS
    ROLE:
upper bound on write calls per descriptor per dispatched event (64)
  - NAME:
NET_IO_PROGRESS
    ROLE:
a write transferred bytes; the out count says how many
  - NAME:
NET_IO_WOULD_BLOCK
    ROLE:
the socket buffer is full; the remaining bytes wait for the next EPOLLOUT
  - NAME:
NET_IO_ERROR
    ROLE:
the descriptor failed (for example EPIPE/ECONNRESET) and the queue cannot be delivered

[GUARANTEE]
RAW:
static void pump_write(struct loop *l, struct session *s)
NAME:
pump_write
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
  - TYPE:
struct session *
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
l: the reactor (NULL tolerated). s: the session whose descriptor reported EPOLLOUT (or a hup/err readiness) with queued output; the caller already verified that s is still a registry entry and that session_output(s)->len > 0.
  ACTION:
1. If l == NULL or s == NULL, return. 2. fd = session_fd(s); if fd < 0 return. 3. out = session_output(s); if out == NULL or out->len == 0 return (an idle session is never written to, so EPOLLOUT with an empty queue costs nothing). 4. For n = 0 to LOOP_MAX_WRITE_CALLS-1: if out->len == 0 break. 5. r = net_write_some(fd, out->data, out->len, &w). 6. If r == NET_IO_WOULD_BLOCK return: the kernel took what it could and the rest stays queued; the mask synchronization keeps EPOLLOUT registered while len > 0, so the remaining bytes are written in a later batch even if the recipient never sends anything further. 7. If r == NET_IO_ERROR call session_close_immediately(s) and return: the descriptor is broken, so the owed bytes can never be delivered and the session is finished at once (this is the path that also releases a CLOSING session whose peer vanished). 8. If r == NET_IO_PROGRESS: if w == 0 return (defensive: no progress must not become a spin loop); otherwise buffer_consume(out, w), which drops exactly the written prefix and keeps the unsent suffix at offset 0, so byte order is preserved across partial writes. 9. Continue while len > 0 and the burst bound is not reached. 10. Return. When the queue reaches len 0 the session owes nothing; if its state was SESSION_CLOSING the broker_reap call later in the same loop iteration closes and removes it, so the flush-completion path needs no special case here.
  OUTPUT:
void. Post-state: out->len has decreased by exactly the total number of bytes the kernel accepted; the bytes still queued are the unsent suffix in original order; the session is either unchanged in state (READY or CLOSING) with a possibly shortened queue, or CLOSED with its queue storage released after a write error. Byte accounting invariant: bytes_written_before + len_after + released_bytes = len_before.
  INVARIANTS_USED:
    - a single nonblocking write call is bounded by len and never blocks the reactor
    - partial writes are tracked by consuming the written count, never by clearing the length (clearing a length would not release storage and would lose data)
    - queued output is delivered to an idle recipient: EPOLLOUT stays registered while len > 0, so a peer that sends nothing after its SUBSCRIBE still receives the routed PUBLISH
    - a failed descriptor closes only its own session
    - the queue is empty exactly when the peer has been given every byte the broker queued
  PRECONDITION:
s is a live registry entry whose descriptor is nonblocking and close-on-exec, whose state is SESSION_READY or SESSION_CLOSING, and whose queued output is non-empty; the dispatched event contained EPOLLOUT or a hup/err condition.
  POSTCONDITION:
no byte is written twice, no byte is silently discarded while the descriptor is healthy, and a drained CLOSING session is reaped in the same iteration.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Single-threaded reactor.
