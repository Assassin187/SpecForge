[PROMPT]
Decode a complete SUBSCRIBE packet: fixed header flags, nonzero Packet Identifier, at least one filter/QoS pair, every filter a legal Topic Filter and every requested QoS byte zero in its reserved bits and at most 2.

[RELY]
STRUCT:
  - NAME:
struct wire_subscribe
    ROLE:
packet identifier plus borrowed filter/QoS payload slice
  - NAME:
struct wire_span
    ROLE:
borrowed filter slice used during validation
FUNC:
  - NAME:
wire_declared_body_len
    KIND:
CALL
    ROLE:
confirm len is exactly the declared frame size
  - NAME:
wire_read_u16
    KIND:
CALL
    ROLE:
read the Packet Identifier and each filter length prefix
  - NAME:
topic_filter_validate
    KIND:
CALL
    ROLE:
reject an illegal Topic Filter or one containing wildcards in the wrong position
VAR:
  - NAME:
WIRE_PACKET_SUBSCRIBE
    ROLE:
expected control packet type 8
  - NAME:
WIRE_MAX_STRING_LENGTH
    ROLE:
upper bound of a length-prefixed field (65535)
  - NAME:
WIRE_COMPLETE
    ROLE:
successful outcome
  - NAME:
WIRE_INVALID
    ROLE:
malformed SUBSCRIBE outcome

[GUARANTEE]
RAW:
enum wire_status wire_decode_subscribe(const uint8_t *packet, size_t len, struct wire_subscribe *out)
NAME:
wire_decode_subscribe
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
struct wire_subscribe *
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
packet with len bytes holding exactly one framed SUBSCRIBE (the caller obtained len from wire_decode_header.total_len); out receiving the Packet Identifier and a borrowed view of the filter/QoS payload.
  ACTION:
1) If packet is NULL or out is NULL return WIRE_INVALID. 2) Zero *out. 3) wire_declared_body_len(packet, len, &header_len, &rl): if it is not WIRE_COMPLETE return WIRE_INVALID (a decoder only runs on a complete frame). 4) Fixed header flags: the low 4 bits must be exactly 0x2, else WIRE_INVALID (MQTT-3.8.1-1; the type nibble is 8, check (packet[0] >> 4) == WIRE_PACKET_SUBSCRIBE too and return WIRE_INVALID otherwise so the decoder is safe when called with any packet). 5) pos = header_len. Packet Identifier: require at least 3 bytes after the header (two identifier bytes plus at least one payload byte before the first length prefix is read); if len - pos < 3 return WIRE_INVALID; pid = wire_read_u16(packet + pos); pos += 2; if pid == 0 return WIRE_INVALID (MQTT-2.3.1-1: a zero Packet Identifier is malformed). 6) Payload walk: pairs = 0; while (pos < len): (a) require pos + 2 <= len else WIRE_INVALID; flen = wire_read_u16(packet + pos); pos += 2; (b) require flen >= 1 (a zero-length Topic Filter is malformed; topic_filter_validate would also refuse it, checked here so the span arithmetic stays simple) else WIRE_INVALID; (c) require pos + flen <= len else WIRE_INVALID (filter runs past the packet); span.data = packet + pos, span.len = flen; (d) if topic_filter_validate(span.data, span.len) != 1 return WIRE_INVALID (illegal UTF-8, '#' not last and alone, '+' not a whole level, or longer than 65535); pos += flen; (e) require pos + 1 <= len else WIRE_INVALID (QoS byte missing); qos = packet[pos++]; (f) if (qos & 0xFC) != 0 return WIRE_INVALID (bits 7-2 are reserved in a SUBSCRIBE QoS byte) and if (qos & 0x03) == 3 return WIRE_INVALID (requested QoS 3 is reserved, MQTT-3.8.3-4); the value 0, 1 or 2 is well formed and is a policy input for the broker, never a decode error; pairs++. 7) If pairs == 0 return WIRE_INVALID (MQTT-3.8.3-3: a SUBSCRIBE payload must contain at least one filter/QoS pair). 8) out->packet_id = pid; out->payload = packet + header_len + 2; out->payload_len = len - (header_len + 2); return WIRE_COMPLETE. The payload slice borrows the caller's packet and stays valid exactly as long as it does; nothing is allocated or copied.
  OUTPUT:
WIRE_COMPLETE with *out.packet_id = Packet Identifier (nonzero) and *out.payload / *out.payload_len describing the whole filter/QoS payload in its original byte order, so wire_subscribe_next can re-walk it. WIRE_INVALID for NULL arguments, a truncated or over-long frame, fixed header flags other than 0x2, a zero Packet Identifier, a missing or over-long filter field, an illegal Topic Filter, a QoS byte with a reserved bit set or value 3, or an empty payload. WIRE_INCOMPLETE is never returned.
  INVARIANTS_USED:
    - SUBSCRIBE fixed header flags are 0x2
    - the Packet Identifier of SUBSCRIBE is nonzero (MQTT-2.3.1-1)
    - a SUBSCRIBE carries at least one filter/QoS pair (MQTT-3.8.3-3)
    - a SUBSCRIBE QoS byte has bits 7-2 zero and a value of 0, 1 or 2
    - every Topic Filter in the payload is independently legal
  PRECONDITION:
packet holds one complete SUBSCRIBE frame of len bytes, len >= 2.
  POSTCONDITION:
*out borrows bytes of packet; no allocation happens and packet is not modified.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Pure function of its arguments; packet must not be mutated concurrently.
