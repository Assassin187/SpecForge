#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum mqtt_packet_type {
    MQTT_PKT_RESERVED = 0,
    MQTT_PKT_CONNECT = 1,
    MQTT_PKT_CONNACK = 2,
    MQTT_PKT_PUBLISH = 3,
    MQTT_PKT_SUBSCRIBE = 8,
    MQTT_PKT_SUBACK = 9,
    MQTT_PKT_PINGREQ = 12,
    MQTT_PKT_PINGRESP = 13,
    MQTT_PKT_DISCONNECT = 14,
} mqtt_packet_type_t;

typedef struct mqtt_connect_payload {
    char* client_id;
    uint16_t keep_alive;
    bool clean_session;
} mqtt_connect_payload_t;

typedef struct mqtt_publish_payload {
    char* topic_name;
    uint8_t* payload;
    size_t payload_len;
    uint8_t qos;
    bool retain;
    bool dup;
    bool has_packet_id;
    uint16_t packet_id;
} mqtt_publish_payload_t;

typedef struct mqtt_subscribe_topic {
    char* filter;
    uint8_t qos;
} mqtt_subscribe_topic_t;

typedef struct mqtt_subscribe_payload {
    uint16_t packet_id;
    mqtt_subscribe_topic_t* topics;
    size_t topic_count;
} mqtt_subscribe_payload_t;

typedef struct mqtt_packet {
    mqtt_packet_type_t type;
    union {
        mqtt_connect_payload_t connect;
        mqtt_publish_payload_t publish;
        mqtt_subscribe_payload_t subscribe;
    } v;
} mqtt_packet_t;

void mqtt_packet_free(mqtt_packet_t* p);

#ifdef __cplusplus
}
#endif
