[PROMPT]
Report the lifecycle state so the execution layer can decide which readiness events permit reads or writes and when a session may be reaped.

[RELY]
STRUCT:
  - NAME:
struct session
    ROLE:
source of the lifecycle state field
FUNC:

VAR:
  - NAME:
SESSION_AWAITING_CONNECT
    ROLE:
returned while no accepted CONNECT has been processed
  - NAME:
SESSION_READY
    ROLE:
returned once CONNACK has been queued
  - NAME:
SESSION_CLOSING
    ROLE:
returned while queued output must still flush
  - NAME:
SESSION_CLOSED
    ROLE:
terminal state, also returned for a NULL session

[GUARANTEE]
RAW:
enum session_state session_state(const struct session *s)
NAME:
session_state
RETURN:
enum session_state
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
1) If s is NULL return SESSION_CLOSED (the terminal value), because a NULL session has no usable descriptor and must be treated as reapable/inactive by callers rather than as a fresh connection. 2) Return s->state verbatim.
  OUTPUT:
One of SESSION_AWAITING_CONNECT, SESSION_READY, SESSION_CLOSING or SESSION_CLOSED. SESSION_CLOSED for a NULL session.
  INVARIANTS_USED:
    - SESSION_CLOSED is terminal and implies the descriptor is already closed
    - state transitions only move forward along the documented state machine
    - reading state must not change the state machine
  PRECONDITION:
s is NULL or points to a live session.
  POSTCONDITION:
the session is unchanged.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Not thread safe with respect to concurrent state transitions on s.
