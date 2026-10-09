[PROMPT]
Decode a complete CONNECT packet: validate the fixed header flags, protocol name, connect flags and field presence, and borrow the client identifier, user name and password slices.

[RELY]
STRUCT:
  - NAME:
struct wire_connect
    ROLE:
decoded CONNECT fields, all slices borrowed from packet
  - NAME:
struct wire_span
    ROLE:
borrowed slice of client identifier, user name or password
FUNC:
  - NAME:
wire_declared_body_len
    KIND:
CALL
    ROLE:
confirm len is exactly the declared frame size
  - NAME:
wire_decode_string
    KIND:
CALL
    ROLE:
read the length-prefixed protocol name, client id, will topic, user name and password fields
  - NAME:
wire_read_u16
    KIND:
CALL
    ROLE:
read the Keep Alive field
VAR:
  - NAME:
WIRE_PACKET_CONNECT
    ROLE:
control packet type 1
  - NAME:
WIRE_COMPLETE
    ROLE:
successful outcome
  - NAME:
WIRE_INVALID
    ROLE:
malformed CONNECT outcome

[GUARANTEE]
RAW:
enum wire_status wire_decode_connect(const uint8_t *packet, size_t len, struct wire_connect *out)
NAME:
wire_decode_connect
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
struct wire_connect *
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
packet with len bytes holding exactly one framed CONNECT (the caller obtained len from wire_decode_header.total_len); out receiving the decoded fields.
  ACTION:
1) If packet is NULL or out is NULL return WIRE_INVALID. 2) Zero *out. 3) wire_declared_body_len(packet, len, &header_len, &rl); if it is not WIRE_COMPLETE return WIRE_INVALID (a decoder never runs on a partial packet, and a short buffer must not be reported as incomplete). 4) Fixed header flags: if (packet[0] & 0x0F) != 0 return WIRE_INVALID (CONNECT reserved flags, fact F003). 5) pos = header_len. Protocol name: wire_decode_string(packet, len, &pos, &span, 1) must return 1, span.len must be 4 and the bytes must be 4D 51 54 54 ('MQTT'); otherwise return WIRE_INVALID. 6) Protocol level: pos must be < len, else WIRE_INVALID; out->protocol_level = packet[pos++]. The value is stored verbatim: a level other than 4 is well formed and is refused later with CONNACK 0x01, never here. 7) Connect flags: require 2 bytes; flags = packet[pos++]; if (flags & 0x01) return WIRE_INVALID (reserved bit 0). will_qos = (flags >> 3) & 0x03; if will_qos == 3 return WIRE_INVALID (reserved Will QoS). If (flags & 0x40) and !(flags & 0x80) return WIRE_INVALID (password flag requires the user name flag). If ((flags >> 2) & 0x01) == 0 and (will_qos != 0 || (flags & 0x20) != 0) return WIRE_INVALID (with the Will flag clear the Will QoS and Will Retain flags MUST be zero, fact F019; such a packet is malformed and must be closed without a CONNACK, facts F017/F058). out->clean_session = (flags >> 1) & 0x01; out->has_will = (flags >> 2) & 0x01; out->has_password = (flags >> 6) & 0x01; out->has_user_name = (flags >> 7) & 0x01. 8) Keep Alive: require 2 bytes, out->keep_alive = wire_read_u16(packet + pos), pos += 2. 9) Client identifier: wire_decode_string(..., 1) must return 1; store the span in out->client_id (empty identifier is stored and refused by broker policy, so the wire decoder does not invent a length rule). 10) If out->has_will: read the Will Topic with UTF-8 validation and the Will Message as a binary field (validate_utf8 = 0); both must decode, both are validated then skipped - the broker does not accept a Will, so no will bytes are retained. 11) If out->has_user_name: decode the user name with UTF-8 validation into out->user_name. 12) If out->has_password: decode the password as a binary field into out->password. 13) If pos != len return WIRE_INVALID (bytes after the last field of the packet). 14) Return WIRE_COMPLETE. Every borrowed span points into packet, which the caller must keep alive until it has copied what it needs (only the client identifier is copied, by session_set_client_id).
  OUTPUT:
WIRE_COMPLETE with *out filled from the packet: protocol_level, clean_session, keep_alive, has_user_name, has_password, has_will and the borrowed client_id/user_name/password spans; empty fields are reported as spans of length 0 rather than as NULL. WIRE_INVALID for reserved flags, a protocol name other than MQTT, a reserved Will QoS, non-zero Will QoS or Will Retain bits with the Will flag clear (fact F019), a password flag without a user name flag, any truncated field, any ill-formed UTF-8 string field, or trailing bytes. The function never returns WIRE_INCOMPLETE and performs no policy decision: level != 4, an empty/non-ASCII client identifier, a Will or Clean Session = 0 are all decoded successfully so that the caller can answer with the mandated CONNACK or close with the mandated silence.
  INVARIANTS_USED:
    - CONNECT is the first packet of a connection
    - the CONNECT fixed header flags are 0
    - the protocol name is exactly MQTT
    - reserved CONNECT flag bit 0 and Will QoS 3 are malformed
    - a password flag without a user name flag is malformed
    - a clear Will flag requires Will QoS 0 and Will Retain 0 (fact F019)
    - fields are present exactly when their flag is set and in protocol order
  PRECONDITION:
packet holds one complete CONNECT frame of len bytes.
  POSTCONDITION:
*out describes the packet and every span is a borrowed slice inside packet; nothing is allocated.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Pure function of its arguments; the packet buffer must not be mutated concurrently.
