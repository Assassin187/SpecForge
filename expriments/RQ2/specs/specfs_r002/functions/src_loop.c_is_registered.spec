[PROMPT]
Answer whether a session pointer delivered by an epoll event is still a live registry entry, so an event for a session that was already reaped can never be dispatched into freed memory.

[RELY]
STRUCT:
  - NAME:
struct loop
    ROLE:
provides the borrowed broker whose registry is searched
  - NAME:
struct broker
    ROLE:
owns the session registry that is the source of truth
  - NAME:
struct session
    ROLE:
pointer compared by identity, never dereferenced
FUNC:
  - NAME:
broker_session_count
    KIND:
CALL
    ROLE:
size of the live registry scan
  - NAME:
broker_session_at
    KIND:
CALL
    ROLE:
borrow each registry entry to compare pointers
VAR:


[GUARANTEE]
RAW:
static int is_registered(const struct loop *l, const struct session *s)
NAME:
is_registered
RETURN:
int
PARAMS:
  - TYPE:
const struct loop *
    NAME:
l
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
const struct session *
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
l: the loop whose broker registry is the authority (NULL tolerated). s: the session pointer carried by the epoll event data (NULL tolerated).
  ACTION:
1. If l == NULL or s == NULL, return 0 (never trust an event without a pointer). 2. b = l->broker; if b == NULL return 0. 3. n = broker_session_count(b); loop i from 0 to n-1: if broker_session_at(b, i) == s, return 1. 4. Fall through and return 0: the pointer is not an entry of the current registry, which means the session was already destroyed by broker_reap (or by broker_destroy on the teardown path), so its memory may be reused and the event must be ignored.
  OUTPUT:
1 when s is currently one of the broker's registry entries, 0 otherwise (including either argument being NULL).
  INVARIANTS_USED:
    - the broker registry is the single source of truth for live sessions
    - a session pointer is invalidated by broker_reap, so a pointer delivered with an event must be revalidated before use
    - reaping never happens inside a dispatched handler, so this guard is the only protection needed against a stale pointer from an earlier batch of the same epoll_wait call
    - the function never dereferences s, so a stale pointer cannot fault
  PRECONDITION:
l was created by loop_create and borrows a live broker, or is NULL.
  POSTCONDITION:
no state is modified; the registry and every session are untouched.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Single-threaded reactor.
