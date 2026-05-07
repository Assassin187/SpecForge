#pragma once

#include "connection.h"

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*mqtt_tcp_on_accept_fn)(void* user, mqtt_connection_t* c);
typedef void (*mqtt_tcp_on_data_fn)(void* user, mqtt_connection_t* c);
typedef void (*mqtt_tcp_on_close_fn)(void* user, mqtt_connection_t* c);

typedef struct mqtt_tcp_callbacks {
    mqtt_tcp_on_accept_fn on_accept;
    mqtt_tcp_on_data_fn on_data;
    mqtt_tcp_on_close_fn on_close;
} mqtt_tcp_callbacks_t;

typedef struct mqtt_tcp_server mqtt_tcp_server_t;

mqtt_tcp_server_t* mqtt_tcp_server_create(uint16_t port, mqtt_tcp_callbacks_t cb, void* user);
void mqtt_tcp_server_destroy(mqtt_tcp_server_t* s);

bool mqtt_tcp_server_start(mqtt_tcp_server_t* s);
void mqtt_tcp_server_run(mqtt_tcp_server_t* s);
void mqtt_tcp_server_stop(mqtt_tcp_server_t* s);

#ifdef __cplusplus
}
#endif
