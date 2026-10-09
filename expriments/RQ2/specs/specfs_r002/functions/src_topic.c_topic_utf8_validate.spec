[PROMPT]
Decide whether a byte slice is well-formed UTF-8 (RFC 3629) without overlong forms, surrogates, code points above U+10FFFF or an encoding of U+0000; an empty slice is well formed.

[RELY]
STRUCT:

FUNC:

VAR:


[GUARANTEE]
RAW:
int topic_utf8_validate(const uint8_t *data, size_t len)
NAME:
topic_utf8_validate
RETURN:
int
PARAMS:
  - TYPE:
const uint8_t *
    NAME:
data
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
data points to len caller-owned bytes (a decoded UTF-8 string slice); data may be NULL only when len is 0.
  ACTION:
Walk the slice with an index i. For each byte b = data[i]: (1) b <= 0x7F: reject b == 0x00 (MQTT-1.5.3-2 forbids U+0000); advance 1. (2) 0xC2 <= b <= 0xDF: require one continuation byte (0x80..0xBF); advance 2. (3) b == 0xE0: require data[i+1] in 0xA0..0xBF (rejects overlong three byte forms) then one more continuation; b in 0xE1..0xEC: two continuations; b == 0xED: require data[i+1] in 0x80..0x9F (rejects the surrogate range U+D800..U+DFFF) then one continuation; b in 0xEE..0xEF: two continuations. (4) b == 0xF0: require data[i+1] in 0x90..0xBF (rejects overlong four byte forms) then two continuations; b in 0xF1..0xF3: three continuations; b == 0xF4: require data[i+1] in 0x80..0x8F (caps the code point at U+10FFFF) then two continuations. Every other lead byte (0x80..0xC1, 0xF5..0xFF) is ill formed. Any sequence whose required continuation bytes are absent because the slice ends is ill formed for this function: callers only validate complete fields, and a truncated multi-byte sequence must not be accepted. Per the standard, control characters and non-characters are discouraged but legal, so they are accepted here (no optional close).
  OUTPUT:
1 when every byte belongs to a well-formed sequence, 0 for the first ill-formed byte or truncated sequence encountered. No value is written through any pointer; the slice is only read.
  INVARIANTS_USED:
    - no normalization, no case folding, no substitution
    - 0xEF 0xBB 0xBF is data (U+FEFF), never skipped
    - validation is a pure function of the borrowed slice
  PRECONDITION:
data is NULL with len == 0, or points to len readable bytes.
  POSTCONDITION:
the slice is unmodified and the result depends only on the byte values
  IDEMPOTENT:
true
  THREAD_SAFETY:
Reentrant and read-only; safe for concurrent calls on distinct slices.
