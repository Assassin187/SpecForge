#pragma once

#include "mqtt_packet.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct mqtt_bytes {
    uint8_t* data;
    size_t len;
} mqtt_bytes_t;

void mqtt_bytes_free(mqtt_bytes_t* b);

mqtt_bytes_t mqtt_encode_connack(bool session_present, uint8_t return_code);

mqtt_bytes_t mqtt_encode_suback(uint16_t packet_id, const uint8_t* return_codes, size_t return_code_count);

mqtt_bytes_t mqtt_encode_pingresp(void);

mqtt_bytes_t mqtt_encode_publish_qos0(const char* topic_name, const uint8_t* payload, size_t payload_len, bool retain);

#ifdef __cplusplus
}
#endif
