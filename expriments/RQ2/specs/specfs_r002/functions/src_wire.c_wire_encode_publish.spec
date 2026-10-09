[PROMPT]
Append one outbound QoS 0 PUBLISH packet, copying the topic name and the arbitrary binary payload verbatim, with the RETAIN bit as given.

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
append the fixed header, the topic length prefix, the topic and the payload
  - NAME:
wire_write_remaining_length
    KIND:
CALL
    ROLE:
append the Remaining Length encoding of 2 + topic_len + payload_len
  - NAME:
topic_name_validate
    KIND:
CALL
    ROLE:
refuse an outbound topic that is empty, too long, not UTF-8 or contains a wildcard
VAR:
  - NAME:
WIRE_PACKET_PUBLISH
    ROLE:
control packet type 3 (fixed header nibble 0x30)
  - NAME:
WIRE_MAX_REMAINING_LENGTH
    ROLE:
268435455; upper bound of the encoded Remaining Length value

[GUARANTEE]
RAW:
int wire_encode_publish(struct byte_buf *out, const uint8_t *topic, size_t topic_len, const uint8_t *payload, size_t payload_len, uint8_t qos, uint8_t dup, uint8_t retain)
NAME:
wire_encode_publish
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
const uint8_t *
    NAME:
topic
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
size_t
    NAME:
topic_len
    NULLABLE:
false
    OWNERSHIP:
BORROWED
  - TYPE:
const uint8_t *
    NAME:
payload
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
size_t
    NAME:
payload_len
    NULLABLE:
false
    OWNERSHIP:
BORROWED
  - TYPE:
uint8_t
    NAME:
qos
    NULLABLE:
false
    OWNERSHIP:
BORROWED
  - TYPE:
uint8_t
    NAME:
dup
    NULLABLE:
false
    OWNERSHIP:
BORROWED
  - TYPE:
uint8_t
    NAME:
retain
    NULLABLE:
false
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
out: caller-owned output buffer. topic/topic_len: borrowed Topic Name bytes, normally a slice of the received PUBLISH. payload/payload_len: borrowed payload bytes, any binary content including 0x00 and possibly empty (payload may be NULL when payload_len is 0). qos/dup/retain: outbound flag bits.
  ACTION:
1) If out is NULL return -1. 2) Validate before writing: if qos != 0 return -1 (this broker only sends QoS 0, and a QoS 1/2 packet would need a Packet Identifier); if dup != 0 return -1 (DUP must be 0 for QoS 0, MQTT-3.3.1-2); if retain > 1 return -1 (one bit); if topic is NULL return -1; if payload is NULL and payload_len > 0 return -1; if topic_name_validate(topic, topic_len) != 1 return -1 (empty topic, more than 65535 bytes, ill-formed UTF-8 or a wildcard byte). 3) body = 2 + topic_len + payload_len (size_t arithmetic); if body > WIRE_MAX_REMAINING_LENGTH return -1 (the Remaining Length field cannot express it). 4) rl_width = (body < 128) ? 1 : (body < 16384) ? 2 : (body < 2097152) ? 3 : 4; if buffer_reserve(out, 1 + rl_width + body) != 0 return -2. 5) hdr = ((WIRE_PACKET_PUBLISH << 4) & 0xF0) | (uint8_t)((retain & 0x01)) = 0x30 | retain; buffer_append(out, &hdr, 1); wire_write_remaining_length(out, body); tlen_hi = (uint8_t)(topic_len >> 8); tlen_lo = (uint8_t)(topic_len & 0xFF); buffer_append(out, &tlen_hi, 1); buffer_append(out, &tlen_lo, 1); buffer_append(out, topic, topic_len); buffer_append(out, payload, payload_len). A nonzero return after the reserve means the packet is incomplete: return -2 and let the caller discard the buffer. 6) Return 0. Topic and payload are never inspected beyond topic validation, so 0x00 bytes, 0xFF bytes and an empty payload survive byte for byte, and no NUL terminator is added.
  OUTPUT:
0 when 0x30|retain, the Remaining Length of 2 + topic_len + payload_len, the two topic length bytes (MSB first), the topic bytes and the payload bytes were appended in that order. -1 for a NULL out/topic, a NULL payload with a nonzero length, qos != 0, dup != 0, retain > 1, an illegal topic name or a packet too large for the Remaining Length field, with nothing written. -2 when the buffer could not be grown.
  INVARIANTS_USED:
    - outbound broker PUBLISH is always QoS 0 with DUP 0
    - the topic name is written as a 2-byte length prefix followed by the exact bytes, with no wildcards
    - the payload is copied verbatim and its length comes from the frame, not from a terminator
    - the RETAIN bit is the only variable fixed-header flag bit in an outbound PUBLISH
    - a queue append preserves already queued bytes
  PRECONDITION:
out is NULL or a valid byte_buf; topic points to topic_len bytes and payload to payload_len bytes when their lengths are nonzero.
  POSTCONDITION:
on success one complete PUBLISH packet follows the previously queued bytes; on -1 nothing changed; on -2 no complete packet was appended.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Not thread safe with respect to out; topic and payload are only read.
