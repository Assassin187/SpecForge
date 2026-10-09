[PROMPT]
Decide whether a byte slice is a legal MQTT Topic Name: at least one character, at most 65535 encoded bytes, no wildcard byte, well-formed UTF-8 without U+0000.

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
65535 byte encoded-string upper bound (MQTT-4.7.3-3)

[GUARANTEE]
RAW:
int topic_name_validate(const uint8_t *name, size_t len)
NAME:
topic_name_validate
RETURN:
int
PARAMS:
  - TYPE:
const uint8_t *
    NAME:
name
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
name points to len caller-owned bytes of a decoded PUBLISH Topic Name field; name may be NULL only when len is 0.
  ACTION:
1) Reject len == 0 (a Topic Name must be at least one character, MQTT-4.7.3-1). 2) Reject len > 65535 (an encoded string cannot exceed 65535 bytes, MQTT-4.7.3-3). 3) Reject name == NULL. 4) Scan every byte: reject 0x23 ('#') and 0x2B ('+') because wildcard characters MUST NOT appear in a Topic Name (MQTT-4.7.1-1). 5) Return topic_utf8_validate(name, len): this rejects ill-formed UTF-8 and any encoding of U+0000. No normalization, case folding or character substitution is performed; a name consisting only of '/' is legal and distinct levels are preserved.
  OUTPUT:
1 when the slice is a legal Topic Name; 0 otherwise. The slice is never modified.
  INVARIANTS_USED:
    - topic names carry no wildcards
    - 1 <= encoded length <= 65535
    - case sensitive, spaces and empty levels are legal
  PRECONDITION:
name is NULL with len == 0 or points to len readable bytes.
  POSTCONDITION:
the slice is unmodified and the result depends only on its bytes and length
  IDEMPOTENT:
true
  THREAD_SAFETY:
Reentrant and read-only.
