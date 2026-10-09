[PROMPT]
Expose one stored subscription through a read-only public view so the core can iterate filters without seeing the private slot layout.

[RELY]
STRUCT:
  - NAME:
struct session
    ROLE:
owner of the subscription array
  - NAME:
struct session_sub_entry
    ROLE:
private slot: a public view plus the owned filter copy
  - NAME:
struct session_sub
    ROLE:
returned read-only projection
FUNC:

VAR:


[GUARANTEE]
RAW:
const struct session_sub *session_subscription_at(const struct session *s, size_t index)
NAME:
session_subscription_at
RETURN:
const struct session_sub *
PARAMS:
  - TYPE:
const struct session *
    NAME:
s
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
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
s: borrowed session pointer, possibly NULL. index: zero-based slot index, normally below session_subscription_count(s).
  ACTION:
1) If s is NULL return NULL. 2) If s->subs is NULL or index >= s->sub_count return NULL (no out-of-range read and no negative index because index is unsigned). 3) Return &s->subs[index].view, where the private slot stores the public struct session_sub directly so the returned type is exact and no cast or aliasing is needed. The view's filter pointer aliases the session-owned copy of the filter bytes, which is not NUL terminated; filter_len gives the length. The returned pointer is borrowed and stays valid until the next session_add_subscription(), session_close_immediately() or session_destroy() on the same session; the caller must not free it or write through it.
  OUTPUT:
Borrowed const pointer to the read-only view of the indexed subscription; NULL when s is NULL or index is out of range.
  INVARIANTS_USED:
    - 0 <= sub_count <= sub_cap, and slots below sub_count always hold a valid owned filter
    - filter bytes are length-delimited, never a C string
    - returning a borrowed view never transfers ownership
  PRECONDITION:
s is NULL or points to a live session.
  POSTCONDITION:
the session and its storage are unchanged.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Not thread safe with respect to session_add_subscription() on the same session.
