/*
 * net.h - POSIX TCP socket helpers for the MQTT broker.
 *
 * Descriptor modes are established at creation time and never inherited:
 * net_listen and net_accept both request SOCK_NONBLOCK | SOCK_CLOEXEC
 * explicitly, and net_accept re-establishes them for every accepted peer with
 * accept4. Every descriptor returned here is therefore nonblocking, so a
 * single read/write call always returns promptly and can never stall the
 * readiness loop on unrelated peers.
 */
#ifndef MQTT_BROKER_NET_H
#define MQTT_BROKER_NET_H

#include <stddef.h>
#include <stdint.h>

/* Outcome of one nonblocking read/write attempt. */
enum net_io_result {
    NET_IO_PROGRESS = 1,     /* bytes were transferred; see the out count */
    NET_IO_WOULD_BLOCK = 0,  /* no progress is possible right now */
    NET_IO_CLOSED = -1,      /* read only: the peer performed an orderly close */
    NET_IO_ERROR = -2        /* fatal error on this descriptor */
};

/*
 * Parse the <port> command line argument: one or more decimal digits, no sign,
 * no whitespace and no trailing characters, with a value in 1..65535.
 * Returns 0 and stores the port on success, -1 otherwise (*out_port untouched).
 */
int net_parse_port(const char *text, uint16_t *out_port);

/*
 * Create a listening TCP socket bound to all local addresses on port with the
 * given backlog. SO_REUSEADDR is set, and the descriptor is created
 * nonblocking and close-on-exec. Returns a descriptor >= 0, or -1 with errno
 * set. Port 0 asks the kernel for an ephemeral port (used by tests; the CLI
 * never passes 0 because net_parse_port rejects it).
 */
int net_listen(uint16_t port, int backlog);

/*
 * Accept one pending peer of listener_fd. On success stores a nonblocking,
 * close-on-exec descriptor in *out_fd and returns 1. Returns 0 when no
 * connection is pending. Returns -1 on any accept error (including EMFILE,
 * ENOBUFS and ECONNABORTED); that is never fatal for the listener, the caller
 * logs it and keeps serving.
 */
int net_accept(int listener_fd, int *out_fd);

/*
 * Read at most cap bytes from fd into buf. Returns NET_IO_PROGRESS with *out_n
 * > 0 when bytes were read, NET_IO_WOULD_BLOCK when nothing is available,
 * NET_IO_CLOSED when the peer closed the stream and NET_IO_ERROR on failure.
 * Interrupted calls are retried internally.
 */
enum net_io_result net_read_some(int fd, uint8_t *buf, size_t cap,
                                 size_t *out_n);

/*
 * Write at most len bytes from buf to fd. Returns NET_IO_PROGRESS with *out_n
 * > 0 when bytes were written, NET_IO_WOULD_BLOCK when the socket buffer is
 * full and NET_IO_ERROR on failure. MSG_NOSIGNAL is used, and an interrupted
 * call is retried internally, so a single call is bounded by len bytes.
 */
enum net_io_result net_write_some(int fd, const uint8_t *buf, size_t len,
                                  size_t *out_n);

/*
 * Close *fd and set it to -1. Does nothing when *fd is already -1, so calling
 * it twice is safe and a descriptor is never closed twice.
 */
void net_close_fd(int *fd);

#endif /* MQTT_BROKER_NET_H */
