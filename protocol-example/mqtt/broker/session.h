#pragma once

#include "../network/connection.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct mqtt_session mqtt_session_t;

mqtt_session_t* mqtt_session_create(mqtt_connection_t* conn);
void mqtt_session_destroy(mqtt_session_t* s);

int mqtt_session_id(const mqtt_session_t* s);
mqtt_connection_t* mqtt_session_connection(const mqtt_session_t* s);

bool mqtt_session_connected(const mqtt_session_t* s);
const char* mqtt_session_client_id(const mqtt_session_t* s);

void mqtt_session_mark_connected(mqtt_session_t* s, const char* client_id, bool clean_session, uint16_t keep_alive);
void mqtt_session_send(mqtt_session_t* s, const uint8_t* data, size_t len);

#ifdef __cplusplus
}
#endif
