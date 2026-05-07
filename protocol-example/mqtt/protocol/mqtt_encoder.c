#include "mqtt_encoder.h"

#include <stdlib.h>
#include <string.h>

static void put_u16(uint8_t* out, size_t* pos, uint16_t v) {
    out[(*pos)++] = (uint8_t)((v >> 8) & 0xFF);
    out[(*pos)++] = (uint8_t)(v & 0xFF);
}

static size_t remaining_length_bytes(size_t len) {
    size_t n = 0;
    do {
        len /= 128;
        n++;
    } while (len > 0);
    return n;
}

static void put_remaining_length(uint8_t* out, size_t* pos, size_t len) {
    do {
        uint8_t encoded = (uint8_t)(len % 128);
        len /= 128;
        if (len > 0) {
            encoded |= 0x80;
        }
        out[(*pos)++] = encoded;
    } while (len > 0);
}

static mqtt_bytes_t make_bytes(size_t len) {
    mqtt_bytes_t b;
    b.data = (uint8_t*)malloc(len);
    b.len = b.data ? len : 0;
    return b;
}

void mqtt_bytes_free(mqtt_bytes_t* b) {
    if (!b) {
        return;
    }
    free(b->data);
    b->data = NULL;
    b->len = 0;
}

mqtt_bytes_t mqtt_encode_connack(bool session_present, uint8_t return_code) {
    // fixed header 2 bytes + remaining length varint (1) + vh (2)
    const size_t vh_len = 2;
    const size_t rl_len = remaining_length_bytes(vh_len);
    mqtt_bytes_t out = make_bytes(1 + rl_len + vh_len);
    if (!out.data) {
        return out;
    }

    size_t pos = 0;
    out.data[pos++] = (uint8_t)(MQTT_PKT_CONNACK << 4);
    put_remaining_length(out.data, &pos, vh_len);
    out.data[pos++] = session_present ? 0x01 : 0x00;
    out.data[pos++] = return_code;

    return out;
}

mqtt_bytes_t mqtt_encode_suback(uint16_t packet_id, const uint8_t* return_codes, size_t return_code_count) {
    const size_t vh_len = 2; // packet id
    const size_t pl_len = return_code_count;
    const size_t body_len = vh_len + pl_len;
    const size_t rl_len = remaining_length_bytes(body_len);

    mqtt_bytes_t out = make_bytes(1 + rl_len + body_len);
    if (!out.data) {
        return out;
    }

    size_t pos = 0;
    out.data[pos++] = (uint8_t)(MQTT_PKT_SUBACK << 4);
    put_remaining_length(out.data, &pos, body_len);
    put_u16(out.data, &pos, packet_id);
    if (return_code_count > 0 && return_codes) {
        memcpy(out.data + pos, return_codes, return_code_count);
        pos += return_code_count;
    }

    return out;
}

mqtt_bytes_t mqtt_encode_pingresp(void) {
    mqtt_bytes_t out = make_bytes(2);
    if (!out.data) {
        return out;
    }
    out.data[0] = (uint8_t)(MQTT_PKT_PINGRESP << 4);
    out.data[1] = 0;
    return out;
}

mqtt_bytes_t mqtt_encode_publish_qos0(const char* topic_name, const uint8_t* payload, size_t payload_len, bool retain) {
    if (!topic_name) {
        mqtt_bytes_t empty = {0};
        return empty;
    }

    const size_t topic_len = strlen(topic_name);
    if (topic_len > 0xFFFF) {
        mqtt_bytes_t empty = {0};
        return empty;
    }

    const size_t vh_len = 2 + topic_len;
    const size_t body_len = vh_len + payload_len;
    const size_t rl_len = remaining_length_bytes(body_len);

    mqtt_bytes_t out = make_bytes(1 + rl_len + body_len);
    if (!out.data) {
        return out;
    }

    size_t pos = 0;
    uint8_t header1 = (uint8_t)(MQTT_PKT_PUBLISH << 4);
    if (retain) {
        header1 |= 0x01;
    }
    out.data[pos++] = header1;
    put_remaining_length(out.data, &pos, body_len);
    put_u16(out.data, &pos, (uint16_t)topic_len);
    memcpy(out.data + pos, topic_name, topic_len);
    pos += topic_len;
    if (payload_len > 0 && payload) {
        memcpy(out.data + pos, payload, payload_len);
        pos += payload_len;
    }

    return out;
}
