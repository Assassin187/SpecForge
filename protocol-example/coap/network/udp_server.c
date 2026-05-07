#include "udp_server.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/epoll.h>
#include <sys/socket.h>
#include <unistd.h>

enum {
    MAX_EVENTS = 16,
    MAX_DATAGRAM_SIZE = 1500,
};

struct coap_udp_server {
    uint16_t port;
    coap_udp_callbacks_t cb;
    void* user;
    int fd;
    int epoll_fd;
    bool running;
};

static bool set_nonblocking(int fd) {
    const int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0) {
        return false;
    }
    return fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0;
}

const char* coap_endpoint_to_string(const coap_endpoint_t* peer, char* buf, size_t buf_sz) {
    if (!peer || !buf || buf_sz == 0) {
        return "";
    }

    char host[INET6_ADDRSTRLEN] = {0};
    uint16_t port = 0;
    if (peer->addr.ss_family == AF_INET) {
        const struct sockaddr_in* in = (const struct sockaddr_in*)&peer->addr;
        inet_ntop(AF_INET, &in->sin_addr, host, sizeof(host));
        port = ntohs(in->sin_port);
    } else if (peer->addr.ss_family == AF_INET6) {
        const struct sockaddr_in6* in6 = (const struct sockaddr_in6*)&peer->addr;
        inet_ntop(AF_INET6, &in6->sin6_addr, host, sizeof(host));
        port = ntohs(in6->sin6_port);
    } else {
        snprintf(buf, buf_sz, "<unknown>");
        return buf;
    }

    snprintf(buf, buf_sz, "%s:%u", host, (unsigned)port);
    return buf;
}

coap_udp_server_t* coap_udp_server_create(uint16_t port, coap_udp_callbacks_t cb, void* user) {
    coap_udp_server_t* s = (coap_udp_server_t*)calloc(1, sizeof(*s));
    if (!s) {
        return NULL;
    }
    s->port = port;
    s->cb = cb;
    s->user = user;
    s->fd = -1;
    s->epoll_fd = -1;
    return s;
}

void coap_udp_server_destroy(coap_udp_server_t* s) {
    if (!s) {
        return;
    }
    coap_udp_server_stop(s);
    free(s);
}

bool coap_udp_server_start(coap_udp_server_t* s) {
    if (!s) {
        return false;
    }

    s->fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (s->fd < 0) {
        fprintf(stderr, "socket() failed: %s\n", strerror(errno));
        return false;
    }

    int opt = 1;
    (void)setsockopt(s->fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    if (!set_nonblocking(s->fd)) {
        fprintf(stderr, "set_nonblocking() failed\n");
        return false;
    }

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons(s->port);

    if (bind(s->fd, (struct sockaddr*)&addr, sizeof(addr)) != 0) {
        fprintf(stderr, "bind() failed: %s\n", strerror(errno));
        return false;
    }

    s->epoll_fd = epoll_create1(0);
    if (s->epoll_fd < 0) {
        fprintf(stderr, "epoll_create1() failed: %s\n", strerror(errno));
        return false;
    }

    struct epoll_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.events = EPOLLIN;
    ev.data.fd = s->fd;
    if (epoll_ctl(s->epoll_fd, EPOLL_CTL_ADD, s->fd, &ev) != 0) {
        fprintf(stderr, "epoll_ctl() failed: %s\n", strerror(errno));
        return false;
    }

    s->running = true;
    return true;
}

void coap_udp_server_stop(coap_udp_server_t* s) {
    if (!s) {
        return;
    }
    s->running = false;
    if (s->epoll_fd >= 0) {
        close(s->epoll_fd);
        s->epoll_fd = -1;
    }
    if (s->fd >= 0) {
        close(s->fd);
        s->fd = -1;
    }
}

bool coap_udp_server_sendto(coap_udp_server_t* s, const coap_endpoint_t* peer, const uint8_t* data, size_t len) {
    if (!s || !peer || !data || len == 0 || s->fd < 0) {
        return false;
    }

    while (1) {
        const ssize_t n = sendto(s->fd, data, len, 0, (const struct sockaddr*)&peer->addr, peer->addr_len);
        if (n >= 0) {
            return (size_t)n == len;
        }
        if (errno == EINTR) {
            continue;
        }
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            return false;
        }
        return false;
    }
}

void coap_udp_server_run(coap_udp_server_t* s) {
    if (!s || !s->running) {
        return;
    }

    printf("CoAP UDP server listening on 0.0.0.0:%u\n", (unsigned)s->port);

    struct epoll_event events[MAX_EVENTS];
    uint8_t buf[MAX_DATAGRAM_SIZE];

    while (s->running) {
        const int n = epoll_wait(s->epoll_fd, events, MAX_EVENTS, 1000);
        if (n < 0) {
            if (errno == EINTR) {
                continue;
            }
            fprintf(stderr, "epoll_wait() failed: %s\n", strerror(errno));
            break;
        }

        for (int i = 0; i < n; ++i) {
            if (!(events[i].events & EPOLLIN) || events[i].data.fd != s->fd) {
                continue;
            }

            while (1) {
                coap_endpoint_t peer;
                memset(&peer, 0, sizeof(peer));
                peer.addr_len = sizeof(peer.addr);

                const ssize_t r = recvfrom(s->fd,
                                           buf,
                                           sizeof(buf),
                                           0,
                                           (struct sockaddr*)&peer.addr,
                                           &peer.addr_len);
                if (r > 0) {
                    if (s->cb.on_datagram) {
                        s->cb.on_datagram(s->user, &peer, buf, (size_t)r);
                    }
                    continue;
                }
                if (r == 0) {
                    break;
                }
                if (errno == EINTR) {
                    continue;
                }
                if (errno == EAGAIN || errno == EWOULDBLOCK) {
                    break;
                }
                fprintf(stderr, "recvfrom() failed: %s\n", strerror(errno));
                break;
            }
        }
    }
}
