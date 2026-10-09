[PROMPT]
Append the fixed two-byte PINGRESP packet 0xD0 0x00 to a caller-owned output buffer.

[RELY]
STRUCT:
  - NAME:
struct byte_buf
    ROLE:
destination for the encoded packet bytes
FUNC:
  - NAME:
buffer_reserve
    KIND:
CALL
    ROLE:
allocate the two packet bytes in one step
  - NAME:
buffer_append
    KIND:
CALL
    ROLE:
append the fixed header byte
VAR:
  - NAME:
WIRE_PACKET_PINGRESP
    ROLE:
control packet type 13 (fixed header 0xD0)

[GUARANTEE]
RAW:
int wire_encode_pingresp(struct byte_buf *out)
NAME:
wire_encode_pingresp
RETURN:
int
PARAMS:
  - TYPE:
struct byte_buf *
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
out: caller-owned output buffer that receives exactly two bytes (a PINGREQ was validated by the caller).
  ACTION:
1) If out is NULL return -1 (nothing written). 2) If buffer_reserve(out, 2) != 0 return -2 (allocation failure; contents unchanged). 3) byte0 = (WIRE_PACKET_PINGRESP << 4) | 0x00 = 0xD0; byte1 = 0x00 (Remaining Length 0); buffer_append(out, &byte0, 1); buffer_append(out, &byte1, 1). Both appends are guaranteed to succeed after the reserve; if one returns -1, return -2 and let the caller discard the buffer. 4) Return 0. The response is appended to the queue, so business traffic queued earlier or later is unaffected and keeps its order (scope R09: the connection continues after a PINGRESP).
  OUTPUT:
0 when exactly 0xD0 0x00 was appended and out->len grew by 2; -1 for a NULL out with nothing written; -2 when the buffer could not be grown.
  INVARIANTS_USED:
    - PINGRESP has type 13, reserved flags 0 and Remaining Length 0 (MQTT-3.13.1-1)
    - the response is queued, not sent inline, so a slow socket never blocks the event loop
    - a queue append preserves the relative order of queued packets
  PRECONDITION:
out is NULL or a valid byte_buf owned by the caller.
  POSTCONDITION:
on success the last two bytes of out are 0xD0 0x00; on failure no complete packet is appended.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Not thread safe; the caller owns out exclusively.
