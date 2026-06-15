#include "http_session.h"

#include <stdlib.h>

http_session_t* http_session_create(int fd) {
    http_session_t* session = (http_session_t*)calloc(1, sizeof(*session));
    if (session == NULL) {
        return NULL;
    }

    session->fd = fd;
    session->conn = http_connection_create(fd);
    if (session->conn == NULL) {
        free(session);
        return NULL;
    }

    return session;
}

void http_session_destroy(http_session_t* session) {
    if (session == NULL) {
        return;
    }
    http_connection_destroy(session->conn);
    free(session);
}
