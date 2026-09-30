#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct mqtt_connection mqtt_connection_t;

mqtt_connection_t* mqtt_connection_create(int fd);
void mqtt_connection_destroy(mqtt_connection_t* c);

int mqtt_connection_fd(const mqtt_connection_t* c);
bool mqtt_connection_closed(const mqtt_connection_t* c);

const char* mqtt_connection_peer(const mqtt_connection_t* c);
void mqtt_connection_set_peer(mqtt_connection_t* c, const char* peer);

// Read as much as possible into internal input buffer.
// Returns false only on fatal error.
// If *peer_closed is true, peer performed orderly shutdown; caller may still
// want to process bytes already read before closing.
bool mqtt_connection_read(mqtt_connection_t* c, bool* peer_closed);

// Try to flush queued output buffers.
bool mqtt_connection_flush(mqtt_connection_t* c);

// Queue bytes to send (copies data).
void mqtt_connection_send(mqtt_connection_t* c, const uint8_t* data, size_t len);

// Input buffer access.
uint8_t* mqtt_connection_in_data(mqtt_connection_t* c);
size_t mqtt_connection_in_len(const mqtt_connection_t* c);
void mqtt_connection_in_consume(mqtt_connection_t* c, size_t n);

bool mqtt_connection_want_write(const mqtt_connection_t* c);
void mqtt_connection_close(mqtt_connection_t* c);

#ifdef __cplusplus
}
#endif
