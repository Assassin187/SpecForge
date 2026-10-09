[PROMPT]
Release every owned filter copy and the slot array, leaving the session with an empty subscription list so a torn-down connection transfers nothing to later connections.

[RELY]
STRUCT:
  - NAME:
struct session
    ROLE:
owner of the subscription array
  - NAME:
struct session_sub_entry
    ROLE:
slot holding the owned filter copy
FUNC:
  - NAME:
free
    KIND:
TYPE_REF
    ROLE:
release each owned filter copy and the slot array
VAR:


[GUARANTEE]
RAW:
static void free_subscriptions(struct session *s)
NAME:
free_subscriptions
RETURN:
void
PARAMS:
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
s: borrowed session pointer, possibly NULL.
  ACTION:
1) If s is NULL return. 2) For i = 0 .. s->sub_count - 1: free(s->subs[i].owned) (the owned copy of that filter's bytes; the public view field s->subs[i].view.filter aliased the same storage and is dead from here on). 3) free(s->subs) — the slot array itself. 4) Set s->subs = NULL, s->sub_count = 0 and s->sub_cap = 0 together, so the session is in the documented empty state and a second call frees nothing (the loop body runs zero times and free(NULL) is a no-op). 5) Return. Clearing the count without releasing the array would leak the array itself, so all three fields are updated here.
  OUTPUT:
No return value. Every filter copy previously owned by the session and the slot array are released; sub_count == sub_cap == 0 and subs == NULL.
  INVARIANTS_USED:
    - each stored filter has exactly one owned copy, created by session_add_subscription
    - slots below sub_count always hold a valid owned pointer, so the loop bound frees every live copy
    - an empty list is represented by subs == NULL with sub_count == sub_cap == 0
  PRECONDITION:
s is NULL or points to a live session.
  POSTCONDITION:
the session stores no subscription and owns no subscription storage; other session fields are untouched.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Not thread safe; called from single-threaded teardown paths.
