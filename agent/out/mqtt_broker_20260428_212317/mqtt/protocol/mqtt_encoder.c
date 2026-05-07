#include "protocol/mqtt_encoder.h"

#include <stdlib.h>
#include <string.h>

#define MQTT_PKT_CONNACK    2
#define MQTT_PKT_PUBLISH    3
#define MQTT_PKT_SUBACK     9
#define MQTT_PKT_PINGRESP   13

static size_t remaining_length_bytes(size_t len)
{
    size_t count = 0;
    do {
        len /= 128;
        count++;
    } while (len > 0);
    return count;
}

static void put_remaining_length(uint8_t* out, size_t* pos, size_t len)
{
    uint8_t encoded_byte;
    do {
        encoded_byte = len % 128;
        len /= 128;
        if (len > 0) {
            encoded_byte |= 128;
        }
        out[(*pos)++] = encoded_byte;
    } while (len > 0);
}

static void put_u16(uint8_t* out, size_t* pos, uint16_t v)
{
    out[(*pos)++] = (uint8_t)((v >> 8) & 0xFF);
    out[(*pos)++] = (uint8_t)(v & 0xFF);
}

static mqtt_bytes_t make_bytes(size_t len)
{
    mqtt_bytes_t b = {0};
    if (len == 0) {
        return b;
    }
    b.data = (uint8_t*)malloc(len);
    if (b.data) {
        b.len = len;
    }
    return b;
}

void mqtt_bytes_free(mqtt_bytes_t* b)
{
    if (!b) {
        return;
    }
    free(b->data);
    b->data = NULL;
    b->len = 0;
}

mqtt_bytes_t mqtt_encode_connack(bool session_present, uint8_t return_code)
{
    const size_t variable_header_len = 2;
    const size_t rl_bytes = remaining_length_bytes(variable_header_len);
    const size_t total_len = 1 + rl_bytes + variable_header_len;

    mqtt_bytes_t out = make_bytes(total_len);
    if (!out.data) {
        return out;
    }

    size_t pos = 0;
    out.data[pos++] = (MQTT_PKT_CONNACK << 4) | 0x00; // no flags for CONNACK
    put_remaining_length(out.data, &pos, variable_header_len);
    out.data[pos++] = session_present ? 0x01 : 0x00;
    out.data[pos++] = return_code;

    return out;
}

mqtt_bytes_t mqtt_encode_suback(uint16_t packet_id, const uint8_t* return_codes, size_t return_code_count)
{
    const size_t payload_len = return_code_count;
    const size_t variable_header_len = 2 + payload_len;
    const size_t rl_bytes = remaining_length_bytes(variable_header_len);
    const size_t total_len = 1 + rl_bytes + variable_header_len;

    mqtt_bytes_t out = make_bytes(total_len);
    if (!out.data) {
        return out;
    }

    size_t pos = 0;
    out.data[pos++] = (MQTT_PKT_SUBACK << 4) | 0x00; // no flags for SUBACK
    put_remaining_length(out.data, &pos, variable_header_len);
    put_u16(out.data, &pos, packet_id);

    if (return_codes && return_code_count > 0) {
        memcpy(&out.data[pos], return_codes, return_code_count);
    }

    return out;
}

mqtt_bytes_t mqtt_encode_pingresp(void)
{
    mqtt_bytes_t out = make_bytes(2);
    if (!out.data) {
        return out;
    }

    out.data[0] = (MQTT_PKT_PINGRESP << 4) | 0x00;
    out.data[1] = 0x00; // Remaining Length = 0

    return out;
}

mqtt_bytes_t mqtt_encode_publish_qos0(const char* topic_name, const uint8_t* payload, size_t payload_len, bool retain)
{
    if (!topic_name) {
        mqtt_bytes_t empty = {0};
        return empty;
    }

    size_t topic_len = strlen(topic_name);
    if (topic_len > 65535) {
        mqtt_bytes_t empty = {0};
        return empty;
    }

    const size_t variable_header_len = 2 + topic_len;
    const size_t payload_offset = variable_header_len;
    const size_t total_body_len = variable_header_len + payload_len;
    const size_t rl_bytes = remaining_length_bytes(total_body_len);
    const size_t total_len = 1 + rl_bytes + total_body_len;

    mqtt_bytes_t out = make_bytes(total_len);
    if (!out.data) {
        return out;
    }

    size_t pos = 0;
    uint8_t flags = 0x00; // QoS=0, DUP=0, RETAIN as given
    if (retain) {
        flags |= 0x01;
    }
    out.data[pos++] = (MQTT_PKT_PUBLISH << 4) | flags;
    put_remaining_length(out.data, &pos, total_body_len);
    put_u16(out.data, &pos, (uint16_t)topic_len);
    memcpy(&out.data[pos], topic_name, topic_len);
    pos += topic_len;

    if (payload && payload_len > 0) {
        memcpy(&out.data[pos], payload, payload_len);
    }

    return out;
}
