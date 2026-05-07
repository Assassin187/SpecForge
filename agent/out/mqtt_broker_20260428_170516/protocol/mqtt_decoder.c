#include "protocol/mqtt_decoder.h"

#include <stdlib.h>
#include <string.h>

// Private type for Remaining Length parsing result
typedef struct remaining_length {
    size_t value;
    size_t bytes;
} remaining_length_t;

static bool try_parse_remaining_length(const uint8_t* buf, size_t buf_len, size_t start, remaining_length_t* out);
static bool read_u16(const uint8_t* body, size_t body_len, size_t* pos, uint16_t* out);
static bool read_string(const uint8_t* body, size_t body_len, size_t* pos, char** out);
static bool decode_one(uint8_t header1, const uint8_t* body, size_t body_len, mqtt_packet_t* out);

bool mqtt_decoder_feed(const uint8_t* buffer, size_t buffer_len, size_t* out_consumed, mqtt_on_packet_fn on_packet, void* user)
{
    if (!buffer || !out_consumed || !on_packet) {
        return false;
    }

    *out_consumed = 0;

    size_t offset = 0;
    while (offset < buffer_len) {
        // Need at least 2 bytes: 1 for fixed header first byte, 1 for at least one Remaining Length byte
        if (offset + 2 > buffer_len) {
            break;
        }

        // Parse Remaining Length starting from offset+1
        remaining_length_t rl;
        if (!try_parse_remaining_length(buffer, buffer_len, offset + 1, &rl)) {
            // Not enough bytes to parse Remaining Length yet
            break;
        }

        size_t total_packet_len = 1 + rl.bytes + rl.value;
        if (offset + total_packet_len > buffer_len) {
            // Full packet not available yet
            break;
        }

        // We have a complete packet
        uint8_t header1 = buffer[offset];
        const uint8_t* body = buffer + offset + 1 + rl.bytes;
        size_t body_len = rl.value;

        mqtt_packet_t pkt = {0};
        if (!decode_one(header1, body, body_len, &pkt)) {
            // Malformed packet - stop processing
            return false;
        }

        // Callback to user
        on_packet(user, &pkt);

        // Free the packet after callback
        mqtt_packet_free(&pkt);

        // Advance consumed count
        offset += total_packet_len;
    }

    *out_consumed = offset;
    return true;
}

static bool try_parse_remaining_length(const uint8_t* buf, size_t buf_len, size_t start, remaining_length_t* out)
{
    size_t multiplier = 1;
    size_t value = 0;
    size_t encoded_byte_count = 0;

    for (size_t i = 0; i < 4; ++i) {
        if (start + i >= buf_len) {
            return false; // Not enough bytes
        }
        encoded_byte_count++;

        uint8_t encoded_byte = buf[start + i];
        value += (encoded_byte & 0x7F) * multiplier;

        if ((encoded_byte & 0x80) == 0) {
            // Last byte of encoding
            out->value = value;
            out->bytes = encoded_byte_count;
            return true;
        }

        multiplier *= 128;
        if (multiplier > 128 * 128 * 128) {
            // MQTT spec allows max 4 bytes, and value must be <= 268435455
            return false;
        }
    }

    // More than 4 bytes is invalid per MQTT spec
    return false;
}

static bool read_u16(const uint8_t* body, size_t body_len, size_t* pos, uint16_t* out)
{
    if (*pos + 2 > body_len) {
        return false;
    }

    *out = ((uint16_t)body[*pos] << 8) | body[*pos + 1];
    *pos += 2;
    return true;
}

static bool read_string(const uint8_t* body, size_t body_len, size_t* pos, char** out)
{
    uint16_t len;
    if (!read_u16(body, body_len, pos, &len)) {
        return false;
    }

    if (*pos + len > body_len) {
        return false;
    }

    char* str = malloc(len + 1);
    if (!str) {
        return false;
    }

    memcpy(str, body + *pos, len);
    str[len] = '\0';
    *pos += len;
    *out = str;
    return true;
}

static bool decode_one(uint8_t header1, const uint8_t* body, size_t body_len, mqtt_packet_t* out)
{
    uint8_t packet_type = (header1 >> 4) & 0x0F;
    out->type = packet_type;

    switch (packet_type) {
        case MQTT_PKT_CONNECT: {
            size_t pos = 0;

            // Protocol name
            char* protocol_name = NULL;
            if (!read_string(body, body_len, &pos, &protocol_name)) {
                return false;
            }

            // Only support "MQTT" (protocol level 4)
            if (strcmp(protocol_name, "MQTT") != 0) {
                free(protocol_name);
                return false;
            }
            free(protocol_name);

            // Protocol level
            if (pos + 1 > body_len) {
                return false;
            }
            uint8_t protocol_level = body[pos++];
            if (protocol_level != 4) {
                return false;
            }

            // Connect flags
            if (pos + 1 > body_len) {
                return false;
            }
            uint8_t connect_flags = body[pos++];

            // Clean session
            out->data.connect.clean_session = (connect_flags & 0x02) != 0;

            // Keep alive
            uint16_t keep_alive;
            if (!read_u16(body, body_len, &pos, &keep_alive)) {
                return false;
            }
            out->data.connect.keep_alive = keep_alive;

            // Client ID
            if (!read_string(body, body_len, &pos, &out->data.connect.client_id)) {
                return false;
            }

            // Will flag
            if (connect_flags & 0x04) {
                out->data.connect.will_flag = true;

                // Will QoS
                out->data.connect.will_qos = (connect_flags >> 3) & 0x03;
                if (out->data.connect.will_qos > 2) {
                    free(out->data.connect.client_id);
                    return false;
                }

                // Will retain
                out->data.connect.will_retain = (connect_flags & 0x20) != 0;

                // Will topic
                if (!read_string(body, body_len, &pos, &out->data.connect.will_topic)) {
                    free(out->data.connect.client_id);
                    return false;
                }

                // Will message
                if (!read_string(body, body_len, &pos, &out->data.connect.will_message)) {
                    free(out->data.connect.client_id);
                    free(out->data.connect.will_topic);
                    return false;
                }
            } else {
                out->data.connect.will_flag = false;
                out->data.connect.will_topic = NULL;
                out->data.connect.will_message = NULL;
            }

            // Username
            if (connect_flags & 0x80) {
                if (!read_string(body, body_len, &pos, &out->data.connect.username)) {
                    free(out->data.connect.client_id);
                    if (out->data.connect.will_flag) {
                        free(out->data.connect.will_topic);
                        free(out->data.connect.will_message);
                    }
                    return false;
                }
            } else {
                out->data.connect.username = NULL;
            }

            // Password
            if (connect_flags & 0x40) {
                if (!read_string(body, body_len, &pos, &out->data.connect.password)) {
                    free(out->data.connect.client_id);
                    if (out->data.connect.will_flag) {
                        free(out->data.connect.will_topic);
                        free(out->data.connect.will_message);
                    }
                    if (out->data.connect.username) {
                        free(out->data.connect.username);
                    }
                    return false;
                }
            } else {
                out->data.connect.password = NULL;
            }

            break;
        }

        case MQTT_PKT_PUBLISH: {
            size_t pos = 0;
            if (!read_string(body, body_len, &pos, &out->data.publish.topic_name)) {
                return false;
            }

            // Check QoS level from header
            uint8_t qos = (header1 >> 1) & 0x03;
            if (qos > 2) {
                free(out->data.publish.topic_name);
                return false;
            }
            out->data.publish.qos = qos;

            out->data.publish.retain = (header1 & 0x01) != 0;
            out->data.publish.dup = (header1 & 0x08) != 0;

            if (qos > 0) {
                if (!read_u16(body, body_len, &pos, &out->data.publish.packet_id)) {
                    free(out->data.publish.topic_name);
                    return false;
                }
            } else {
                out->data.publish.packet_id = 0;
            }

            // Payload
            out->data.publish.payload_len = body_len - pos;
            if (out->data.publish.payload_len > 0) {
                out->data.publish.payload = malloc(out->data.publish.payload_len);
                if (!out->data.publish.payload) {
                    free(out->data.publish.topic_name);
                    return false;
                }
                memcpy(out->data.publish.payload, body + pos, out->data.publish.payload_len);
            } else {
                out->data.publish.payload = NULL;
            }

            break;
        }

        case MQTT_PKT_SUBSCRIBE: {
            size_t pos = 0;

            // Packet ID
            if (!read_u16(body, body_len, &pos, &out->data.subscribe.packet_id)) {
                return false;
            }

            // Count topics
            size_t topic_count = 0;
            size_t temp_pos = pos;
            while (temp_pos < body_len) {
                uint16_t topic_len;
                if (!read_u16(body, body_len, &temp_pos, &topic_len)) {
                    return false;
                }
                if (temp_pos + topic_len + 1 > body_len) {
                    return false;
                }
                temp_pos += topic_len + 1; // +1 for QoS byte
                topic_count++;
            }

            if (topic_count == 0) {
                return false;
            }

            out->data.subscribe.topic_count = topic_count;
            out->data.subscribe.topics = calloc(topic_count, sizeof(mqtt_topic_filter_t));
            if (!out->data.subscribe.topics) {
                return false;
            }

            for (size_t i = 0; i < topic_count; ++i) {
                if (!read_string(body, body_len, &pos, &out->data.subscribe.topics[i].filter)) {
                    // Cleanup on failure
                    for (size_t j = 0; j < i; ++j) {
                        free(out->data.subscribe.topics[j].filter);
                    }
                    free(out->data.subscribe.topics);
                    return false;
                }

                if (pos >= body_len) {
                    free(out->data.subscribe.topics[i].filter);
                    for (size_t j = 0; j < i; ++j) {
                        free(out->data.subscribe.topics[j].filter);
                    }
                    free(out->data.subscribe.topics);
                    return false;
                }

                uint8_t qos = body[pos++];
                if (qos > 2) {
                    free(out->data.subscribe.topics[i].filter);
                    for (size_t j = 0; j < i; ++j) {
                        free(out->data.subscribe.topics[j].filter);
                    }
                    free(out->data.subscribe.topics);
                    return false;
                }
                out->data.subscribe.topics[i].qos = qos;
            }

            break;
        }

        case MQTT_PKT_PINGREQ:
            // No payload
            break;

        case MQTT_PKT_DISCONNECT:
            // No payload
            break;

        default:
            // Unsupported packet type
            return false;
    }

    return true;
}
