[PROMPT]
Apply the readiness preconditions that decide, for one already-validated session, whether a read pump runs, whether a write pump runs, or both, so that writable readiness never permits a blocking read and queued output always gets written.

[RELY]
STRUCT:
  - NAME:
struct loop
    ROLE:
reactor that owns the pumps
  - NAME:
struct session
    ROLE:
state and queue inspected to choose the operations
  - NAME:
struct byte_buf
    ROLE:
queue whose length decides whether a write is permitted
FUNC:
  - NAME:
is_registered
    KIND:
CALL
    ROLE:
reject an event whose session was already reaped
  - NAME:
session_state
    KIND:
CALL
    ROLE:
read the lifecycle state before and after the read pump
  - NAME:
session_output
    KIND:
CALL
    ROLE:
check that queued output exists before writing
  - NAME:
pump_read
    KIND:
CALL
    ROLE:
consume readable input under the read preconditions
  - NAME:
pump_write
    KIND:
CALL
    ROLE:
drain queued output under the write preconditions
VAR:
  - NAME:
EPOLLIN
    ROLE:
readable readiness: permits the read pump
  - NAME:
EPOLLHUP
    ROLE:
peer hang-up: permits a read attempt that observes the close and the final frames
  - NAME:
EPOLLERR
    ROLE:
descriptor error: permits a read or write attempt that fails fast
  - NAME:
EPOLLRDHUP
    ROLE:
peer half close when the constant is available; treated like EPOLLIN for reading
  - NAME:
EPOLLOUT
    ROLE:
writable readiness: permits the write pump only when output is queued
  - NAME:
SESSION_AWAITING_CONNECT
    ROLE:
state in which input is still accepted (CONNECT not yet seen)
  - NAME:
SESSION_READY
    ROLE:
state in which input and queued output are both active
  - NAME:
SESSION_CLOSING
    ROLE:
state in which output may still be flushed but no further input is read
  - NAME:
SESSION_CLOSED
    ROLE:
terminal state: no read and no write is permitted

[GUARANTEE]
RAW:
static void dispatch_event(struct loop *l, struct session *s, uint32_t events)
NAME:
dispatch_event
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
  - TYPE:
uint32_t
    NAME:
events
    NULLABLE:
false
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
EVENT
EVENT:
  TRIGGER:
epoll_wait reports one or more events for a descriptor whose data.ptr is a non-NULL session pointer (listener events carry data.ptr == NULL and are handled by the accept path instead).
  PRECONDITION:
l and s are non-NULL, s is still one of the broker's registry entries (checked with is_registered), the descriptor is nonblocking and close-on-exec, and it is registered level-triggered with an interest mask computed by mask_sync.
  INPUT:
s and the raw epoll event mask in events. The mask is only a hint; the pumps and the protocol core decide what actually happens, and every syscall made is nonblocking.
  ACTION:
1. If l == NULL or s == NULL, return. 2. If is_registered(l, s) == 0, return without touching s: the session was destroyed by an earlier reap in this loop iteration, so its pointer must not be dereferenced. 3. read_mask = EPOLLIN | EPOLLHUP | EPOLLERR, plus EPOLLRDHUP guarded by #ifdef EPOLLRDHUP (so no feature-test macro is required to compile this file). 4. state = session_state(s). 5. READ GATE: if (state == SESSION_AWAITING_CONNECT || state == SESSION_READY) and (events & read_mask) != 0, call pump_read(l, s). A pure EPOLLOUT event never satisfies this gate, so writable readiness can never trigger a read; a pure hup/err event does, which is how a FIN that arrives together with the last data (or a hard reset) is observed. 6. Re-read state = session_state(s) after the read pump, because the input may have closed the session (malformed packet, non-CONNECT before CONNECT, EOF, read error, unbuffered input). 7. WRITE GATE: if state == SESSION_READY or state == SESSION_CLOSING, then out = session_output(s) and, ONLY if out != NULL and out->len > 0, call pump_write(l, s) when (events & EPOLLOUT) != 0 OR when the event carries EPOLLHUP/EPOLLERR (an error-driven drain attempt that either makes progress or fails immediately inside pump_write and closes the session, so a CLOSING session can never be stranded with undeliverable bytes). If out->len == 0 the write pump is not called at all, so EPOLLOUT on an idle queue costs nothing. 8. state == SESSION_CLOSED forbids both pumps: the descriptor is already closed and the queue released. 9. Nothing is reaped here; loop_run calls broker_reap and then mask_sync after the whole event batch, which is what turns a drained CLOSING session into a removed one and what registers a newly created session for EPOLLIN.
  STATE_CHANGE:
s may stay AWAITING_CONNECT/READY, move READY -> CLOSING (orderly close or read error after the buffered complete frames were processed, queued output preserved), or move to CLOSED (its input was rejected or its descriptor failed; the queue storage is released). A CLOSING session may have its queue shortened or emptied by the write gate. No other session is affected.
  RESPONSE:
No return value. Observable effects: bytes read from the kernel and handed to the protocol core, complete responses queued, queued bytes written to the peer, and possibly this one session closed. The reactor then reaps finished sessions and recomputes interest masks for all sessions.
  EVENT_TYPE:
level-triggered epoll readiness event for one session descriptor
  INVARIANTS_USED:
    - EPOLLOUT alone never permits a read; reads happen only under EPOLLIN/EPOLLRDHUP/EPOLLHUP/EPOLLERR
    - a write is attempted only when the session actually has queued output
    - every descriptor is nonblocking, so any pump call returns promptly and cannot stall unrelated peers
    - an event for an already reaped session is ignored instead of dereferenced
    - no session is destroyed inside a dispatched handler, so a session pointer stays valid for the remainder of the batch
    - queued output is delivered even when the recipient sends nothing further, because EPOLLOUT remains registered while len > 0
  POSTCONDITION:
Every readable byte that the kernel had ready has been read (up to the burst bound), every queued byte that the socket accepted has been consumed from the queue, and no session was read or written in a state that forbids it.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Single-threaded reactor.
