[PROMPT]
Allocate a per-connection object, adopt an accepted descriptor and initialise it in AWAITING_CONNECT so it can receive its first CONNECT.

[RELY]
STRUCT:
  - NAME:
struct session
    ROLE:
object being allocated and initialised
  - NAME:
struct byte_buf
    ROLE:
input and output buffers initialised by buffer_init
FUNC:
  - NAME:
calloc
    KIND:
TYPE_REF
    ROLE:
allocate a zeroed session object
  - NAME:
buffer_init
    KIND:
CALL
    ROLE:
put the receive and output buffers in the empty state
  - NAME:
net_close_fd
    KIND:
CALL
    ROLE:
only used by the caller on failure; session_create never closes fd
VAR:
  - NAME:
SESSION_AWAITING_CONNECT
    ROLE:
initial lifecycle state

[GUARANTEE]
RAW:
int session_create(int fd, struct session **out_session)
NAME:
session_create
RETURN:
int
PARAMS:
  - TYPE:
int
    NAME:
fd
    NULLABLE:
false
    OWNERSHIP:
BORROWED
  - TYPE:
struct session **
    NAME:
out_session
    NULLABLE:
true
    OWNERSHIP:
OWNED_BY_CALLER

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
fd: an accepted descriptor that is already O_NONBLOCK|FD_CLOEXEC and is owned by the caller until this function succeeds. out_session: output slot for the new session pointer.
  ACTION:
1) If out_session is NULL return -1 (nothing can be reported). 2) Set *out_session = NULL so a caller that ignores the return value cannot use an indeterminate pointer. 3) If fd < 0 return -1; the descriptor is invalid, and the function must not attempt to close it. 4) s = calloc(1, sizeof *s); if s is NULL return -1 (allocation failure; fd is untouched and stays the caller's responsibility, so the caller closes it through net_close_fd). 5) s->fd = fd (ownership moves to the session from this point). 6) s->state = SESSION_AWAITING_CONNECT. 7) buffer_init(&s->input) and buffer_init(&s->output) so both buffers have no storage yet; storage is allocated lazily by buffer_reserve when the first read or the first queued response needs it. calloc already zeroed every pointer and length, so client_id is NULL, client_id_len is 0, subs is NULL, sub_count is 0 and sub_cap is 0; the explicit buffer_init calls document that contract instead of relying on calloc's byte pattern. 8) *out_session = s and return 0. The descriptor is closed exactly once later, by session_close_immediately(), session_destroy() or the flush-completion path, all of which route through net_close_fd(&s->fd).
  OUTPUT:
0 with *out_session set to a session that owns fd and is ready to buffer input. -1 with *out_session == NULL when out_session is NULL, fd < 0, or the allocation failed; on every -1 the descriptor is still open and still owned by the caller.
  INVARIANTS_USED:
    - a session owns exactly one descriptor and starts in AWAITING_CONNECT
    - every owned pointer starts as NULL, so partial construction cannot leak storage and session_destroy can be called on any successfully created session
    - ownership of fd transfers only on success
  PRECONDITION:
out_session is NULL or a writable pointer slot; fd is a nonblocking, close-on-exec descriptor owned by the caller.
  POSTCONDITION:
on success the caller owns a session that owns fd; on failure nothing is allocated and fd is unchanged.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Safe for distinct out_session slots; calloc'ed state is not shared until it is published.
