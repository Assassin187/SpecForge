#include "mqtt_decoder.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>

typedef struct remaining_length {
    size_t value;
    size_t bytes;
} remaining_length_t;

static bool try_parse_remaining_length(const uint8_t* buf, size_t buf_len, size_t start, remaining_length_t* out) {
    size_t multiplier = 1;
    size_t value = 0;
    size_t i = 0;

    while (1) {
        if (start + i >= buf_len) {
            return false;
        }
        const uint8_t encoded = buf[start + i];
        value += (encoded & 0x7F) * multiplier;
        multiplier *= 128;
        i++;
        if ((encoded & 0x80) == 0) {
            break;
        }
        if (i >= 4) {
            return false;
        }
    }

    out->value = value;
    out->bytes = i;
    return true;
}

static bool read_u16(const uint8_t* body, size_t body_len, size_t* pos, uint16_t* out) {
    if (*pos + 2 > body_len) {
        return false;
    }
    *out = (uint16_t)((body[*pos] << 8) | body[*pos + 1]);
    *pos += 2;
    return true;
}

static bool read_string(const uint8_t* body, size_t body_len, size_t* pos, char** out) {
    uint16_t len = 0;
    if (!read_u16(body, body_len, pos, &len)) {
        return false;
    }
    if (*pos + len > body_len) {
        return false;
    }

    char* s = (char*)malloc((size_t)len + 1);
    if (!s) {
        return false;
    }
    memcpy(s, body + *pos, len);
    s[len] = '\0';
    *pos += len;
    *out = s;
    return true;
}

static bool decode_one(uint8_t header1, const uint8_t* body, size_t body_len, mqtt_packet_t* out) {
    memset(out, 0, sizeof(*out));
    const mqtt_packet_type_t type = (mqtt_packet_type_t)(header1 >> 4);
    const uint8_t flags = header1 & 0x0F;

    out->type = type;

    switch (type) {
        case MQTT_PKT_CONNECT: {
            size_t pos = 0;
            char* proto = NULL;
            if (!read_string(body, body_len, &pos, &proto)) {
                return false;
            }
            if (strcmp(proto, "MQTT") != 0) {
                free(proto);
                return false;
            }
            free(proto);

            if (pos + 4 > body_len) {
                return false;
            }
            const uint8_t level = body[pos++];
            const uint8_t connect_flags = body[pos++];
            uint16_t keep_alive = 0;
            if (!read_u16(body, body_len, &pos, &keep_alive)) {
                return false;
            }
            if (level != 4) {
                return false;
            }

            const bool clean_session = (connect_flags & 0x02) != 0;

            char* client_id = NULL;
            if (!read_string(body, body_len, &pos, &client_id)) {
                return false;
            }

            out->v.connect.client_id = client_id;
            out->v.connect.keep_alive = keep_alive;
            out->v.connect.clean_session = clean_session;
            return true;
        }
        case MQTT_PKT_SUBSCRIBE: {
            if (flags != 0x02) {
                return false;
            }
            size_t pos = 0;
            uint16_t packet_id = 0;
            if (!read_u16(body, body_len, &pos, &packet_id)) {
                return false;
            }

            mqtt_subscribe_payload_t sp;
            memset(&sp, 0, sizeof(sp));
            sp.packet_id = packet_id;
            sp.topics = NULL;
            sp.topic_count = 0;

            while (pos < body_len) {
                char* filter = NULL;
                if (!read_string(body, body_len, &pos, &filter)) {
                    // cleanup
                    mqtt_packet_t tmp;
                    tmp.type = MQTT_PKT_SUBSCRIBE;
                    tmp.v.subscribe = sp;
                    mqtt_packet_free(&tmp);
                    return false;
                }
                if (pos + 1 > body_len) {
                    free(filter);
                    mqtt_packet_t tmp;
                    tmp.type = MQTT_PKT_SUBSCRIBE;
                    tmp.v.subscribe = sp;
                    mqtt_packet_free(&tmp);
                    return false;
                }
                const uint8_t qos = body[pos++] & 0x03;

                mqtt_subscribe_topic_t* nt = (mqtt_subscribe_topic_t*)realloc(
                    sp.topics, (sp.topic_count + 1) * sizeof(mqtt_subscribe_topic_t));
                if (!nt) {
                    free(filter);
                    mqtt_packet_t tmp;
                    tmp.type = MQTT_PKT_SUBSCRIBE;
                    tmp.v.subscribe = sp;
                    mqtt_packet_free(&tmp);
                    return false;
                }
                sp.topics = nt;
                sp.topics[sp.topic_count].filter = filter;
                sp.topics[sp.topic_count].qos = qos;
                sp.topic_count++;
            }

            out->v.subscribe = sp;
            return true;
        }
        case MQTT_PKT_PUBLISH: {
            size_t pos = 0;
            char* topic = NULL;
            if (!read_string(body, body_len, &pos, &topic)) {
                return false;
            }

            mqtt_publish_payload_t pp;
            memset(&pp, 0, sizeof(pp));
            pp.topic_name = topic;
            pp.dup = (header1 & 0x08) != 0;
            pp.qos = (uint8_t)((header1 >> 1) & 0x03);
            pp.retain = (header1 & 0x01) != 0;
            pp.has_packet_id = false;

            if (pp.qos > 0) {
                uint16_t pid = 0;
                if (!read_u16(body, body_len, &pos, &pid)) {
                    free(topic);
                    return false;
                }
                pp.has_packet_id = true;
                pp.packet_id = pid;
            }

            if (pos > body_len) {
                free(topic);
                return false;
            }

            const size_t pl_len = body_len - pos;
            uint8_t* pl = NULL;
            if (pl_len > 0) {
                pl = (uint8_t*)malloc(pl_len);
                if (!pl) {
                    free(topic);
                    return false;
                }
                memcpy(pl, body + pos, pl_len);
            }
            pp.payload = pl;
            pp.payload_len = pl_len;

            out->v.publish = pp;
            return true;
        }
        case MQTT_PKT_PINGREQ:
        case MQTT_PKT_DISCONNECT:
            return true;
        default:
            return false;
    }
}

bool mqtt_decoder_feed(const uint8_t* buffer,
                       size_t buffer_len,
                       size_t* out_consumed,
                       mqtt_on_packet_fn on_packet,
                       void* user) {
    if (!buffer || !out_consumed) {
        return false;
    }

    *out_consumed = 0;
    size_t consumed = 0;
    while (1) {
        if (buffer_len - consumed < 2) {
            break;
        }

        const uint8_t header1 = buffer[consumed];
        remaining_length_t rl;
        if (!try_parse_remaining_length(buffer, buffer_len, consumed + 1, &rl)) {
            return false;
        }

        const size_t fixed_hdr_len = 1 + rl.bytes;
        const size_t total_len = fixed_hdr_len + rl.value;
        if (buffer_len - consumed < total_len) {
            break;
        }

        const size_t body_start = consumed + fixed_hdr_len;
        const uint8_t* body = buffer + body_start;
        const size_t body_len = rl.value;

        mqtt_packet_t pkt;
        if (decode_one(header1, body, body_len, &pkt)) {
            if (on_packet) {
                on_packet(user, &pkt);
            }
            mqtt_packet_free(&pkt);
        }

        consumed += total_len;
    }

    *out_consumed = consumed;
    return true;
}
