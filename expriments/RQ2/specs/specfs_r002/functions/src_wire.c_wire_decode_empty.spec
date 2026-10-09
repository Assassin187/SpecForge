[PROMPT]
Validate a complete zero-length PINGREQ or DISCONNECT: exact type, reserved flags 0 and Remaining Length 0, reporting the two consumed bytes.

[RELY]
STRUCT:

FUNC:
  - NAME:
wire_declared_body_len
    KIND:
CALL
    ROLE:
confirm len is exactly the declared frame size
VAR:
  - NAME:
WIRE_PACKET_PINGREQ
    ROLE:
accepted expect_type 12
  - NAME:
WIRE_PACKET_DISCONNECT
    ROLE:
accepted expect_type 14
  - NAME:
WIRE_COMPLETE
    ROLE:
successful outcome
  - NAME:
WIRE_INVALID
    ROLE:
malformed PINGREQ/DISCONNECT outcome

[GUARANTEE]
RAW:
enum wire_status wire_decode_empty(const uint8_t *packet, size_t len, uint8_t expect_type, size_t *consumed)
NAME:
wire_decode_empty
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
uint8_t
    NAME:
expect_type
    NULLABLE:
false
    OWNERSHIP:
BORROWED
  - TYPE:
size_t *
    NAME:
consumed
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
packet with len bytes holding exactly one framed control packet; expect_type is WIRE_PACKET_PINGREQ (12) or WIRE_PACKET_DISCONNECT (14); consumed receives the packet length in bytes.
  ACTION:
1) If packet is NULL or consumed is NULL return WIRE_INVALID. 2) If expect_type is neither WIRE_PACKET_PINGREQ nor WIRE_PACKET_DISCONNECT return WIRE_INVALID (this helper only validates the two zero-length packets MQTT 3.1.1 defines). 3) *consumed = 0. 4) wire_declared_body_len(packet, len, &header_len, &rl): if it is not WIRE_COMPLETE or rl != 0 return WIRE_INVALID (PINGREQ/DISCONNECT must declare a Remaining Length of 0. F004/F005 define only the encoding algorithm and MQTT 3.1.1 states no minimality rule, so every legal encoding width whose decoded value is 0 is accepted - 0x00, 0x80 0x00, 0x80 0x80 0x00 and 0x80 0x80 0x80 0x00 - exactly as wire_decode_header accepts the 5-byte encoding of 0; a nonzero decoded value, a truncated Remaining Length field and a five-byte (over-long) Remaining Length field are malformed, and a decoder never sees a partial frame because wire_decode_header reports incompleteness first). 5) If (packet[0] >> 4) != expect_type return WIRE_INVALID (wrong control packet type). 6) If (packet[0] & 0x0F) != 0 return WIRE_INVALID (the low 4 bits of PINGREQ and DISCONNECT are reserved and must be 0, MQTT-3.12.1-1 / MQTT-3.14.1-1). 7) *consumed = header_len, the encoded fixed-header size: 2 for 0x00, 3 for 0x80 0x00, 4 for 0x80 0x80 0x00, 5 for 0x80 0x80 0x80 0x00 (the whole packet, since the Remaining Length decodes to 0 so there is no body); return WIRE_COMPLETE. No bytes are retained and nothing is allocated.
  OUTPUT:
WIRE_COMPLETE with *consumed = header_len (2..5) when packet holds exactly the expected zero-body packet: type nibble == expect_type, flag bits 0, and a Remaining Length field of any legal encoding width that decodes to 0. WIRE_INVALID for NULL arguments, an unexpected expect_type, a control packet type other than expect_type, nonzero flag bits, a Remaining Length that does not decode to 0, a truncated or over-long (five byte) Remaining Length field, or a caller length that is not exactly header_len + 0. *consumed is only written on the WIRE_COMPLETE path.
  INVARIANTS_USED:
    - PINGREQ and DISCONNECT declare a Remaining Length of 0 (MQTT-3.12.1-1, MQTT-3.14.1-1, F042, F044)
    - the fixed header flags of PINGREQ and DISCONNECT are reserved and must be 0 (F003, F004)
    - the Remaining Length field is 1..4 bytes and decodes to 0 for every legal encoding of 0 (F004, F005); no fact forbids a non-minimal encoding
    - the consumed count is the encoded header length, so the caller drops exactly the packet bytes from the receive buffer (CR-LENGTH)
  PRECONDITION:
packet holds one complete frame of len bytes (wire_decode_header already reported WIRE_COMPLETE/total_len == len).
  POSTCONDITION:
no memory is allocated or modified; only *consumed is written on success.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Pure function of its arguments.
