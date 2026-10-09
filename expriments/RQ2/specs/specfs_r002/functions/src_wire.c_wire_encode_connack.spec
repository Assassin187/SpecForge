[PROMPT]
Append a complete CONNACK packet (0x20, Remaining Length 2, acknowledge flags, return code) to a caller-owned output buffer.

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
allocate the whole 4-byte packet before the first byte is written
  - NAME:
buffer_append
    KIND:
CALL
    ROLE:
append the fixed header byte and the two variable header bytes
VAR:
  - NAME:
WIRE_PACKET_CONNACK
    ROLE:
control packet type 2 (fixed header nibble 0x20)
  - NAME:
WIRE_MAX_STRING_LENGTH
    ROLE:
unused upper bound constant available to the module

[GUARANTEE]
RAW:
int wire_encode_connack(struct byte_buf *out, uint8_t ack_flags, uint8_t return_code)
NAME:
wire_encode_connack
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
  - TYPE:
uint8_t
    NAME:
ack_flags
    NULLABLE:
false
    OWNERSHIP:
BORROWED
  - TYPE:
uint8_t
    NAME:
return_code
    NULLABLE:
false
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
out: caller-owned buffer receiving exactly 4 bytes. ack_flags: CONNACK acknowledge flags byte. return_code: CONNACK return code.
  ACTION:
1) If out is NULL return -1. 2) Validate arguments before writing any byte: if (ack_flags & 0xFE) != 0 return -1 (only bit 0, Session Present, is defined; the other bits are reserved and must be 0); if return_code > 5 return -1 (only 0x00..0x05 are defined MQTT 3.1.1 return codes). 3) If buffer_reserve(out, 4) != 0 return -2 (allocation failure; the buffer contents are unchanged, the caller closes the session and nothing is sent). 4) byte0 = (WIRE_PACKET_CONNACK << 4) | 0x00 = 0x20; byte1 = 0x02 (Remaining Length 2); buffer_append(out, &byte0, 1); buffer_append(out, &byte1, 1); buffer_append(out, &ack_flags, 1); buffer_append(out, &return_code, 1). Because the reserve covered all 4 bytes these appends are guaranteed to succeed; if one still returns -1 the function returns -2 and the caller must discard the whole output buffer rather than put it on the wire. 5) Return 0. The packet is appended after any bytes already queued, so a CONNACK can follow queued output without disturbing it.
  OUTPUT:
0 when exactly 4 bytes 0x20 0x02 ack_flags return_code were appended and out->len grew by 4; -1 for a NULL out, a reserved ack_flags bit or an undefined return code (no byte written); -2 when the buffer could not be grown (no complete packet appended). The successful CONNACK for scope R02 is wire_encode_connack(out, 0x00, 0x00) -> 20 02 00 00.
  INVARIANTS_USED:
    - CONNACK is 0x20 with Remaining Length 2
    - the acknowledge flags byte has only bit 0 defined
    - return codes are 0x00..0x05
    - a queue append never reorders or drops already queued bytes
  PRECONDITION:
out is NULL or a valid byte_buf owned by the caller.
  POSTCONDITION:
on success the last 4 bytes of out are the encoded CONNACK; on -1 nothing changed; on -2 the contents may be unchanged or unusable, and the caller reaches a -2 only by abandoning the buffer.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Not thread safe; the caller owns out exclusively.
