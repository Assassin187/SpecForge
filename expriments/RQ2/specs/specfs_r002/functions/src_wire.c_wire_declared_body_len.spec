[PROMPT]
Re-parse the Remaining Length field of an already framed packet and report whether len is exactly the declared frame size (private).

[RELY]
STRUCT:

FUNC:

VAR:
  - NAME:
WIRE_MAX_REMAINING_LENGTH_BYTES
    ROLE:
a Remaining Length field is at most 4 bytes
  - NAME:
WIRE_MAX_REMAINING_LENGTH
    ROLE:
268435455 is the largest Remaining Length value

[GUARANTEE]
RAW:
static enum wire_status wire_declared_body_len(const uint8_t *packet, size_t len, size_t *header_len, size_t *remaining_length)
NAME:
wire_declared_body_len
RETURN:
enum wire_status
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
header_len
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
size_t *
    NAME:
remaining_length
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
packet with len bytes (the caller passes exactly the frame length reported by wire_decode_header.total_len, or a shorter buffer when the frame is not yet complete), header_len/remaining_length receiving the parsed numbers.
  ACTION:
1) If packet is NULL return WIRE_INVALID. 2) If len < 1 return WIRE_INCOMPLETE (no fixed header byte buffered) - the caller maps this to WIRE_INVALID because it already framed the packet. 3) Walk the Remaining Length field from index 1 with multiplier 1, 128, 16384, 2097152: for i = 0..3 read packet[1+i] if 2+i <= len, else return WIRE_INCOMPLETE; accumulate (byte & 0x7F) * multiplier; stop when the continuation bit is clear. If the fourth byte still has the continuation bit set, return WIRE_INVALID (a Remaining Length field longer than 4 bytes is malformed, fact F004/F005). 4) h = 1 + number_of_bytes_consumed; total = h + value. 5) If len < total return WIRE_INCOMPLETE. 6) If len > total return WIRE_INVALID (the buffer holds more bytes than this packet declares, so it is not exactly one framed packet). 7) Write *header_len = h and *remaining_length = value when the pointers are non-NULL and return WIRE_COMPLETE.
  OUTPUT:
WIRE_COMPLETE with *header_len (2..5) and *remaining_length (0..268435455) set when len == header_len + remaining_length; WIRE_INCOMPLETE when fewer bytes than the declared frame are buffered; WIRE_INVALID when the Remaining Length field is unusable or len exceeds the declared frame. Public packet decoders treat every non-COMPLETE result as WIRE_INVALID, so a short buffer never escapes as INCOMPLETE.
  INVARIANTS_USED:
    - Remaining Length is encoded in at most 4 bytes of 7 bits each
    - a decoder only runs on a complete packet
  PRECONDITION:
packet points to len readable bytes beginning with a fixed header byte.
  POSTCONDITION:
The output numbers are the ones carried by the packet bytes; the packet is never modified.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Pure function of its arguments.
