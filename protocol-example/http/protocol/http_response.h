#ifndef HTTP_RESPONSE_H
#define HTTP_RESPONSE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct http_connection http_connection_t;

/* Status-line helpers */
const char* http_status_text(int code);

/* Queue a complete response to the connection.
 * - status: HTTP status code (200, 404, etc.)
 * - extra_headers: NULL-terminated array of "Name: value" strings, or NULL.
 * - body: raw body bytes (may be NULL if body_len == 0).
 * - body_len: length of body in bytes.
 * Returns 0 on success, -1 on error. */
int http_response_send(http_connection_t* conn,
                       int status,
                       const char** extra_headers,
                       const void* body, size_t body_len);

/* Convenience: send a simple text/html response built from a format string. */
int http_response_send_html(http_connection_t* conn,
                            int status,
                            const char* title,
                            const char* fmt, ...)
    __attribute__((format(printf, 4, 5)));

#ifdef __cplusplus
}
#endif

#endif
