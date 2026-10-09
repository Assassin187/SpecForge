[PROMPT]
Report how many subscriptions a session stores, so iteration bounds and SUBACK result counts are derived from actual stored state.

[RELY]
STRUCT:
  - NAME:
struct session
    ROLE:
source of the subscription count
FUNC:

VAR:


[GUARANTEE]
RAW:
size_t session_subscription_count(const struct session *s)
NAME:
session_subscription_count
RETURN:
size_t
PARAMS:
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
s: borrowed session pointer, possibly NULL.
  ACTION:
1) If s is NULL return 0. 2) Return s->sub_count. The count reflects stored entries only: a fresh session reports 0, an identical filter replaced in place does not increase the count, and a CLOSED session reports the same count as before closing because dropping a connection never transfers its subscriptions to another session.
  OUTPUT:
Number of stored subscriptions, 0 for a NULL session or a session with none.
  INVARIANTS_USED:
    - subscriptions are stored with no clean-session persistence across new connections
    - sub_count counts stored entries and never exceeds sub_cap
    - iteration uses count as the exclusive upper bound for session_subscription_at
  PRECONDITION:
s is NULL or points to a live session.
  POSTCONDITION:
the session is unchanged.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Not thread safe with respect to session_add_subscription() on the same session.
