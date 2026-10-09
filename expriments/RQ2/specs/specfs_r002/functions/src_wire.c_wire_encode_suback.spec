[PROMPT]
Append a complete SUBACK packet echoing the SUBSCRIBE Packet Identifier and carrying one result code per filter in the order the filters appeared.

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
allocate the whole packet before the first byte is written
  - NAME:
buffer_append
    KIND:
CALL
    ROLE:
append the fixed header, the Packet Identifier bytes and the result codes
  - NAME:
wire_write_remaining_length
    KIND:
CALL
    ROLE:
append the Remaining Length encoding of 2 + count
VAR:
  - NAME:
WIRE_PACKET_SUBACK
    ROLE:
control packet type 9 (fixed header nibble 0x90)
  - NAME:
WIRE_MAX_STRING_LENGTH
    ROLE:
65535; the largest accepted number of result codes

[GUARANTEE]
RAW:
int wire_encode_suback(struct byte_buf *out, uint16_t packet_id, const uint8_t *codes, size_t count)
NAME:
wire_encode_suback
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
uint16_t
    NAME:
packet_id
    NULLABLE:
false
    OWNERSHIP:
BORROWED
  - TYPE:
const uint8_t *
    NAME:
codes
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
size_t
    NAME:
count
    NULLABLE:
false
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
out: caller-owned buffer. packet_id: the nonzero Packet Identifier of the SUBSCRIBE being answered. codes: count result bytes in filter order. count: number of filters, at least 1.
  ACTION:
1) If out is NULL or codes is NULL return -1. 2) If count == 0 return -1 (MQTT-3.9.3-1 requires at least one return code, matching the at-least-one-pair SUBSCRIBE rule); if count > 65535 return -1; if packet_id == 0 return -1 (the echoed identifier must be nonzero, MQTT-2.3.1-1). 3) For every i < count: if codes[i] is not one of 0x00, 0x01, 0x02 or 0x80 return -1 (only these four SUBACK return codes exist). All argument validation happens before the first byte is written. 4) body = 2 + count; rl_width = (body < 128) ? 1 : (body < 16384) ? 2 : (body < 2097152) ? 3 : 4; if buffer_reserve(out, 1 + rl_width + body) != 0 return -2. 5) hdr = (WIRE_PACKET_SUBACK << 4) = 0x90; buffer_append(out, &hdr, 1); wire_write_remaining_length(out, body); pid_hi = (uint8_t)(packet_id >> 8); pid_lo = (uint8_t)(packet_id & 0xFF); buffer_append(out, &pid_hi, 1); buffer_append(out, &pid_lo, 1); buffer_append(out, codes, count). Any nonzero return after the reserve means the packet is incomplete: return -2 and let the caller discard the buffer. 6) Return 0. The codes array order is the filter order, so result i corresponds to the i-th filter/QoS pair of the SUBSCRIBE payload.
  OUTPUT:
0 when 0x90, the Remaining Length of 2 + count, the two identifier bytes (MSB first) and count result codes were appended in that order. -1 for a NULL out/codes, count 0, count above 65535, a zero packet_id or an undefined result code, with nothing written. -2 when the buffer could not be grown even though the arguments were valid. This broker always passes count = number of filters and codes[i] = 0x00, producing 90 03 00 01 00 for the single-filter SUBSCRIBE with Packet Identifier 1.
  INVARIANTS_USED:
    - SUBACK has fixed header flags 0 and Remaining Length 2 + number of filters
    - the SUBACK Packet Identifier equals the nonzero SUBSCRIBE Packet Identifier (MQTT-3.9.2-1)
    - one result code per filter, in the order the filters appeared (MQTT-3.9.3-1)
    - SUBACK return codes are 0x00, 0x01, 0x02 and 0x80
    - a queue append preserves already queued bytes
  PRECONDITION:
out is NULL or a valid byte_buf; codes points to count readable result bytes when count > 0.
  POSTCONDITION:
on success the SUBACK is appended after the bytes already queued; on -1 nothing changed; on -2 no complete packet was appended.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Not thread safe; the caller owns out exclusively.
