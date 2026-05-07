#include "tcp_server.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/epoll.h>
#include <sys/socket.h>
#include <unistd.h>

enum { MAX_EVENTS = 64 };

typedef struct conn_node {
    mqtt_connection_t* c;
    struct conn_node* next;
} conn_node_t;

struct mqtt_tcp_server {
    uint16_t port;
    mqtt_tcp_callbacks_t cb;
    void* user;

    int listen_fd;
    int epoll_fd;
    bool running;

    conn_node_t* conns;
};

static bool set_nonblocking(int fd) {
    const int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0) {
        return false;
    }
    return fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0;
}

static char* sockaddr_to_string(const struct sockaddr_in* addr) {
    char ip[INET_ADDRSTRLEN] = {0};
    inet_ntop(AF_INET, &addr->sin_addr, ip, sizeof(ip));
    const uint16_t port = ntohs(addr->sin_port);

    char buf[128];
    snprintf(buf, sizeof(buf), "%s:%u", ip, (unsigned)port);
    return strdup(buf);
}

static mqtt_connection_t* find_conn(mqtt_tcp_server_t* s, int fd) {
    for (conn_node_t* n = s->conns; n; n = n->next) {
        if (mqtt_connection_fd(n->c) == fd) {
            return n->c;
        }
    }
    return NULL;
}

static void remove_conn(mqtt_tcp_server_t* s, int fd) {
    conn_node_t** pp = &s->conns;
    while (*pp) {
        mqtt_connection_t* c = (*pp)->c;
        if (mqtt_connection_fd(c) == fd) {
            conn_node_t* dead = *pp;
            *pp = dead->next;
            mqtt_connection_destroy(dead->c);
            free(dead);
            return;
        }
        pp = &(*pp)->next;
    }
}

static void close_connection(mqtt_tcp_server_t* s, mqtt_connection_t* c) {
    if (!s || !c) {
        return;
    }
    const int fd = mqtt_connection_fd(c);
    (void)epoll_ctl(s->epoll_fd, EPOLL_CTL_DEL, fd, NULL);

    if (s->cb.on_close) {
        s->cb.on_close(s->user, c);
    }

    // on_close may still reference the connection; destroy after callback.
    remove_conn(s, fd);
}

static void update_interest(mqtt_tcp_server_t* s, mqtt_connection_t* c) {
    if (!s || !c || mqtt_connection_closed(c)) {
        return;
    }
    struct epoll_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.data.fd = mqtt_connection_fd(c);
    ev.events = EPOLLIN;
    if (mqtt_connection_want_write(c)) {
        ev.events |= EPOLLOUT;
    }
    (void)epoll_ctl(s->epoll_fd, EPOLL_CTL_MOD, mqtt_connection_fd(c), &ev);
}

static bool setup_listen_socket(mqtt_tcp_server_t* s) {
    s->listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (s->listen_fd < 0) {
        fprintf(stderr, "socket() failed: %s\n", strerror(errno));
        return false;
    }

    int opt = 1;
    (void)setsockopt(s->listen_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    if (!set_nonblocking(s->listen_fd)) {
        fprintf(stderr, "set_nonblocking(listen_fd) failed\n");
        return false;
    }

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons(s->port);

    if (bind(s->listen_fd, (struct sockaddr*)&addr, sizeof(addr)) != 0) {
        fprintf(stderr, "bind() failed: %s\n", strerror(errno));
        return false;
    }

    if (listen(s->listen_fd, 128) != 0) {
        fprintf(stderr, "listen() failed: %s\n", strerror(errno));
        return false;
    }

    return true;
}

static bool setup_epoll(mqtt_tcp_server_t* s) {
    s->epoll_fd = epoll_create1(0);
    if (s->epoll_fd < 0) {
        fprintf(stderr, "epoll_create1() failed: %s\n", strerror(errno));
        return false;
    }

    struct epoll_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.events = EPOLLIN;
    ev.data.fd = s->listen_fd;
    if (epoll_ctl(s->epoll_fd, EPOLL_CTL_ADD, s->listen_fd, &ev) != 0) {
        fprintf(stderr, "epoll_ctl(ADD listen) failed: %s\n", strerror(errno));
        return false;
    }

    return true;
}

static void accept_loop(mqtt_tcp_server_t* s) {
    while (1) {
        struct sockaddr_in peer;
        socklen_t len = sizeof(peer);
        const int client_fd = accept(s->listen_fd, (struct sockaddr*)&peer, &len);
        if (client_fd < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                return;
            }
            if (errno == EINTR) {
                continue;
            }
            fprintf(stderr, "accept() failed: %s\n", strerror(errno));
            return;
        }

        if (!set_nonblocking(client_fd)) {
            close(client_fd);
            continue;
        }

        mqtt_connection_t* c = mqtt_connection_create(client_fd);
        if (!c) {
            close(client_fd);
            continue;
        }
        char* peer_s = sockaddr_to_string(&peer);
        mqtt_connection_set_peer(c, peer_s);
        free(peer_s);

        conn_node_t* node = (conn_node_t*)calloc(1, sizeof(*node));
        if (!node) {
            mqtt_connection_destroy(c);
            continue;
        }
        node->c = c;
        node->next = s->conns;
        s->conns = node;

        struct epoll_event ev;
        memset(&ev, 0, sizeof(ev));
        ev.events = EPOLLIN;
        ev.data.fd = client_fd;
        if (epoll_ctl(s->epoll_fd, EPOLL_CTL_ADD, client_fd, &ev) != 0) {
            fprintf(stderr, "epoll_ctl(ADD client) failed: %s\n", strerror(errno));
            close_connection(s, c);
            continue;
        }

        if (s->cb.on_accept) {
            s->cb.on_accept(s->user, c);
        }
    }
}

mqtt_tcp_server_t* mqtt_tcp_server_create(uint16_t port, mqtt_tcp_callbacks_t cb, void* user) {
    mqtt_tcp_server_t* s = (mqtt_tcp_server_t*)calloc(1, sizeof(*s));
    if (!s) {
        return NULL;
    }
    s->port = port;
    s->cb = cb;
    s->user = user;
    s->listen_fd = -1;
    s->epoll_fd = -1;
    s->running = false;
    s->conns = NULL;
    return s;
}

void mqtt_tcp_server_destroy(mqtt_tcp_server_t* s) {
    if (!s) {
        return;
    }
    mqtt_tcp_server_stop(s);
    free(s);
}

bool mqtt_tcp_server_start(mqtt_tcp_server_t* s) {
    if (!s) {
        return false;
    }
    if (!setup_listen_socket(s)) {
        return false;
    }
    if (!setup_epoll(s)) {
        return false;
    }
    s->running = true;
    return true;
}

void mqtt_tcp_server_stop(mqtt_tcp_server_t* s) {
    if (!s) {
        return;
    }
    s->running = false;

    // close all connections
    while (s->conns) {
        mqtt_connection_t* c = s->conns->c;
        close_connection(s, c);
    }

    if (s->epoll_fd >= 0) {
        close(s->epoll_fd);
        s->epoll_fd = -1;
    }
    if (s->listen_fd >= 0) {
        close(s->listen_fd);
        s->listen_fd = -1;
    }
}

void mqtt_tcp_server_run(mqtt_tcp_server_t* s) {
    if (!s || !s->running) {
        return;
    }

    printf("MQTT TCP server listening on 0.0.0.0:%u\n", (unsigned)s->port);

    struct epoll_event events[MAX_EVENTS];
    while (s->running) {
        bool has_pending_write = false;
        for (conn_node_t* n = s->conns; n; n = n->next) {
            if (mqtt_connection_want_write(n->c)) {
                has_pending_write = true;
                break;
            }
        }

        const int timeout_ms = has_pending_write ? 0 : 1000;
        const int n = epoll_wait(s->epoll_fd, events, MAX_EVENTS, timeout_ms);
        if (n < 0) {
            if (errno == EINTR) {
                continue;
            }
            fprintf(stderr, "epoll_wait() failed: %s\n", strerror(errno));
            break;
        }

        for (int i = 0; i < n; ++i) {
            const int fd = events[i].data.fd;
            const uint32_t ev = events[i].events;

            if (fd == s->listen_fd) {
                accept_loop(s);
                continue;
            }

            mqtt_connection_t* c = find_conn(s, fd);
            if (!c) {
                continue;
            }

            if (ev & (EPOLLHUP | EPOLLERR)) {
                close_connection(s, c);
                continue;
            }

            if (ev & EPOLLIN) {
                bool peer_closed = false;
                if (!mqtt_connection_read(c, &peer_closed)) {
                    close_connection(s, c);
                    continue;
                }
                if (s->cb.on_data) {
                    s->cb.on_data(s->user, c);
                }
                if (peer_closed) {
                    close_connection(s, c);
                    continue;
                }
            }

            if (ev & EPOLLOUT) {
                if (!mqtt_connection_flush(c)) {
                    close_connection(s, c);
                    continue;
                }
            }

            update_interest(s, c);
        }

        // progress write queues even without EPOLLOUT events
        if (s->conns) {
            conn_node_t* cur = s->conns;
            // collect fds to close to avoid messing list during iteration
            int* close_fds = NULL;
            size_t close_cnt = 0;
            size_t close_cap = 0;

            while (cur) {
                mqtt_connection_t* c = cur->c;
                if (c && !mqtt_connection_closed(c) && mqtt_connection_want_write(c)) {
                    if (!mqtt_connection_flush(c)) {
                        if (close_cnt == close_cap) {
                            size_t nc = close_cap ? close_cap * 2 : 8;
                            int* p = (int*)realloc(close_fds, nc * sizeof(int));
                            if (p) {
                                close_fds = p;
                                close_cap = nc;
                            }
                        }
                        if (close_cnt < close_cap) {
                            close_fds[close_cnt++] = mqtt_connection_fd(c);
                        }
                    } else {
                        update_interest(s, c);
                    }
                }
                cur = cur->next;
            }

            for (size_t k = 0; k < close_cnt; ++k) {
                mqtt_connection_t* c = find_conn(s, close_fds[k]);
                if (c) {
                    close_connection(s, c);
                }
            }
            free(close_fds);
        }
    }
}
