#include "network/tcp_server.h"

#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/epoll.h>
#include <errno.h>

struct conn_node {
    mqtt_connection_t* conn;
    struct conn_node* next;
};

struct mqtt_tcp_server {
    uint16_t port;
    mqtt_tcp_callbacks_t cb;
    void* user;

    int listen_fd;
    int epoll_fd;
    bool running;

    struct conn_node* conns;
};

static bool set_nonblocking(int fd) {
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags == -1) {
        return false;
    }
    return fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0;
}

static char* sockaddr_to_string(const struct sockaddr_in* addr) {
    char ip_str[INET_ADDRSTRLEN];
    if (!inet_ntop(AF_INET, &addr->sin_addr, ip_str, sizeof(ip_str))) {
        return NULL;
    }
    size_t len = strlen(ip_str) + 1 + 5 + 1; // ip:port\0
    char* buf = malloc(len);
    if (!buf) {
        return NULL;
    }
    snprintf(buf, len, "%s:%d", ip_str, ntohs(addr->sin_port));
    return buf;
}

static mqtt_connection_t* find_conn(mqtt_tcp_server_t* s, int fd) {
    for (struct conn_node* node = s->conns; node; node = node->next) {
        if (mqtt_connection_fd(node->conn) == fd) {
            return node->conn;
        }
    }
    return NULL;
}

static void remove_conn(mqtt_tcp_server_t* s, int fd) {
    struct conn_node** pp = &s->conns;
    while (*pp) {
        if (mqtt_connection_fd((*pp)->conn) == fd) {
            struct conn_node* to_free = *pp;
            *pp = to_free->next;
            mqtt_connection_destroy(to_free->conn);
            free(to_free);
            return;
        }
        pp = &(*pp)->next;
    }
}

static void close_connection(mqtt_tcp_server_t* s, mqtt_connection_t* c) {
    int fd = mqtt_connection_fd(c);
    epoll_ctl(s->epoll_fd, EPOLL_CTL_DEL, fd, NULL);
    if (s->cb.on_close) {
        s->cb.on_close(s->user, c);
    }
    remove_conn(s, fd);
}

static void update_interest(mqtt_tcp_server_t* s, mqtt_connection_t* c) {
    if (mqtt_connection_closed(c)) {
        return;
    }

    struct epoll_event ev = {0};
    ev.data.fd = mqtt_connection_fd(c);
    ev.events = EPOLLIN;
    if (mqtt_connection_want_write(c)) {
        ev.events |= EPOLLOUT;
    }

    if (epoll_ctl(s->epoll_fd, EPOLL_CTL_MOD, mqtt_connection_fd(c), &ev) == -1) {
        close_connection(s, c);
    }
}

static bool setup_listen_socket(mqtt_tcp_server_t* s) {
    s->listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (s->listen_fd == -1) {
        return false;
    }

    int reuse = 1;
    if (setsockopt(s->listen_fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse)) == -1) {
        close(s->listen_fd);
        s->listen_fd = -1;
        return false;
    }

    if (!set_nonblocking(s->listen_fd)) {
        close(s->listen_fd);
        s->listen_fd = -1;
        return false;
    }

    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons(s->port);

    if (bind(s->listen_fd, (struct sockaddr*)&addr, sizeof(addr)) == -1) {
        close(s->listen_fd);
        s->listen_fd = -1;
        return false;
    }

    if (listen(s->listen_fd, SOMAXCONN) == -1) {
        close(s->listen_fd);
        s->listen_fd = -1;
        return false;
    }

    return true;
}

static bool setup_epoll(mqtt_tcp_server_t* s) {
    s->epoll_fd = epoll_create1(EPOLL_CLOEXEC);
    if (s->epoll_fd == -1) {
        return false;
    }

    struct epoll_event ev = {0};
    ev.events = EPOLLIN;
    ev.data.fd = s->listen_fd;

    if (epoll_ctl(s->epoll_fd, EPOLL_CTL_ADD, s->listen_fd, &ev) == -1) {
        close(s->epoll_fd);
        s->epoll_fd = -1;
        return false;
    }

    return true;
}

static void accept_loop(mqtt_tcp_server_t* s) {
    struct sockaddr_in client_addr;
    socklen_t addr_len = sizeof(client_addr);

    while (true) {
        int client_fd = accept(s->listen_fd, (struct sockaddr*)&client_addr, &addr_len);
        if (client_fd == -1) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                break;
            }
            continue;
        }

        if (!set_nonblocking(client_fd)) {
            close(client_fd);
            continue;
        }

        mqtt_connection_t* conn = mqtt_connection_create(client_fd);
        if (!conn) {
            close(client_fd);
            continue;
        }

        char* peer_str = sockaddr_to_string(&client_addr);
        if (peer_str) {
            mqtt_connection_set_peer(conn, peer_str);
            free(peer_str);
        }

        struct conn_node* node = malloc(sizeof(struct conn_node));
        if (!node) {
            mqtt_connection_destroy(conn);
            close(client_fd);
            continue;
        }
        node->conn = conn;
        node->next = s->conns;
        s->conns = node;

        struct epoll_event ev = {0};
        ev.events = EPOLLIN;
        ev.data.fd = client_fd;

        if (epoll_ctl(s->epoll_fd, EPOLL_CTL_ADD, client_fd, &ev) == -1) {
            close_connection(s, conn);
            continue;
        }

        if (s->cb.on_accept) {
            s->cb.on_accept(s->user, conn);
        }
    }
}

mqtt_tcp_server_t* mqtt_tcp_server_create(uint16_t port, mqtt_tcp_callbacks_t cb, void* user) {
    mqtt_tcp_server_t* s = malloc(sizeof(mqtt_tcp_server_t));
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
        close(s->listen_fd);
        s->listen_fd = -1;
        return false;
    }

    s->running = true;
    return true;
}

void mqtt_tcp_server_run(mqtt_tcp_server_t* s) {
    if (!s || !s->running) {
        return;
    }

    struct epoll_event events[64];
    while (s->running) {
        int nfds = epoll_wait(s->epoll_fd, events, 64, -1);
        if (nfds == -1) {
            if (errno == EINTR) {
                continue;
            }
            break;
        }

        for (int i = 0; i < nfds; i++) {
            int fd = events[i].data.fd;
            uint32_t events_occurred = events[i].events;

            if (fd == s->listen_fd) {
                if (events_occurred & EPOLLIN) {
                    accept_loop(s);
                }
                continue;
            }

            mqtt_connection_t* c = find_conn(s, fd);
            if (!c) {
                continue;
            }

            if (events_occurred & (EPOLLERR | EPOLLHUP)) {
                close_connection(s, c);
                continue;
            }

            if (events_occurred & EPOLLOUT) {
                if (!mqtt_connection_flush(c)) {
                    close_connection(s, c);
                    continue;
                }
            }

            if (events_occurred & EPOLLIN) {
                bool peer_closed = false;
                if (!mqtt_connection_read(c, &peer_closed)) {
                    close_connection(s, c);
                    continue;
                }
                if (peer_closed) {
                    close_connection(s, c);
                    continue;
                }
                if (s->cb.on_data) {
                    s->cb.on_data(s->user, c);
                }
            }

            update_interest(s, c);
        }
    }
}

void mqtt_tcp_server_stop(mqtt_tcp_server_t* s) {
    if (!s || !s->running) {
        return;
    }

    s->running = false;

    while (s->conns) {
        close_connection(s, s->conns->conn);
    }

    if (s->epoll_fd != -1) {
        close(s->epoll_fd);
        s->epoll_fd = -1;
    }

    if (s->listen_fd != -1) {
        close(s->listen_fd);
        s->listen_fd = -1;
    }
}
