[PROMPT]
Finish and forget sessions: close a CLOSING session whose queued output is fully drained, then destroy and remove every CLOSED session, re-checking the new element at each index because removal shifts the tail down.

[RELY]
STRUCT:
  - NAME:
struct broker
    ROLE:
registry whose session array is compacted in place
  - NAME:
struct session
    ROLE:
each registry entry inspected for CLOSING/CLOSED state
  - NAME:
struct byte_buf
    ROLE:
queued-output buffer whose len decides whether a CLOSING session may be closed now
FUNC:
  - NAME:
session_state
    KIND:
CALL
    ROLE:
read the lifecycle state of the current entry
  - NAME:
session_output
    KIND:
CALL
    ROLE:
borrow the queue to test whether all queued output was flushed
  - NAME:
session_close_immediately
    KIND:
CALL
    ROLE:
close a drained CLOSING session once
  - NAME:
remove_session_at
    KIND:
CALL
    ROLE:
destroy the session and shift the tail down
VAR:
  - NAME:
SESSION_CLOSING
    ROLE:
state that means no more output will ever be queued for this session
  - NAME:
SESSION_CLOSED
    ROLE:
state whose entry is destroyed and removed from the registry

[GUARANTEE]
RAW:
void broker_reap(struct broker *b)
NAME:
broker_reap
RETURN:
void
PARAMS:
  - TYPE:
struct broker *
    NAME:
b
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
b: the broker registry to compact; NULL is tolerated. No other parameters. The registry array element b->sessions[i] may be NULL (it never is in normal operation, but removal must not read through a NULL entry).
  ACTION:
1. If b == NULL, return. 2. i = 0. 3. While i < b->count: s = b->sessions[i]; if s == NULL, i++ and continue. 4. state = session_state(s). 5. If state == SESSION_CLOSING: out = session_output(s); if out != NULL and out->len == 0, call session_close_immediately(s) (releases the descriptor, frees the drained queue, sets SESSION_CLOSED), then set state = SESSION_CLOSED and fall through; otherwise leave the session CLOSING with its bytes intact and go to step 8. 6. If state == SESSION_CLOSED: call remove_session_at(b, i) which destroys the session and shifts the tail down, and do NOT increment i, because the element that was at i+1 is now at i and must be inspected by the next iteration (this makes adjacent victims collapse in one pass). 7. Otherwise (AWAITING_CONNECT or READY): i++ to keep the session. 8. Loop back to step 3. 9. Return; the registry contains only live sessions and CLOSING sessions that still owe bytes.
  OUTPUT:
void. Post-state: every session that was CLOSED is destroyed and removed, every drained CLOSING session has been closed and removed in the same pass, every CLOSING session with queued bytes is kept in CLOSING with its queue unchanged, and AWAITING_CONNECT/READY sessions are untouched. b->count decreases by exactly the number of removed entries; brokers that own no sessions are unchanged. A NULL broker is an accepted no-op.
  INVARIANTS_USED:
    - a CLOSING session has discarded its receive buffer and will never queue new output, so an empty queue means nothing more can be owed to the peer
    - clearing a length does not release storage: closing a drained CLOSING session is what releases its queue storage
    - removal shifts the tail down, so the index must not be advanced after a removal
    - the registry is owned entirely by the broker; a caller only borrows session pointers, which reaping invalidates
    - closing one session never affects the state of any other session
  PRECONDITION:
b is a live registry created by broker_create, or NULL. Every pointer in b->sessions[0 .. b->count-1] is either NULL or a session owned by this broker.
  POSTCONDITION:
no CLOSED session remains in the registry; every removed session's descriptor, buffers, identifier copy and filter copies have been released exactly once; CLOSING sessions with queued output remain until their bytes are flushed and broker_reap runs again.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Single-threaded reactor; called between epoll_wait batches and from the teardown path, never concurrently.
