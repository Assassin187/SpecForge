[PROMPT]
LANG:
C99
ROLE:
MQTT 3.1.1 codec: Remaining Length framing with incomplete/complete/invalid outcomes, per-packet decoders returning borrowed slices, and atomic packet encoders appending to a caller-owned byte_buf.

[RELY]
DEPENDENCY:
  - src/wire.h
  - src/buffer.h
  - src/topic.h
SYSTEM_DEPENDENCY:
  - stddef.h
  - stdint.h
  - string.h

[GUARANTEE]
PATH:
src/wire.h
DEPENDENCY:
  - src/buffer.h
SYSTEM_DEPENDENCY:
  - stddef.h
  - stdint.h
DATA:
  - NAME:
WIRE_MAX_REMAINING_LENGTH
    KIND:
MACRO
    VISIBILITY:
PUBLIC
    VALUE:
268435455u
    ROLE:
Protocol maximum Remaining Length value
  - NAME:
WIRE_MAX_STRING_LENGTH
    KIND:
MACRO
    VISIBILITY:
PUBLIC
    VALUE:
65535u
    ROLE:
Maximum length of a length-prefixed UTF-8/binary field
  - NAME:
enum wire_status
    KIND:
TYPE
    VISIBILITY:
PUBLIC
    ROLE:
Decode outcome of one framing or packet decode call
    TYPE_SPEC:
      TYPE_KIND:
ENUM
      ENUM_VALUES:
        - NAME:
WIRE_INCOMPLETE
          VALUE:
0
          ROLE:
Whole packet not yet buffered
        - NAME:
WIRE_COMPLETE
          VALUE:
1
          ROLE:
Packet well formed and decoded
        - NAME:
WIRE_INVALID
          VALUE:
2
          ROLE:
Framing or control packet rule violated
  - NAME:
enum wire_packet_type
    KIND:
TYPE
    VISIBILITY:
PUBLIC
    ROLE:
Control packet type constants for the 4-bit type field
    TYPE_SPEC:
      TYPE_KIND:
ENUM
      ENUM_VALUES:
        - NAME:
WIRE_PACKET_CONNECT
          VALUE:
1
          ROLE:
CONNECT
        - NAME:
WIRE_PACKET_CONNACK
          VALUE:
2
          ROLE:
CONNACK
        - NAME:
WIRE_PACKET_PUBLISH
          VALUE:
3
          ROLE:
PUBLISH
        - NAME:
WIRE_PACKET_PUBACK
          VALUE:
4
          ROLE:
PUBACK
        - NAME:
WIRE_PACKET_PUBREC
          VALUE:
5
          ROLE:
PUBREC
        - NAME:
WIRE_PACKET_PUBREL
          VALUE:
6
          ROLE:
PUBREL
        - NAME:
WIRE_PACKET_PUBCOMP
          VALUE:
7
          ROLE:
PUBCOMP
        - NAME:
WIRE_PACKET_SUBSCRIBE
          VALUE:
8
          ROLE:
SUBSCRIBE
        - NAME:
WIRE_PACKET_SUBACK
          VALUE:
9
          ROLE:
SUBACK
        - NAME:
WIRE_PACKET_UNSUBSCRIBE
          VALUE:
10
          ROLE:
UNSUBSCRIBE
        - NAME:
WIRE_PACKET_UNSUBACK
          VALUE:
11
          ROLE:
UNSUBACK
        - NAME:
WIRE_PACKET_PINGREQ
          VALUE:
12
          ROLE:
PINGREQ
        - NAME:
WIRE_PACKET_PINGRESP
          VALUE:
13
          ROLE:
PINGRESP
        - NAME:
WIRE_PACKET_DISCONNECT
          VALUE:
14
          ROLE:
DISCONNECT
  - NAME:
struct wire_span
    KIND:
TYPE
    VISIBILITY:
PUBLIC
    ROLE:
Borrowed byte range inside a caller-owned packet buffer; no ownership
    TYPE_SPEC:
      TYPE_KIND:
STRUCT
      FIELDS:
        - NAME:
data
          TYPE:
const uint8_t *
          ROLE:
Borrowed pointer into the decoded packet
        - NAME:
len
          TYPE:
size_t
          ROLE:
Borrowed length in bytes
  - NAME:
struct wire_header
    KIND:
TYPE
    VISIBILITY:
PUBLIC
    ROLE:
Decoded fixed header: type, flags, body length, header bytes and total frame bytes
    TYPE_SPEC:
      TYPE_KIND:
STRUCT
      FIELDS:
        - NAME:
type
          TYPE:
uint8_t
          ROLE:
Control packet type 1..14
        - NAME:
flags
          TYPE:
uint8_t
          ROLE:
Low 4 bits of fixed header byte 1
        - NAME:
remaining_length
          TYPE:
size_t
          ROLE:
Bytes after the Remaining Length field
        - NAME:
header_len
          TYPE:
size_t
          ROLE:
Fixed header byte plus Remaining Length bytes (2..5)
        - NAME:
total_len
          TYPE:
size_t
          ROLE:
header_len + remaining_length
  - NAME:
struct wire_connect
    KIND:
TYPE
    VISIBILITY:
PUBLIC
    ROLE:
Decoded CONNECT fields as borrowed slices plus the policy inputs the caller needs
    TYPE_SPEC:
      TYPE_KIND:
STRUCT
      FIELDS:
        - NAME:
protocol_level
          TYPE:
uint8_t
          ROLE:
Protocol Level byte from the variable header
        - NAME:
clean_session
          TYPE:
uint8_t
          ROLE:
Clean Session flag bit (0 or 1)
        - NAME:
keep_alive
          TYPE:
uint16_t
          ROLE:
Keep Alive seconds, parsed and unused by this broker
        - NAME:
has_user_name
          TYPE:
uint8_t
          ROLE:
User Name Flag bit
        - NAME:
has_password
          TYPE:
uint8_t
          ROLE:
Password Flag bit
        - NAME:
has_will
          TYPE:
uint8_t
          ROLE:
Will Flag bit; a set bit makes the connection out of scope
        - NAME:
client_id
          TYPE:
struct wire_span
          ROLE:
Borrowed Client Identifier UTF-8 string
        - NAME:
user_name
          TYPE:
struct wire_span
          ROLE:
Borrowed user name, ignored
        - NAME:
password
          TYPE:
struct wire_span
          ROLE:
Borrowed password bytes, ignored
  - NAME:
struct wire_subscribe
    KIND:
TYPE
    VISIBILITY:
PUBLIC
    ROLE:
Decoded SUBSCRIBE Packet Identifier plus the borrowed filter/QoS payload
    TYPE_SPEC:
      TYPE_KIND:
STRUCT
      FIELDS:
        - NAME:
packet_id
          TYPE:
uint16_t
          ROLE:
Nonzero Packet Identifier to echo in SUBACK
        - NAME:
payload
          TYPE:
const uint8_t *
          ROLE:
Borrowed first filter length prefix
        - NAME:
payload_len
          TYPE:
size_t
          ROLE:
Bytes of filter/QoS payload
  - NAME:
struct wire_publish
    KIND:
TYPE
    VISIBILITY:
PUBLIC
    ROLE:
Decoded PUBLISH topic and payload slices with the DUP/QoS/RETAIN flag values
    TYPE_SPEC:
      TYPE_KIND:
STRUCT
      FIELDS:
        - NAME:
topic
          TYPE:
const uint8_t *
          ROLE:
Borrowed topic name bytes
        - NAME:
topic_len
          TYPE:
size_t
          ROLE:
Topic name length (1..65535)
        - NAME:
payload
          TYPE:
const uint8_t *
          ROLE:
Borrowed payload bytes after topic and Packet Identifier
        - NAME:
payload_len
          TYPE:
size_t
          ROLE:
Payload length; 0 is legal
        - NAME:
qos
          TYPE:
uint8_t
          ROLE:
QoS level 0, 1 or 2
        - NAME:
dup
          TYPE:
uint8_t
          ROLE:
DUP flag bit
        - NAME:
retain
          TYPE:
uint8_t
          ROLE:
RETAIN flag bit
INTERFACE:
  - SIGNATURE:
enum wire_status wire_decode_header(const uint8_t *buf, size_t len, struct wire_header *out)
    NAME:
wire_decode_header
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Frame one packet from a stream buffer
    VISIBILITY:
public
  - SIGNATURE:
enum wire_status wire_decode_connect(const uint8_t *packet, size_t len, struct wire_connect *out)
    NAME:
wire_decode_connect
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Validate and decode CONNECT
    VISIBILITY:
public
  - SIGNATURE:
enum wire_status wire_decode_subscribe(const uint8_t *packet, size_t len, struct wire_subscribe *out)
    NAME:
wire_decode_subscribe
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Validate and decode SUBSCRIBE
    VISIBILITY:
public
  - SIGNATURE:
int wire_subscribe_next(const struct wire_subscribe *sub, size_t index, struct wire_span *filter, uint8_t *qos)
    NAME:
wire_subscribe_next
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Iterate SUBSCRIBE filter/QoS pairs in order
    VISIBILITY:
public
  - SIGNATURE:
enum wire_status wire_decode_publish(const uint8_t *packet, size_t len, struct wire_publish *out)
    NAME:
wire_decode_publish
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Validate and decode PUBLISH
    VISIBILITY:
public
  - SIGNATURE:
enum wire_status wire_decode_empty(const uint8_t *packet, size_t len, uint8_t expect_type, size_t *consumed)
    NAME:
wire_decode_empty
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Validate a zero-length PINGREQ/DISCONNECT
    VISIBILITY:
public
  - SIGNATURE:
int wire_encode_connack(struct byte_buf *out, uint8_t ack_flags, uint8_t return_code)
    NAME:
wire_encode_connack
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Encode CONNACK
    VISIBILITY:
public
  - SIGNATURE:
int wire_encode_suback(struct byte_buf *out, uint16_t packet_id, const uint8_t *codes, size_t count)
    NAME:
wire_encode_suback
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Encode SUBACK with ordered return codes
    VISIBILITY:
public
  - SIGNATURE:
int wire_encode_publish(struct byte_buf *out, const uint8_t *topic, size_t topic_len, const uint8_t *payload, size_t payload_len, uint8_t qos, uint8_t dup, uint8_t retain)
    NAME:
wire_encode_publish
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Encode outbound QoS 0 PUBLISH
    VISIBILITY:
public
  - SIGNATURE:
int wire_encode_pingresp(struct byte_buf *out)
    NAME:
wire_encode_pingresp
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Encode PINGRESP
    VISIBILITY:
public

[SPECIFICATION]
SOURCE:
  PATH:
src/wire.c
  DEPENDENCY:
    - src/wire.h
    - src/buffer.h
    - src/topic.h
  SYSTEM_DEPENDENCY:
    - stddef.h
    - stdint.h
    - string.h
  DATA:
    - NAME:
WIRE_FIXED_HEADER_MIN_LEN
      KIND:
MACRO
      VISIBILITY:
PRIVATE
      VALUE:
2u
      ROLE:
Fixed header byte plus at least one Remaining Length byte
    - NAME:
WIRE_FIXED_HEADER_MAX_LEN
      KIND:
MACRO
      VISIBILITY:
PRIVATE
      VALUE:
5u
      ROLE:
Four Remaining Length bytes plus the fixed header byte
    - NAME:
WIRE_MAX_REMAINING_LENGTH_BYTES
      KIND:
MACRO
      VISIBILITY:
PRIVATE
      VALUE:
4u
      ROLE:
Maximum Remaining Length encoding width
  INTERFACE:
    - SIGNATURE:
static uint16_t wire_read_u16(const uint8_t *p)
      NAME:
wire_read_u16
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Big-endian 2-byte field read helper (private)
      VISIBILITY:
private
    - SIGNATURE:
static int wire_decode_string(const uint8_t *packet, size_t len, size_t *pos, struct wire_span *out, int validate_utf8)
      NAME:
wire_decode_string
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Read a 2-byte length prefixed field with bounds and optional UTF-8 check (private)
      VISIBILITY:
private
    - SIGNATURE:
static enum wire_status wire_declared_body_len(const uint8_t *packet, size_t len, size_t *header_len, size_t *remaining_length)
      NAME:
wire_declared_body_len
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Parse the Remaining Length field of a complete packet and check len equals the frame size (private)
      VISIBILITY:
private
    - SIGNATURE:
static int wire_write_remaining_length(struct byte_buf *out, size_t value)
      NAME:
wire_write_remaining_length
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Append the 7-bit-per-byte Remaining Length encoding, value <= 268435455 (private)
      VISIBILITY:
private
    - SIGNATURE:
enum wire_status wire_decode_header(const uint8_t *buf, size_t len, struct wire_header *out)
      NAME:
wire_decode_header
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Frame one packet, rejecting reserved types 0/15 and over-long Remaining Length
      VISIBILITY:
public
    - SIGNATURE:
enum wire_status wire_decode_connect(const uint8_t *packet, size_t len, struct wire_connect *out)
      NAME:
wire_decode_connect
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Decode CONNECT variable header and payload in protocol order with strict field presence
      VISIBILITY:
public
    - SIGNATURE:
enum wire_status wire_decode_subscribe(const uint8_t *packet, size_t len, struct wire_subscribe *out)
      NAME:
wire_decode_subscribe
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Decode SUBSCRIBE and validate every filter and QoS byte
      VISIBILITY:
public
    - SIGNATURE:
int wire_subscribe_next(const struct wire_subscribe *sub, size_t index, struct wire_span *filter, uint8_t *qos)
      NAME:
wire_subscribe_next
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Walk the validated payload pair list by index
      VISIBILITY:
public
    - SIGNATURE:
enum wire_status wire_decode_publish(const uint8_t *packet, size_t len, struct wire_publish *out)
      NAME:
wire_decode_publish
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Decode PUBLISH flags, topic name and payload
      VISIBILITY:
public
    - SIGNATURE:
enum wire_status wire_decode_empty(const uint8_t *packet, size_t len, uint8_t expect_type, size_t *consumed)
      NAME:
wire_decode_empty
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Validate type, flags and zero Remaining Length of PINGREQ/DISCONNECT
      VISIBILITY:
public
    - SIGNATURE:
int wire_encode_connack(struct byte_buf *out, uint8_t ack_flags, uint8_t return_code)
      NAME:
wire_encode_connack
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Append 0x20 0x02 ack_flags return_code
      VISIBILITY:
public
    - SIGNATURE:
int wire_encode_suback(struct byte_buf *out, uint16_t packet_id, const uint8_t *codes, size_t count)
      NAME:
wire_encode_suback
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Append SUBACK with the Packet Identifier echoed and codes in filter order
      VISIBILITY:
public
    - SIGNATURE:
int wire_encode_publish(struct byte_buf *out, const uint8_t *topic, size_t topic_len, const uint8_t *payload, size_t payload_len, uint8_t qos, uint8_t dup, uint8_t retain)
      NAME:
wire_encode_publish
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Append outbound QoS 0 PUBLISH preserving topic and payload bytes
      VISIBILITY:
public
    - SIGNATURE:
int wire_encode_pingresp(struct byte_buf *out)
      NAME:
wire_encode_pingresp
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Append 0xD0 0x00
      VISIBILITY:
public
PUBLIC_SYMBOLS:
  - NAME:
enum wire_status
    KIND:
TYPE
    ROLE:
Decode outcome
  - NAME:
enum wire_packet_type
    KIND:
TYPE
    ROLE:
Packet type constants
  - NAME:
struct wire_span
    KIND:
TYPE
    ROLE:
Borrowed slice
  - NAME:
struct wire_header
    KIND:
TYPE
    ROLE:
Decoded fixed header
  - NAME:
struct wire_connect
    KIND:
TYPE
    ROLE:
Decoded CONNECT
  - NAME:
struct wire_subscribe
    KIND:
TYPE
    ROLE:
Decoded SUBSCRIBE
  - NAME:
struct wire_publish
    KIND:
TYPE
    ROLE:
Decoded PUBLISH
  - NAME:
wire_decode_header
    KIND:
FUNC
    SIGNATURE:
enum wire_status wire_decode_header(const uint8_t *buf, size_t len, struct wire_header *out)
    ROLE:
Frame packet
  - NAME:
wire_decode_connect
    KIND:
FUNC
    SIGNATURE:
enum wire_status wire_decode_connect(const uint8_t *packet, size_t len, struct wire_connect *out)
    ROLE:
Decode CONNECT
  - NAME:
wire_decode_subscribe
    KIND:
FUNC
    SIGNATURE:
enum wire_status wire_decode_subscribe(const uint8_t *packet, size_t len, struct wire_subscribe *out)
    ROLE:
Decode SUBSCRIBE
  - NAME:
wire_subscribe_next
    KIND:
FUNC
    SIGNATURE:
int wire_subscribe_next(const struct wire_subscribe *sub, size_t index, struct wire_span *filter, uint8_t *qos)
    ROLE:
Iterate filters
  - NAME:
wire_decode_publish
    KIND:
FUNC
    SIGNATURE:
enum wire_status wire_decode_publish(const uint8_t *packet, size_t len, struct wire_publish *out)
    ROLE:
Decode PUBLISH
  - NAME:
wire_decode_empty
    KIND:
FUNC
    SIGNATURE:
enum wire_status wire_decode_empty(const uint8_t *packet, size_t len, uint8_t expect_type, size_t *consumed)
    ROLE:
Decode PINGREQ/DISCONNECT
  - NAME:
wire_encode_connack
    KIND:
FUNC
    SIGNATURE:
int wire_encode_connack(struct byte_buf *out, uint8_t ack_flags, uint8_t return_code)
    ROLE:
Encode CONNACK
  - NAME:
wire_encode_suback
    KIND:
FUNC
    SIGNATURE:
int wire_encode_suback(struct byte_buf *out, uint16_t packet_id, const uint8_t *codes, size_t count)
    ROLE:
Encode SUBACK
  - NAME:
wire_encode_publish
    KIND:
FUNC
    SIGNATURE:
int wire_encode_publish(struct byte_buf *out, const uint8_t *topic, size_t topic_len, const uint8_t *payload, size_t payload_len, uint8_t qos, uint8_t dup, uint8_t retain)
    ROLE:
Encode PUBLISH
  - NAME:
wire_encode_pingresp
    KIND:
FUNC
    SIGNATURE:
int wire_encode_pingresp(struct byte_buf *out)
    ROLE:
Encode PINGRESP
