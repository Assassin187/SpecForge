[PROMPT]
Big-endian 2-byte field read helper (private).

[RELY]
STRUCT:

FUNC:

VAR:


[GUARANTEE]
RAW:
static uint16_t wire_read_u16(const uint8_t *p)
NAME:
wire_read_u16
RETURN:
uint16_t
PARAMS:
  - TYPE:
const uint8_t *
    NAME:
p
    NULLABLE:
false
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
p points to the first of two readable bytes inside a caller-owned packet.
  ACTION:
Return ((uint16_t)p[0] << 8) | (uint16_t)p[1]: MSB is the byte at the lower address, LSB the next one (MQTT network byte order, fact F006). No bounds check is performed: every caller establishes that two bytes are available before calling.
  OUTPUT:
The 16-bit value with p[0] as the most significant byte, e.g. p = 00 0A yields 10 and p = FF FF yields 65535.
  INVARIANTS_USED:
    - multi-byte integers are transmitted MSB first
    - the helper neither consumes nor retains p
  PRECONDITION:
p is non-NULL and at least 2 bytes are readable from p.
  POSTCONDITION:
No state is modified; the caller offsets are unchanged.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Pure function of its argument; safe on disjoint buffers.
