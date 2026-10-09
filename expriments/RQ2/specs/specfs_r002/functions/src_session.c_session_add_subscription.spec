[PROMPT]
Copy an accepted filter into session-owned storage, replacing an identical filter in place or appending a new slot, so routing matches stored state and the connection's subscriptions die with it.

[RELY]
STRUCT:
  - NAME:
struct session
    ROLE:
owner of the subscription array and its growth
  - NAME:
struct session_sub_entry
    ROLE:
slot receiving the public view and the owned filter copy
FUNC:
  - NAME:
malloc
    KIND:
TYPE_REF
    ROLE:
allocate the filter copy
  - NAME:
realloc
    KIND:
TYPE_REF
    ROLE:
grow the slot array, preserving existing entries and their owned copies
  - NAME:
memcmp
    KIND:
TYPE_REF
    ROLE:
detect an identical filter (length-delimited, not a C string comparison)
  - NAME:
memcpy
    KIND:
TYPE_REF
    ROLE:
copy the filter bytes
  - NAME:
free
    KIND:
TYPE_REF
    ROLE:
release the candidate copy when the array cannot grow
VAR:
  - NAME:
SESSION_INITIAL_SUB_CAP
    ROLE:
slot count allocated for the first subscription (4)

[GUARANTEE]
RAW:
int session_add_subscription(struct session *s, const char *filter, size_t filter_len, uint8_t qos)
NAME:
session_add_subscription
RETURN:
int
PARAMS:
  - TYPE:
struct session *
    NAME:
s
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
const char *
    NAME:
filter
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
size_t
    NAME:
filter_len
    NULLABLE:
false
    OWNERSHIP:
BORROWED
  - TYPE:
uint8_t
    NAME:
qos
    NULLABLE:
false
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
s: borrowed session pointer. filter: borrowed filter bytes (not NUL terminated), already validated as a legal Topic Filter by the caller. filter_len: its length, at least 1. qos: the granted QoS to store, 0..2.
  ACTION:
1) If s is NULL, filter is NULL, filter_len == 0 or qos > 2 return -1 (storage-level argument check; full filter syntax was validated before this call). 2) Scan slots i = 0 .. sub_count - 1: when s->subs[i].view.filter_len == filter_len and memcmp(s->subs[i].view.filter, filter, filter_len) == 0, this is the same filter — set s->subs[i].view.qos = qos and return 0. The position and the existing owned copy are reused, exactly like an MQTT client that re-subscribes to a filter it already has, so no duplicate is created and no allocation happens. 3) Otherwise make the copy: copy = malloc(filter_len); if copy is NULL return -1 with the subscription list unchanged. memcpy(copy, filter, filter_len). 4) If sub_count == sub_cap, grow the array: new_cap = (sub_cap == 0) ? SESSION_INITIAL_SUB_CAP : sub_cap * 2; reject new_cap < sub_cap (size_t overflow) by freeing copy and returning -1; tmp = realloc(s->subs, new_cap * sizeof *tmp) and on NULL free(copy) and return -1 (the old array and all existing filter copies stay valid and reachable through s->subs); otherwise s->subs = tmp and s->sub_cap = new_cap. 5) Install the new slot at index sub_count: view.filter = copy, view.filter_len = filter_len, view.qos = qos, owned = copy; sub_count += 1. 6) Return 0. Failure paths never leave a partially grown array or an orphaned copy: either the copy is adopted by a slot and counted, or it is freed before returning.
  OUTPUT:
0 when the filter was replaced in place (identical filter, same count) or appended as a new slot (count + 1) with its own owned copy. -1 for invalid arguments, an overflowed capacity computation or allocation failure, with the stored subscriptions unchanged.
  INVARIANTS_USED:
    - the session owns a copy of every filter it stores; the borrowed packet bytes do not outlive the decoded packet
    - 0 <= sub_count <= sub_cap and every slot below sub_count holds one owned copy
    - an identical filter is replaced in place rather than duplicated
    - on any failure the stored subscription list must remain exactly as it was
  PRECONDITION:
s is NULL or points to a live, non-destroyed session; filter points to filter_len readable bytes when filter_len > 0.
  POSTCONDITION:
on success the filter is stored with the given QoS and session_subscription_count() is unchanged or grew by one; on failure the list, its copies and its capacity are unchanged.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Not thread safe; single-threaded per session.
