#ifndef HTTP_SESSION_H
#define HTTP_SESSION_H

#include <stdbool.h>
#include <stddef.h>

#include "../network/connection.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct http_session {
    int fd;
    http_connection_t* conn;
} http_session_t;

http_session_t* http_session_create(int fd);
void http_session_destroy(http_session_t* session);

#ifdef __cplusplus
}
#endif

#endif
