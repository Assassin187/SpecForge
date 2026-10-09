[PROMPT]
Hand out a borrowed pointer to the session stored at a registry index so the execution layer can read its descriptor, state and queued output.

[RELY]
STRUCT:
  - NAME:
struct broker
    ROLE:
registry whose slot array is read; the pointer is treated as read-only
  - NAME:
struct session
    ROLE:
borrowed session returned to the caller
FUNC:

VAR:


[GUARANTEE]
RAW:
struct session *broker_session_at(const struct broker *b, size_t index)
NAME:
broker_session_at
RETURN:
struct session *
PARAMS:
  - TYPE:
const struct broker *
    NAME:
b
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
size_t
    NAME:
index
    NULLABLE:
false
    OWNERSHIP:
UNKNOWN

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
b: registry to inspect, or NULL. index: slot number in the range reported by broker_session_count.
  ACTION:
If b is NULL or index >= b->count return NULL; otherwise return b->sessions[index] (which may be NULL only for a slot left empty by a removal inside the same count, a state the current callers never observe because removal is always paired with a count decrement). The function never dereferences the returned session and never transfers ownership.
  OUTPUT:
A borrowed struct session pointer valid until the next broker_reap, broker_accept or broker_destroy call (those may reallocate or compact the slot array), or NULL when the index is out of range.
  INVARIANTS_USED:
    - the broker owns every session in the array; callers only borrow
    - compaction during broker_reap and growth during broker_accept invalidate previously returned slot pointers and the array base
    - iterating i = 0 .. broker_session_count(b) - 1 with this accessor visits each registered session exactly once
  PRECONDITION:
b is NULL or a registry from broker_create; index is a slot number the caller obtained from a count taken without an intervening mutating broker call.
  POSTCONDITION:
the registry is unchanged and the returned pointer (when non-NULL) is still owned by the broker.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Read-only; the caller must not keep the result across a reap, accept or destroy in the single-threaded reactor.
