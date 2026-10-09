[PROMPT]
Read one 2-byte length prefixed field with bounds checking and optional UTF-8 validation, advancing the cursor (private).

[RELY]
STRUCT:
  - NAME:
struct wire_span
    ROLE:
receives the borrowed field slice
FUNC:
  - NAME:
wire_read_u16
    KIND:
CALL
    ROLE:
read the 2-byte big-endian length prefix
  - NAME:
topic_utf8_validate
    KIND:
CALL
    ROLE:
validate the field bytes when validate_utf8 is set
VAR:
  - NAME:
WIRE_MAX_STRING_LENGTH
    ROLE:
65535 is the largest value a 2-byte prefix can express

[GUARANTEE]
RAW:
static int wire_decode_string(const uint8_t *packet, size_t len, size_t *pos, struct wire_span *out, int validate_utf8)
NAME:
wire_decode_string
RETURN:
int
PARAMS:
  - TYPE:
const uint8_t *
    NAME:
packet
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
  - TYPE:
size_t *
    NAME:
pos
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
struct wire_span *
    NAME:
out
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
int
    NAME:
validate_utf8
    NULLABLE:
false
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
packet with len buffered bytes, *pos giving the offset of the 2-byte length prefix, out receiving the borrowed slice, validate_utf8 selecting the UTF-8 check.
  ACTION:
1) If packet, pos or out is NULL return 0 (indistinguishable from truncation, and both outcomes become WIRE_INVALID at the decoder). 2) If *pos > len or len - *pos < 2, return 0 and change nothing. 3) field_len = wire_read_u16(packet + *pos). 4) If field_len > (len - *pos - 2), return 0 (the declared field runs past the packet end). 5) If validate_utf8 is nonzero and topic_utf8_validate(packet + *pos + 2, field_len) != 1, return -1 and leave *pos unchanged so the caller stops. 6) out->data = packet + *pos + 2; out->len = field_len; *pos += 2 + field_len; return 1. The bytes stay in the caller-owned packet: no copy is made and out must not be used after the packet is released.
  OUTPUT:
1 = one complete field decoded, *pos advanced by 2 + field_len, *out filled with a borrowed slice (possibly empty when field_len is 0); 0 = truncation/declared length past the end, *pos and *out untouched; -1 = the field is complete but not valid UTF-8.
  INVARIANTS_USED:
    - every string field is a 2-byte length prefix followed by exactly that many bytes
    - decoded slices are borrowed from the packet and never copied
    - a malformed field makes the whole packet invalid
  PRECONDITION:
packet points to len readable bytes of one framed packet; *pos <= len; out points to writable storage.
  POSTCONDITION:
On 1 the field bytes are exactly packet[*pos_before+2 .. *pos_before+2+field_len-1]; on 0 and -1 no caller-visible state changed except nothing.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Uses only its arguments; safe when no other thread mutates the packet.
