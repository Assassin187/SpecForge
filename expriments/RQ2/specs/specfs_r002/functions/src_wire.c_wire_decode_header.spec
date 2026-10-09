[PROMPT]
Frame one MQTT control packet from the stream buffer: parse type, flags and Remaining Length, and report whether the whole packet is buffered.

[RELY]
STRUCT:
  - NAME:
struct wire_header
    ROLE:
receives type, flags, header length, Remaining Length and total frame size
FUNC:

VAR:
  - NAME:
WIRE_MAX_REMAINING_LENGTH
    ROLE:
268435455 is the largest legal Remaining Length
  - NAME:
WIRE_MAX_REMAINING_LENGTH_BYTES
    ROLE:
at most 4 Remaining Length bytes

[GUARANTEE]
RAW:
enum wire_status wire_decode_header(const uint8_t *buf, size_t len, struct wire_header *out)
NAME:
wire_decode_header
RETURN:
enum wire_status
PARAMS:
  - TYPE:
const uint8_t *
    NAME:
buf
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
struct wire_header *
    NAME:
out
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
buf holding len unconsumed stream bytes for one connection; out receiving the framing numbers.
  ACTION:
1) If buf is NULL or out is NULL return WIRE_INVALID. 2) If len < 1 return WIRE_INCOMPLETE with out untouched (nothing decoded yet). 3) type = buf[0] >> 4; flags = buf[0] & 0x0F. If type == 0 or type == 15 return WIRE_INVALID with out untouched (reserved control packet types, facts F002/F003). 4) Parse the Remaining Length field starting at index 1, multiplier 1 then 128, 16384, 2097152; before reading byte number 1+i require 1+i < len, otherwise return WIRE_INCOMPLETE with out untouched (the Remaining Length field itself is unfinished). Accumulate (byte & 0x7F) * multiplier and stop when the continuation bit 0x80 is clear. If the fourth byte still has the continuation bit set return WIRE_INVALID (a Remaining Length field longer than 4 bytes is malformed). 5) out->type = type; out->flags = flags; out->remaining_length = value; out->header_len = 1 + encoded_byte_count (2..5); out->total_len = out->header_len + value (value <= 268435455 so no size_t overflow). 6) Return WIRE_COMPLETE when len >= out->total_len, otherwise WIRE_INCOMPLETE.
  OUTPUT:
WIRE_COMPLETE when the buffer holds at least total_len bytes and out describes the packet; WIRE_INCOMPLETE when more bytes are needed - out is filled once the Remaining Length field parsed (so the caller may size a read request from total_len) and untouched while the Remaining Length field itself is unfinished; WIRE_INVALID for a reserved type or a Remaining Length field wider than 4 bytes, with out untouched. The caller consumes exactly total_len bytes for one packet.
  INVARIANTS_USED:
    - control packet type 0 and 15 are reserved and forbidden
    - Remaining Length counts every byte after the Remaining Length field
    - at most 4 Remaining Length bytes are legal
  PRECONDITION:
buf points to len readable bytes of the connection's stream (a slice may contain a partial packet, one packet or several).
  POSTCONDITION:
No byte of buf is modified; the caller's buffer length is unchanged.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Pure function of its arguments.
