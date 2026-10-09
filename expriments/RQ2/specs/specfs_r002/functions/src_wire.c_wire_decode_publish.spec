[PROMPT]
Decode a complete PUBLISH packet: DUP/QoS/RETAIN flags, a nonempty Topic Name without wildcards, a Packet Identifier exactly when QoS is 1 or 2, and an arbitrary binary payload.

[RELY]
STRUCT:
  - NAME:
struct wire_publish
    ROLE:
borrowed topic and payload slices plus the decoded flags
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
read the topic name length prefix and the Packet Identifier
  - NAME:
topic_name_validate
    KIND:
CALL
    ROLE:
require a nonempty UTF-8 topic name of at most 65535 bytes without 0x23 or 0x2B
VAR:
  - NAME:
WIRE_PACKET_PUBLISH
    ROLE:
expected control packet type 3
  - NAME:
WIRE_COMPLETE
    ROLE:
successful outcome
  - NAME:
WIRE_INVALID
    ROLE:
malformed PUBLISH outcome

[GUARANTEE]
RAW:
enum wire_status wire_decode_publish(const uint8_t *packet, size_t len, struct wire_publish *out)
NAME:
wire_decode_publish
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
struct wire_publish *
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
packet with len bytes holding exactly one framed PUBLISH (the caller obtained len from wire_decode_header.total_len); out receiving the flag bits and the borrowed topic and payload slices.
  ACTION:
1) If packet is NULL or out is NULL return WIRE_INVALID. 2) Zero *out. 3) wire_declared_body_len(packet, len, &header_len, &rl): if it is not WIRE_COMPLETE return WIRE_INVALID. 4) Fixed header: if (packet[0] >> 4) != WIRE_PACKET_PUBLISH return WIRE_INVALID. flags = packet[0] & 0x0F; out->dup = (flags >> 3) & 0x01; qos = (flags >> 1) & 0x03; out->retain = flags & 0x01; out->qos = qos. 5) if qos == 3 return WIRE_INVALID (both QoS bits set is a malformed fixed header, MQTT-3.3.1-4). 6) if out->dup == 1 && qos == 0 return WIRE_INVALID (MQTT-3.3.1-2: DUP must be 0 for QoS 0 messages). 7) Topic Name: pos = header_len; require len - pos >= 2 else WIRE_INVALID; tlen = wire_read_u16(packet + pos); pos += 2; require tlen >= 1 (MQTT-4.7.3-1: a Topic Name must be at least one character) and pos + tlen <= len (topic runs past the packet) else WIRE_INVALID; if topic_name_validate(packet + pos, tlen) != 1 return WIRE_INVALID (ill-formed UTF-8, or a wildcard 0x23/0x2B in a Topic Name). out->topic = packet + pos; out->topic_len = tlen; pos += tlen. 8) Packet Identifier: if qos == 1 or qos == 2 require pos + 2 <= len else WIRE_INVALID; pid = wire_read_u16(packet + pos); if pid == 0 return WIRE_INVALID (MQTT-2.3.1-1); pos += 2. The identifier is validated then skipped: this broker routes only QoS 0 and closes a connection that sends QoS 1 or 2, so no identifier is retained in struct wire_publish. 9) out->payload_len = len - pos; out->payload = (out->payload_len == 0) ? NULL : packet + pos. A zero-length payload is legal and is reported as length 0 (never as a missing payload). 10) Return WIRE_COMPLETE. Topic and payload slices borrow packet verbatim; the payload is never scanned for NUL and is never treated as a C string.
  OUTPUT:
WIRE_COMPLETE with *out filled: topic/topic_len and payload/payload_len are borrowed slices of packet preserving the exact bytes (including 0x00 and an empty payload), dup/qos/retain carry the decoded flag bits. WIRE_INVALID for NULL arguments, a short or over-long frame, a reserved QoS value of 3, DUP set with QoS 0, a missing/empty/over-long/ill-formed topic name, a wildcard in the topic name, or a missing or zero Packet Identifier on a QoS 1/2 message. WIRE_INCOMPLETE is never returned. QoS 1 and QoS 2 messages are well-formed input that decodes successfully; refusing them is connection state policy applied by the broker.
  INVARIANTS_USED:
    - PUBLISH fixed header flags carry DUP (bit 3), QoS (bits 2-1) and RETAIN (bit 0)
    - QoS 3 is malformed and DUP must be 0 for QoS 0 (MQTT-3.3.1-2, MQTT-3.3.1-4)
    - a Topic Name is nonempty, valid UTF-8 and contains no wildcard (MQTT-4.7.3-1)
    - a Packet Identifier is present exactly for QoS 1 and 2 and is nonzero (MQTT-2.3.1-1)
    - the payload is arbitrary binary data whose length is carried by the frame, not by a terminator
  PRECONDITION:
packet holds one complete PUBLISH frame of len bytes, len >= 2.
  POSTCONDITION:
*out borrows bytes of packet; packet is neither modified nor copied and no allocation happens.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Pure function of its arguments; packet must not be mutated concurrently.
