#ifndef SMTP_TCP_SERVER_H
#define SMTP_TCP_SERVER_H

#include <netinet/in.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct smtp_tcp_server smtp_tcp_server_t;

enum {
    SMTP_IO_READABLE = 1u << 0,
    SMTP_IO_WRITABLE = 1u << 1,
};

typedef struct {
    void (*on_accept)(void* user, int fd, const struct sockaddr_storage* peer, socklen_t peer_len);
    void (*on_event)(void* user, int fd, uint32_t events);
    void (*on_close)(void* user, int fd);
} smtp_tcp_callbacks_t;

smtp_tcp_server_t* smtp_tcp_server_create(uint16_t port, smtp_tcp_callbacks_t callbacks, void* user);
int smtp_tcp_server_start(smtp_tcp_server_t* server);
int smtp_tcp_server_run(smtp_tcp_server_t* server);
void smtp_tcp_server_stop(smtp_tcp_server_t* server);
void smtp_tcp_server_destroy(smtp_tcp_server_t* server);

int smtp_tcp_server_update_interest(smtp_tcp_server_t* server, int fd, bool want_write);
int smtp_tcp_server_close_client(smtp_tcp_server_t* server, int fd);

#ifdef __cplusplus
}
#endif

#endif
