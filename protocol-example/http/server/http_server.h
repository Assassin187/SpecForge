#ifndef HTTP_SERVER_H
#define HTTP_SERVER_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct http_server http_server_t;

http_server_t* http_server_create(uint16_t port, const char* root_dir);
int http_server_start(http_server_t* server);
int http_server_run(http_server_t* server);
void http_server_stop(http_server_t* server);
void http_server_destroy(http_server_t* server);

#ifdef __cplusplus
}
#endif

#endif
