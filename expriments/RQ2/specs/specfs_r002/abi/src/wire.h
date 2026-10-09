/*
 * wire.h - MQTT 3.1.1 Control Packet framing, decoding and encoding.
 *
 * All decoders operate on complete packets as framed from the stream: they
 * never read past the packet, and every decoded variable-length value is a
 * borrowed slice pointing into the caller's packet buffer. The packet buffer
 * must stay alive and unmodified as long as the borrowed slices are used.
 *
 * Decoders return:
 *   WIRE_INCOMPLETE - the buffer does not yet hold the whole packet (only
 *                     wire_decode_header can report this);
 *   WIRE_COMPLETE   - the packet is well formed and fully decoded;
 *   WIRE_INVALID    - the packet violates framing or control packet rules.
 *
 * Encoders append a complete packet to a caller-owned byte_buf. They return
 * 0 on success, -1 for an invalid argument and -2 when growing the output
 * failed; the output buffer is left unchanged on every failure.
 */
#ifndef MQTT_BROKER_WIRE_H
#define MQTT_BROKER_WIRE_H

#include <stddef.h>
#include <stdint.h>

#include "buffer.h"

/* Maximum Remaining Length value (4-byte variable length encoding, 7 bits each). */
#define WIRE_MAX_REMAINING_LENGTH 268435455u
/* Maximum length of a length-prefixed UTF-8 string / binary field. */
#define WIRE_MAX_STRING_LENGTH 65535u

enum wire_status {
    WIRE_INCOMPLETE = 0,
    WIRE_COMPLETE = 1,
    WIRE_INVALID = 2
};

enum wire_packet_type {
    WIRE_PACKET_CONNECT = 1,
    WIRE_PACKET_CONNACK = 2,
    WIRE_PACKET_PUBLISH = 3,
    WIRE_PACKET_PUBACK = 4,
    WIRE_PACKET_PUBREC = 5,
    WIRE_PACKET_PUBREL = 6,
    WIRE_PACKET_PUBCOMP = 7,
    WIRE_PACKET_SUBSCRIBE = 8,
    WIRE_PACKET_SUBACK = 9,
    WIRE_PACKET_UNSUBSCRIBE = 10,
    WIRE_PACKET_UNSUBACK = 11,
    WIRE_PACKET_PINGREQ = 12,
    WIRE_PACKET_PINGRESP = 13,
    WIRE_PACKET_DISCONNECT = 14
};

/* Borrowed view of a byte range inside a caller-owned packet buffer. */
struct wire_span {
    const uint8_t *data;
    size_t len;
};

/* Decoded fixed header of one packet. */
struct wire_header {
    uint8_t type;              /* 1..14 */
    uint8_t flags;             /* low 4 bits of the fixed header byte */
    size_t remaining_length;   /* bytes after the Remaining Length field */
    size_t header_len;         /* 2..5: fixed header byte plus Remaining Length bytes */
    size_t total_len;          /* header_len + remaining_length */
};

/* Decoded CONNECT fields; all slices are borrowed from the packet. */
struct wire_connect {
    uint8_t protocol_level;
    uint8_t clean_session;
    uint16_t keep_alive;
    uint8_t has_user_name;
    uint8_t has_password;
    uint8_t has_will;    /* set means the connection is outside this broker's scope */
    struct wire_span client_id;
    struct wire_span user_name;  /* borrowed UTF-8 string, ignored by this broker */
    struct wire_span password;   /* borrowed binary data, ignored by this broker */
};

/* Decoded SUBSCRIBE header plus the borrowed filter/QoS payload. */
struct wire_subscribe {
    uint16_t packet_id;
    const uint8_t *payload;   /* borrowed: first filter length prefix */
    size_t payload_len;       /* bytes of the filter/QoS payload */
};

/* Decoded PUBLISH topic and payload. */
struct wire_publish {
    const uint8_t *topic;
    size_t topic_len;
    const uint8_t *payload;
    size_t payload_len;
    uint8_t qos;
    uint8_t dup;
    uint8_t retain;
};

/*
 * Frame one packet from a stream buffer. The fixed header (type, flags,
 * Remaining Length field) is parsed first; when it parses, *out is filled with
 * remaining_length, header_len (2..5) and total_len = header_len +
 * remaining_length. Returns WIRE_COMPLETE once len is at least total_len, and
 * WIRE_INCOMPLETE while fewer bytes are buffered (<out is still filled, so the
 * caller can size its read buffer). Returns WIRE_INCOMPLETE when the Remaining
 * Length field itself is unfinished, and WIRE_INVALID for a reserved packet
 * type (0, 15) or a Remaining Length field longer than 4 bytes; in both of
 * those cases *out is left untouched.
 */
enum wire_status wire_decode_header(const uint8_t *buf, size_t len,
                                    struct wire_header *out);

/*
 * Decode and validate a complete CONNECT packet (packet[0..len-1]). Structural
 * and protocol-name violations return WIRE_INVALID; the caller closes without
 * a CONNACK. protocol_level, clean_session and the client id are reported in
 * *out so the caller can apply the CONNACK return code policy.
 */
enum wire_status wire_decode_connect(const uint8_t *packet, size_t len,
                                     struct wire_connect *out);

/*
 * Decode and validate a complete SUBSCRIBE packet: flags 0x2, nonzero Packet
 * Identifier, at least one filter/QoS pair, each filter a legal Topic Filter
 * and each QoS byte with bits 7-2 zero and a requested QoS of 0, 1 or 2.
 */
enum wire_status wire_decode_subscribe(const uint8_t *packet, size_t len,
                                       struct wire_subscribe *out);

/*
 * Read the index-th filter/QoS pair from a decoded SUBSCRIBE payload, in order.
 * Returns 1 and fills *filter (borrowed, points into the packet) and *qos on
 * success, 0 when index is past the last pair.
 */
int wire_subscribe_next(const struct wire_subscribe *sub, size_t index,
                        struct wire_span *filter, uint8_t *qos);

/*
 * Decode and validate a complete PUBLISH packet: flags carry DUP/QoS/RETAIN,
 * the topic name is a legal nonempty Topic Name without wildcards, and a
 * Packet Identifier is present exactly when QoS is 1 or 2. QoS 3 and a DUP
 * flag combined with QoS 0 are WIRE_INVALID. The decoded payload keeps
 * arbitrary binary bytes including 0x00.
 */
enum wire_status wire_decode_publish(const uint8_t *packet, size_t len,
                                     struct wire_publish *out);

/*
 * Validate a complete zero-length PINGREQ/DISCONNECT packet whose type must be
 * expect_type and whose flags and Remaining Length must both be 0. On
 * WIRE_COMPLETE, *consumed is the packet length in bytes (2).
 */
enum wire_status wire_decode_empty(const uint8_t *packet, size_t len,
                                   uint8_t expect_type, size_t *consumed);

/*
 * CONNACK: 0x20, Remaining Length 2, acknowledge flags byte and return code.
 * ack_flags bit 0 is the Session Present flag; other bits must be 0.
 */
int wire_encode_connack(struct byte_buf *out, uint8_t ack_flags,
                        uint8_t return_code);

/*
 * SUBACK: 0x90, Remaining Length 2+count, the SUBSCRIBE Packet Identifier and
 * one return code per filter in the order the filters appeared. count must be
 * at least 1.
 */
int wire_encode_suback(struct byte_buf *out, uint16_t packet_id,
                       const uint8_t *codes, size_t count);

/*
 * PUBLISH: validated topic name (1..65535 bytes, no wildcards) followed by
 * the raw payload. Broker fan-out is always QoS 0, so this encoder has no
 * Packet Identifier parameter: qos must be 0 and dup must be 0 (a DUP flag
 * with QoS 0 is a protocol error); retain may be 0 or 1. Any other value
 * returns -1.
 */
int wire_encode_publish(struct byte_buf *out, const uint8_t *topic,
                        size_t topic_len, const uint8_t *payload,
                        size_t payload_len, uint8_t qos, uint8_t dup,
                        uint8_t retain);

/* PINGRESP: 0xD0, Remaining Length 0. */
int wire_encode_pingresp(struct byte_buf *out);

#endif /* MQTT_BROKER_WIRE_H */
