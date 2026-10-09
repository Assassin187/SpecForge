[PROMPT]
Report how many sessions the registry currently holds so the execution layer can iterate it by index.

[RELY]
STRUCT:
  - NAME:
struct broker
    ROLE:
registry whose count field is read; the pointer is treated as read-only
FUNC:

VAR:


[GUARANTEE]
RAW:
size_t broker_session_count(const struct broker *b)
NAME:
broker_session_count
RETURN:
size_t
PARAMS:
  - TYPE:
const struct broker *
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
b: registry to inspect, or NULL.
  ACTION:
Return b == NULL ? 0 : b->count. No state is modified and no session is dereferenced, so the count stays valid across a sequence of read-only broker_session_at calls until the next mutating registry operation.
  OUTPUT:
The number of live slots in the registry (including sessions that are CLOSING or CLOSED but not yet reaped) or 0 for a NULL registry.
  INVARIANTS_USED:
    - count is the number of owned session pointers stored in the array, and every index below it is either a live session or a session awaiting reaping
    - the count is only reduced by remove_session_at during broker_reap
  PRECONDITION:
b is NULL or a registry from broker_create.
  POSTCONDITION:
the registry is unchanged.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Read-only; safe to call between dispatch steps in the single-threaded reactor.
