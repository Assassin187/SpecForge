#include "protocol/mqtt_decoder.h"

#include <stdlib.h>
#include <string.h>

// Private type for Remaining Length parsing
typedef struct remaining_length {
    size_t value;
    size_t bytes;
} remaining_length_t;

static bool try_parse_remaining_length(const uint8_t* buf, size_t buf_len, size_t start, remaining_length_t* out);
static bool read_u16(const uint8_t* body, size_t body_len, size_t* pos, uint16_t* out);
static bool read_string(const uint8_t* body, size_t body_len, size_t* pos, char** out);
static bool decode_one(uint8_t header1, const uint8_t* body, size_t body_len, mqtt_packet_t* out);

bool mqtt_decoder_feed(const uint8_t* buffer, size_t buffer_len, size_t* out_consumed, mqtt_on_packet_fn on_packet, void* user) {
    if (!buffer || !out_consumed || !on_packet) {
        return false;
    }

    *out_consumed = 0;
    size_t offset = 0;

    while (offset < buffer_len) {
        // Need at least 2 bytes: 1 for fixed header, 1 for Remaining Length
        if (offset + 2 > buffer_len) {
            break;
        }

        uint8_t header1 = buffer[offset];
        remaining_length_t rl = {0};
        if (!try_parse_remaining_length(buffer, buffer_len, offset + 1, &rl)) {
            break;
        }

        size_t total_len = 1 + rl.bytes + rl.value;
        if (offset + total_len > buffer_len) {
            break;
        }

        mqtt_packet_t pkt = {0};
        if (!decode_one(header1, buffer + offset + 1 + rl.bytes, rl.value, &pkt)) {
            return false;
        }

        on_packet(user, &pkt);
        mqtt_packet_free(&pkt);

        offset += total_len;
    }

    *out_consumed = offset;
    return true;
}

static bool try_parse_remaining_length(const uint8_t* buf, size_t buf_len, size_t start, remaining_length_t* out) {
    size_t multiplier = 1;
    size_t value = 0;
    size_t bytes = 0;
    size_t i = start;

    while (i < buf_len && bytes < 4) {
        uint8_t encoded_byte = buf[i];
        value += (encoded_byte & 0x7F) * multiplier;
        bytes++;

        if ((encoded_byte & 0x80) == 0) {
            out->value = value;
            out->bytes = bytes;
            return true;
        }

        multiplier *= 128;
        if (multiplier > 128 * 128 * 128) {
            break;
        }
        i++;
    }

    return false;
}

static bool read_u16(const uint8_t* body, size_t body_len, size_t* pos, uint16_t* out) {
    if (*pos + 2 > body_len) {
        return false;
    }
    *out = ((uint16_t)body[*pos] << 8) | (uint16_t)body[*pos + 1];
    *pos += 2;
    return true;
}

static bool read_string(const uint8_t* body, size_t body_len, size_t* pos, char** out) {
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

static bool decode_one(uint8_t header1, const uint8_t* body, size_t body_len, mqtt_packet_t* out) {
    mqtt_packet_type_t pkt_type = (mqtt_packet_type_t)(header1 >> 4);
    out->type = pkt_type;

    switch (pkt_type) {
        case MQTT_PKT_CONNECT: {
            size_t pos = 0;
            char* protocol_name = NULL;
            if (!read_string(body, body_len, &pos, &protocol_name)) {
                return false;
            }
            if (strcmp(protocol_name, "MQTT") != 0) {
                free(protocol_name);
                return false;
            }
            free(protocol_name);

            if (pos + 1 > body_len) {
                return false;
            }
            uint8_t protocol_level = body[pos++];
            if (protocol_level != 4) {
                return false;
            }

            if (pos + 1 > body_len) {
                return false;
            }
            uint8_t connect_flags = body[pos++];

            if (pos + 2 > body_len) {
                return false;
            }
            uint16_t keep_alive;
            if (!read_u16(body, body_len, &pos, &keep_alive)) {
                return false;
            }

            char* client_id = NULL;
            if (!read_string(body, body_len, &pos, &client_id)) {
                return false;
            }

            // Skip Will Topic and Will Message if present
            if (connect_flags & 0x04) {
                uint16_t will_topic_len;
                if (!read_u16(body, body_len, &pos, &will_topic_len)) {
                    free(client_id);
                    return false;
                }
                if (pos + will_topic_len > body_len) {
                    free(client_id);
                    return false;
                }
                pos += will_topic_len;

                uint16_t will_message_len;
                if (!read_u16(body, body_len, &pos, &will_message_len)) {
                    free(client_id);
                    return false;
                }
                if (pos + will_message_len > body_len) {
                    free(client_id);
                    return false;
                }
                pos += will_message_len;
            }

            // Skip Username if present
            if (connect_flags & 0x80) {
                uint16_t username_len;
                if (!read_u16(body, body_len, &pos, &username_len)) {
                    free(client_id);
                    return false;
                }
                if (pos + username_len > body_len) {
                    free(client_id);
                    return false;
                }
                pos += username_len;
            }

            // Skip Password if present
            if (connect_flags & 0x40) {
                uint16_t password_len;
                if (!read_u16(body, body_len, &pos, &password_len)) {
                    free(client_id);
                    return false;
                }
                if (pos + password_len > body_len) {
                    free(client_id);
                    return false;
                }
                pos += password_len;
            }

            out->v.connect.client_id = client_id;
            out->v.connect.keep_alive = keep_alive;
            out->v.connect.clean_session = (connect_flags & 0x02) != 0;
            return true;
        }

        case MQTT_PKT_PUBLISH: {
            size_t pos = 0;
            char* topic_name = NULL;
            if (!read_string(body, body_len, &pos, &topic_name)) {
                return false;
            }

            uint8_t qos = (header1 >> 1) & 0x03;
            if (qos > 2) {
                free(topic_name);
                return false;
            }

            bool has_packet_id = (qos > 0);
            uint16_t packet_id = 0;
            if (has_packet_id) {
                if (!read_u16(body, body_len, &pos, &packet_id)) {
                    free(topic_name);
                    return false;
                }
            }

            size_t payload_len = body_len - pos;
            uint8_t* payload = NULL;
            if (payload_len > 0) {
                payload = malloc(payload_len);
                if (!payload) {
                    free(topic_name);
                    return false;
                }
                memcpy(payload, body + pos, payload_len);
            }

            out->v.publish.topic_name = topic_name;
            out->v.publish.payload = payload;
            out->v.publish.payload_len = payload_len;
            out->v.publish.qos = qos;
            out->v.publish.retain = (header1 & 0x01) != 0;
            out->v.publish.dup = ((header1 >> 3) & 0x01) != 0;
            out->v.publish.has_packet_id = has_packet_id;
            out->v.publish.packet_id = packet_id;
            return true;
        }

        case MQTT_PKT_SUBSCRIBE: {
            if ((header1 & 0x0F) != 0x02) {
                return false;
            }

            size_t pos = 0;
            uint16_t packet_id;
            if (!read_u16(body, body_len, &pos, &packet_id)) {
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

            mqtt_subscribe_topic_t* topics = malloc(topic_count * sizeof(mqtt_subscribe_topic_t));
            if (!topics) {
                return false;
            }

            for (size_t i = 0; i < topic_count; i++) {
                char* filter = NULL;
                if (!read_string(body, body_len, &pos, &filter)) {
                    // Free previously allocated filters
                    for (size_t j = 0; j < i; j++) {
                        free(topics[j].filter);
                    }
                    free(topics);
                    return false;
                }
                topics[i].filter = filter;

                if (pos >= body_len) {
                    free(filter);
                    for (size_t j = 0; j < i; j++) {
                        free(topics[j].filter);
                    }
                    free(topics);
                    return false;
                }
                topics[i].qos = body[pos++] & 0x03;
            }

            out->v.subscribe.packet_id = packet_id;
            out->v.subscribe.topics = topics;
            out->v.subscribe.topic_count = topic_count;
            return true;
        }

        case MQTT_PKT_PINGREQ:
        case MQTT_PKT_DISCONNECT:
            // No variable header or payload; nothing to initialize in union
            return true;

        default:
            return false;
    }
}
