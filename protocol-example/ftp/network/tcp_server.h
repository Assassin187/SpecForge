#ifndef FTP_TCP_SERVER_H
#define FTP_TCP_SERVER_H

#include <netinet/in.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct ftp_tcp_server ftp_tcp_server_t;

enum {
    FTP_IO_READABLE = 1u << 0,
    FTP_IO_WRITABLE = 1u << 1,
};

typedef struct {
    void (*on_accept)(void* user, int fd, const struct sockaddr_storage* peer, socklen_t peer_len);
    void (*on_event)(void* user, int fd, uint32_t events);
    void (*on_close)(void* user, int fd);
} ftp_tcp_callbacks_t;

ftp_tcp_server_t* ftp_tcp_server_create(uint16_t port, ftp_tcp_callbacks_t callbacks, void* user);
int ftp_tcp_server_start(ftp_tcp_server_t* server);
int ftp_tcp_server_run(ftp_tcp_server_t* server);
void ftp_tcp_server_stop(ftp_tcp_server_t* server);
void ftp_tcp_server_destroy(ftp_tcp_server_t* server);

int ftp_tcp_server_update_interest(ftp_tcp_server_t* server, int fd, bool want_write);
int ftp_tcp_server_close_client(ftp_tcp_server_t* server, int fd);

#ifdef __cplusplus
}
#endif

#endif
