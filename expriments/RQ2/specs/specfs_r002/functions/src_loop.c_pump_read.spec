[PROMPT]
Run a bounded burst of nonblocking reads into the session input buffer and hand the accumulated bytes to the protocol core after every successful read, turning would-block into a quiet return, dispatch EOF/error into the broker's EOF path, and closing only this session when its input cannot be buffered.

[RELY]
STRUCT:
  - NAME:
struct loop
    ROLE:
provides the borrowed broker that frames and answers packets
  - NAME:
struct session
    ROLE:
owns the descriptor and the receive buffer that accumulates this burst
  - NAME:
struct byte_buf
    ROLE:
growable receive buffer appended to by this function
FUNC:
  - NAME:
session_fd
    KIND:
CALL
    ROLE:
descriptor to read from
  - NAME:
session_input
    KIND:
CALL
    ROLE:
borrow the receive buffer
  - NAME:
net_read_some
    KIND:
CALL
    ROLE:
one bounded nonblocking read
  - NAME:
buffer_append
    KIND:
CALL
    ROLE:
append the bytes just read to the receive buffer
  - NAME:
broker_process_input
    KIND:
CALL
    ROLE:
frame, dispatch and consume every complete packet now buffered
  - NAME:
broker_session_eof
    KIND:
CALL
    ROLE:
handle orderly close / read error: process complete frames, then finish after flushing
  - NAME:
session_close_immediately
    KIND:
CALL
    ROLE:
close this session when its input cannot be buffered
VAR:
  - NAME:
LOOP_READ_CHUNK
    ROLE:
size of the stack staging chunk used for one read call (4096 bytes)
  - NAME:
LOOP_MAX_READ_CALLS
    ROLE:
upper bound on read calls per descriptor per dispatched event (64)
  - NAME:
NET_IO_PROGRESS
    ROLE:
a read transferred bytes; the out count says how many
  - NAME:
NET_IO_WOULD_BLOCK
    ROLE:
no more bytes are available right now
  - NAME:
NET_IO_CLOSED
    ROLE:
the peer performed an orderly close of its write side
  - NAME:
NET_IO_ERROR
    ROLE:
the descriptor failed

[GUARANTEE]
RAW:
static void pump_read(struct loop *l, struct session *s)
NAME:
pump_read
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
l: the reactor (NULL tolerated). s: the session whose descriptor reported readability; the caller has already checked that s is still a registry entry and that its state is AWAITING_CONNECT or READY (EPOLLOUT alone never reaches this function).
  ACTION:
1. If l == NULL or s == NULL or l->broker == NULL, return. 2. fd = session_fd(s); if fd < 0 return (the session already finished). 3. in = session_input(s); if in == NULL, session_close_immediately(s) and return. 4. For n = 0 to LOOP_MAX_READ_CALLS-1: r = net_read_some(fd, chunk, LOOP_READ_CHUNK, &got) where chunk is a LOOP_READ_CHUNK-byte stack array. 5. If r == NET_IO_WOULD_BLOCK, return: the burst is over and the level-triggered EPOLLIN event will fire again when more bytes arrive. 6. If r == NET_IO_CLOSED, call broker_session_eof(l->broker, s) and return: the peer shut down its write side, so any complete frames already buffered - including a PUBLISH that arrived immediately before the shutdown - are handled first and the session then finishes after flushing what it owes (scope R07/R08). 7. If r == NET_IO_ERROR, call broker_session_eof(l->broker, s) and return: a broken descriptor is treated the same way so buffered complete frames are still processed; the broken socket then fails the following write and the session is closed immediately by pump_write, so no session is stranded. 8. Otherwise r == NET_IO_PROGRESS and got > 0: if buffer_append(in, chunk, got) != 0, the bytes cannot be buffered (allocation failure); call session_close_immediately(s) and return, because failing to buffer input would corrupt framing if the bytes were dropped. 9. Call broker_process_input(l->broker, s); if it returns -1 the session closed itself (malformed or unframeable input, or a packet before CONNECT), so return without another read. 10. If got < LOOP_READ_CHUNK the kernel buffer was drained; return rather than issuing another read that would only return would-block. 11. Otherwise loop to the next read; the burst bound keeps a peer that streams without pause from monopolising the reactor, because the reactor returns after at most LOOP_MAX_READ_CALLS reads and serves every other ready descriptor, whose own responses are then written even though this peer is idle.
  OUTPUT:
void. Post-state: every byte read during the burst is either consumed as a complete packet by the protocol core or still buffered as a partial frame; the session is either still AWAITING_CONNECT/READY, CLOSING after an orderly close or a read error (with complete frames already handled), or CLOSED (its input could not be buffered, or its own input was rejected). Bytes consumed and bytes buffered always sum to the bytes read in this call, so no input is lost or duplicated.
  INVARIANTS_USED:
    - a session's own packets are framed with explicit Remaining Length counts, so partially received frames are preserved in the receive buffer for the next batch
    - bytes are consumed only for packets that were fully handled
    - the drain-after-close order is: process complete frames, then discard only the partial tail
    - read work per event is bounded so one peer cannot stall unrelated peers
    - a descriptor that is nonblocking is read only after a readable/hup/err readiness event
    - every failure path closes at most the session whose descriptor failed
  PRECONDITION:
s is a live registry entry in SESSION_AWAITING_CONNECT or SESSION_READY whose descriptor is nonblocking, close-on-exec and registered for EPOLLIN, and the dispatched event contained EPOLLIN, EPOLLRDHUP, EPOLLHUP or EPOLLERR.
  POSTCONDITION:
no read is issued for a session that is CLOSING or CLOSED, no byte is discarded except a partial frame after EOF, and the reactor state is consistent with the broker registry.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Single-threaded reactor; the session is never touched concurrently.
