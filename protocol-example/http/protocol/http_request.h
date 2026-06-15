#ifndef HTTP_REQUEST_H
#define HTTP_REQUEST_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct http_connection http_connection_t;

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    HTTP_GET,
    HTTP_HEAD,
    HTTP_POST,
    HTTP_UNKNOWN,
} http_method_t;

enum {
    HTTP_MAX_HEADERS = 64,
    HTTP_MAX_URI     = 2048,
    HTTP_MAX_HEADER_NAME  = 256,
    HTTP_MAX_HEADER_VALUE = 4096,
    HTTP_MAX_BODY    = (16 * 1024 * 1024), /* 16 MiB */
};

typedef struct {
    char name[HTTP_MAX_HEADER_NAME];
    char value[HTTP_MAX_HEADER_VALUE];
} http_header_t;

typedef struct {
    http_method_t method;
    char uri[HTTP_MAX_URI];
    http_header_t headers[HTTP_MAX_HEADERS];
    size_t header_count;
    char* body;       /* heap-allocated, may be NULL */
    size_t body_len;
} http_request_t;

void http_request_init(http_request_t* req);
void http_request_free(http_request_t* req);

/* Parse from connection's read buffer. Returns 0 on success, -1 on error,
 * 1 if more data is needed (incomplete request). */
int http_request_parse(http_request_t* req, http_connection_t* conn);

/* Accessors */
const char* http_request_header(const http_request_t* req, const char* name);

#ifdef __cplusplus
}
#endif

#endif
