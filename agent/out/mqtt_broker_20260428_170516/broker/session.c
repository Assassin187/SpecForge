#include "broker/session.h"
#include <stdlib.h>
#include <string.h>

struct mqtt_session {
    mqtt_connection_t* conn;
    bool connected;
    char* client_id;
    bool clean_session;
    uint16_t keep_alive;
};

mqtt_session_t* mqtt_session_create(mqtt_connection_t* conn)
{
    if (conn == NULL) {
        return NULL;
    }

    mqtt_session_t* s = malloc(sizeof(mqtt_session_t));
    if (s == NULL) {
        return NULL;
    }

    s->conn = conn;
    s->connected = false;
    s->client_id = NULL;
    s->clean_session = true;
    s->keep_alive = 0;

    return s;
}

void mqtt_session_destroy(mqtt_session_t* s)
{
    if (s == NULL) {
        return;
    }

    free(s->client_id);
    s->client_id = NULL;
    free(s);
}

int mqtt_session_id(const mqtt_session_t* s)
{
    if (s == NULL || s->conn == NULL) {
        return -1;
    }

    return mqtt_connection_fd(s->conn);
}

mqtt_connection_t* mqtt_session_connection(const mqtt_session_t* s)
{
    if (s == NULL) {
        return NULL;
    }

    return s->conn;
}

bool mqtt_session_connected(const mqtt_session_t* s)
{
    if (s == NULL) {
        return false;
    }

    return s->connected;
}

const char* mqtt_session_client_id(const mqtt_session_t* s)
{
    if (s == NULL || s->client_id == NULL) {
        return "";
    }

    return s->client_id;
}

void mqtt_session_mark_connected(mqtt_session_t* s, const char* client_id, bool clean_session, uint16_t keep_alive)
{
    if (s == NULL) {
        return;
    }

    s->connected = true;

    free(s->client_id);
    s->client_id = NULL;

    if (client_id != NULL) {
        size_t len = strlen(client_id);
        s->client_id = malloc(len + 1);
        if (s->client_id != NULL) {
            memcpy(s->client_id, client_id, len + 1);
        }
    }

    s->clean_session = clean_session;
    s->keep_alive = keep_alive;
}

void mqtt_session_send(mqtt_session_t* s, const uint8_t* data, size_t len)
{
    if (s == NULL || s->conn == NULL || data == NULL || len == 0) {
        return;
    }

    mqtt_connection_send(s->conn, data, len);
}
