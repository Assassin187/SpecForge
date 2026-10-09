[PROMPT]
Decide whether a byte slice is a legal MQTT Topic Filter: at least one character, at most 65535 bytes, well-formed UTF-8 without U+0000, '#' only alone or after a separator and always last, '+' only as a complete level.

[RELY]
STRUCT:

FUNC:
  - NAME:
topic_utf8_validate
    KIND:
CALL
    ROLE:
Well-formedness and U+0000 check on the same slice
VAR:
  - NAME:
WIRE_MAX_STRING_LENGTH
    ROLE:
65535 byte encoded-string upper bound

[GUARANTEE]
RAW:
int topic_filter_validate(const uint8_t *filter, size_t len)
NAME:
topic_filter_validate
RETURN:
int
PARAMS:
  - TYPE:
const uint8_t *
    NAME:
filter
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
size_t
    NAME:
len
    NULLABLE:
false
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
filter points to len caller-owned bytes of a decoded SUBSCRIBE Topic Filter field; filter may be NULL only when len is 0.
  ACTION:
1) Reject len == 0 (filters are at least one character, MQTT-4.7.3-1) and len > 65535 (MQTT-4.7.3-3) and filter == NULL. 2) Reject any encoding of U+0000 and any ill-formed UTF-8 by requiring topic_utf8_validate(filter, len) == 1. 3) Scan bytes with index i: for byte 0x23 ('#') require (i == 0 || filter[i-1] == '/') and i == len - 1, otherwise reject: the multi-level wildcard must stand alone or follow a separator and MUST be the last character of the filter (MQTT-4.7.1-2). For byte 0x2B ('+') require (i == 0 || filter[i-1] == '/') and (i == len - 1 || filter[i+1] == '/'), otherwise reject: the single-level wildcard must occupy an entire level (MQTT-4.7.1-3). Other bytes, including space, '$' and empty levels, are unrestricted. 4) Return 1.
  OUTPUT:
1 for a legal Topic Filter, 0 otherwise; the slice is unmodified.
  INVARIANTS_USED:
    - wildcard placement is level based and byte wise
    - no normalization or case folding
    - len >= 1 and len <= 65535
  PRECONDITION:
filter is NULL with len == 0 or points to len readable bytes.
  POSTCONDITION:
the slice is unmodified and the result depends only on its bytes and length
  IDEMPOTENT:
true
  THREAD_SAFETY:
Reentrant and read-only.
