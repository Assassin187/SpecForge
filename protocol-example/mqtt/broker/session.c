#include "session.h"

#include <stdlib.h>
#include <string.h>

struct mqtt_session {
    mqtt_connection_t* conn;

    bool connected;
    char* client_id;
    bool clean_session;
    uint16_t keep_alive;
};

mqtt_session_t* mqtt_session_create(mqtt_connection_t* conn) {
    if (!conn) {
        return NULL;
    }
    mqtt_session_t* s = (mqtt_session_t*)calloc(1, sizeof(*s));
    if (!s) {
        return NULL;
    }
    s->conn = conn;
    s->connected = false;
    s->client_id = NULL;
    s->clean_session = true;
    s->keep_alive = 0;
    return s;
}

void mqtt_session_destroy(mqtt_session_t* s) {
    if (!s) {
        return;
    }
    free(s->client_id);
    s->client_id = NULL;
    free(s);
}

int mqtt_session_id(const mqtt_session_t* s) {
    return s && s->conn ? mqtt_connection_fd(s->conn) : -1;
}

mqtt_connection_t* mqtt_session_connection(const mqtt_session_t* s) {
    return s ? s->conn : NULL;
}

bool mqtt_session_connected(const mqtt_session_t* s) {
    return s ? s->connected : false;
}

const char* mqtt_session_client_id(const mqtt_session_t* s) {
    return (s && s->client_id) ? s->client_id : "";
}

void mqtt_session_mark_connected(mqtt_session_t* s, const char* client_id, bool clean_session, uint16_t keep_alive) {
    if (!s) {
        return;
    }
    s->connected = true;
    free(s->client_id);
    s->client_id = NULL;
    if (client_id) {
        s->client_id = strdup(client_id);
    }
    s->clean_session = clean_session;
    s->keep_alive = keep_alive;
}

void mqtt_session_send(mqtt_session_t* s, const uint8_t* data, size_t len) {
    if (!s || !s->conn || !data || len == 0) {
        return;
    }
    mqtt_connection_send(s->conn, data, len);
}
