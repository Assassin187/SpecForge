[PROMPT]
Decide whether a session wants a topic name, by testing the topic against every stored filter with the MQTT wildcard rules.

[RELY]
STRUCT:
  - NAME:
struct session
    ROLE:
source of the stored filters
  - NAME:
struct session_sub_entry
    ROLE:
per-slot public view with the owned filter bytes
FUNC:
  - NAME:
topic_filter_matches
    KIND:
CALL
    ROLE:
apply the wildcard and '$' matching rules to one stored filter
  - NAME:
session_subscription_count
    KIND:
CALL
    ROLE:
bound the scan by the stored entry count
VAR:


[GUARANTEE]
RAW:
int session_matches_topic(const struct session *s, const char *topic, size_t topic_len)
NAME:
session_matches_topic
RETURN:
int
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
const char *
    NAME:
topic
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
size_t
    NAME:
topic_len
    NULLABLE:
false
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
s: borrowed session pointer. topic: borrowed topic-name bytes (not NUL terminated), already validated as a legal, nonempty, wildcard-free Topic Name by the PUBLISH decoder. topic_len: its length in bytes.
  ACTION:
1) If s is NULL or topic is NULL return 0. 2) n = session_subscription_count(s); for i = 0 .. n - 1: v = &s->subs[i].view; if topic_filter_matches((const uint8_t *)v->filter, v->filter_len, (const uint8_t *)topic, topic_len) return 1. 3) Return 0. The scan is a plain linear search over the session's own slots, so no allocation happens and the borrowed topic is used only during the call. Matching is case sensitive and applies no normalization: the bytes of the topic must equal the bytes of the filter levels, with '+' standing for exactly one whole level and '#' for the current level and everything below it (including zero further levels) when it is the last level; filters that begin with a wildcard never match a topic whose first level begins with '$' (that rule is implemented inside topic_filter_matches). Because a filter is compared byte-wise and never as a C string, a topic containing binary bytes is matched exactly.
  OUTPUT:
1 when at least one stored filter matches the topic; 0 when no filter matches, when the session stores no subscriptions, or when s or topic is NULL.
  INVARIANTS_USED:
    - only validated filters are stored, so matching never needs to re-validate syntax
    - matching is case sensitive and performs no normalization or percent decoding
    - level boundaries are the separators between filters and topics; a wildcard covers whole levels only
    - routing uses the same predicate for every recipient, including the publisher itself
  PRECONDITION:
s is NULL or points to a live session; topic points to topic_len readable bytes when topic_len > 0.
  POSTCONDITION:
no session state changes.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Read-only over the session; not safe against a concurrent session_add_subscription() on the same session.
