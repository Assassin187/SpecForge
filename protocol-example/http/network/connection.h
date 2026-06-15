#ifndef HTTP_CONNECTION_H
#define HTTP_CONNECTION_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct http_connection http_connection_t;

http_connection_t* http_connection_create(int fd);
void http_connection_destroy(http_connection_t* conn);

int http_connection_fd(const http_connection_t* conn);
int http_connection_read(http_connection_t* conn);

/* Pop a \r\n-terminated line from the read buffer. Caller must free. */
char* http_connection_pop_line(http_connection_t* conn);

/* Consume exactly len bytes from the read buffer; returns heap copy or NULL. */
char* http_connection_pop_bytes(http_connection_t* conn, size_t len);

/* How many bytes are currently buffered. */
size_t http_connection_buffered(const http_connection_t* conn);

/* Write-side queue. */
int http_connection_queue(http_connection_t* conn, const void* data, size_t len);
int http_connection_queue_str(http_connection_t* conn, const char* text);
int http_connection_flush(http_connection_t* conn);
bool http_connection_has_pending(const http_connection_t* conn);

#ifdef __cplusplus
}
#endif

#endif
