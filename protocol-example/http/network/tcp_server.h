#ifndef HTTP_TCP_SERVER_H
#define HTTP_TCP_SERVER_H

#include <netinet/in.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct http_tcp_server http_tcp_server_t;

enum {
    HTTP_IO_READABLE = 1u << 0,
    HTTP_IO_WRITABLE = 1u << 1,
};

typedef struct {
    void (*on_accept)(void* user, int fd, const struct sockaddr_storage* peer, socklen_t peer_len);
    void (*on_event)(void* user, int fd, uint32_t events);
    void (*on_close)(void* user, int fd);
} http_tcp_callbacks_t;

http_tcp_server_t* http_tcp_server_create(uint16_t port, http_tcp_callbacks_t callbacks, void* user);
int http_tcp_server_start(http_tcp_server_t* server);
int http_tcp_server_run(http_tcp_server_t* server);
void http_tcp_server_stop(http_tcp_server_t* server);
void http_tcp_server_destroy(http_tcp_server_t* server);

int http_tcp_server_update_interest(http_tcp_server_t* server, int fd, bool want_write);
int http_tcp_server_close_client(http_tcp_server_t* server, int fd);

#ifdef __cplusplus
}
#endif

#endif
