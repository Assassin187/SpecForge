#include "tcp_server.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/epoll.h>
#include <sys/socket.h>
#include <unistd.h>

typedef struct ftp_client_node {
    int fd;
    bool want_write;
    struct ftp_client_node* next;
} ftp_client_node_t;

struct ftp_tcp_server {
    uint16_t port;
    int listen_fd;
    int epoll_fd;
    bool running;
    ftp_tcp_callbacks_t callbacks;
    void* user;
    ftp_client_node_t* clients;
};

static int set_nonblocking(int fd) {
    const int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0) {
        return -1;
    }
    if (fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0) {
        return -1;
    }
    return 0;
}

static ftp_client_node_t* find_client(ftp_tcp_server_t* server, int fd) {
    ftp_client_node_t* cur = server->clients;
    while (cur != NULL) {
        if (cur->fd == fd) {
            return cur;
        }
        cur = cur->next;
    }
    return NULL;
}

static int add_client(ftp_tcp_server_t* server, int fd) {
    ftp_client_node_t* node = (ftp_client_node_t*)calloc(1, sizeof(*node));
    if (node == NULL) {
        return -1;
    }

    struct epoll_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.data.fd = fd;
    ev.events = EPOLLIN | EPOLLRDHUP;

    if (epoll_ctl(server->epoll_fd, EPOLL_CTL_ADD, fd, &ev) < 0) {
        free(node);
        return -1;
    }

    node->fd = fd;
    node->want_write = false;
    node->next = server->clients;
    server->clients = node;
    return 0;
}

static void remove_client_node(ftp_tcp_server_t* server, int fd) {
    ftp_client_node_t** cur = &server->clients;
    while (*cur != NULL) {
        if ((*cur)->fd == fd) {
            ftp_client_node_t* dead = *cur;
            *cur = dead->next;
            free(dead);
            return;
        }
        cur = &(*cur)->next;
    }
}

int ftp_tcp_server_close_client(ftp_tcp_server_t* server, int fd) {
    if (server == NULL || fd < 0) {
        return -1;
    }

    if (find_client(server, fd) == NULL) {
        return -1;
    }

    epoll_ctl(server->epoll_fd, EPOLL_CTL_DEL, fd, NULL);
    close(fd);
    remove_client_node(server, fd);

    if (server->callbacks.on_close != NULL) {
        server->callbacks.on_close(server->user, fd);
    }

    return 0;
}

ftp_tcp_server_t* ftp_tcp_server_create(uint16_t port, ftp_tcp_callbacks_t callbacks, void* user) {
    ftp_tcp_server_t* server = (ftp_tcp_server_t*)calloc(1, sizeof(*server));
    if (server == NULL) {
        return NULL;
    }

    server->port = port;
    server->listen_fd = -1;
    server->epoll_fd = -1;
    server->callbacks = callbacks;
    server->user = user;
    return server;
}

int ftp_tcp_server_update_interest(ftp_tcp_server_t* server, int fd, bool want_write) {
    if (server == NULL || fd < 0) {
        return -1;
    }

    ftp_client_node_t* node = find_client(server, fd);
    if (node == NULL) {
        return -1;
    }

    if (node->want_write == want_write) {
        return 0;
    }

    struct epoll_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.data.fd = fd;
    ev.events = EPOLLIN | EPOLLRDHUP;
    if (want_write) {
        ev.events |= EPOLLOUT;
    }

    if (epoll_ctl(server->epoll_fd, EPOLL_CTL_MOD, fd, &ev) < 0) {
        return -1;
    }

    node->want_write = want_write;
    return 0;
}

static int setup_listener(ftp_tcp_server_t* server) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        return -1;
    }

    int opt = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    if (set_nonblocking(fd) < 0) {
        close(fd);
        return -1;
    }

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons(server->port);

    if (bind(fd, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        close(fd);
        return -1;
    }

    if (listen(fd, 128) < 0) {
        close(fd);
        return -1;
    }

    server->listen_fd = fd;
    return 0;
}

int ftp_tcp_server_start(ftp_tcp_server_t* server) {
    if (server == NULL) {
        return -1;
    }

    if (setup_listener(server) < 0) {
        return -1;
    }

    server->epoll_fd = epoll_create1(0);
    if (server->epoll_fd < 0) {
        close(server->listen_fd);
        server->listen_fd = -1;
        return -1;
    }

    struct epoll_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.data.fd = server->listen_fd;
    ev.events = EPOLLIN;

    if (epoll_ctl(server->epoll_fd, EPOLL_CTL_ADD, server->listen_fd, &ev) < 0) {
        close(server->epoll_fd);
        close(server->listen_fd);
        server->epoll_fd = -1;
        server->listen_fd = -1;
        return -1;
    }

    return 0;
}

static void accept_loop(ftp_tcp_server_t* server) {
    while (true) {
        struct sockaddr_storage peer;
        socklen_t peer_len = sizeof(peer);
        const int client_fd = accept(server->listen_fd, (struct sockaddr*)&peer, &peer_len);
        if (client_fd < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                return;
            }
            return;
        }

        if (set_nonblocking(client_fd) < 0 || add_client(server, client_fd) < 0) {
            close(client_fd);
            continue;
        }

        if (server->callbacks.on_accept != NULL) {
            server->callbacks.on_accept(server->user, client_fd, &peer, peer_len);
        }
    }
}

int ftp_tcp_server_run(ftp_tcp_server_t* server) {
    if (server == NULL || server->epoll_fd < 0 || server->listen_fd < 0) {
        return -1;
    }

    server->running = true;
    struct epoll_event events[64];

    while (server->running) {
        const int n = epoll_wait(server->epoll_fd, events, 64, 1000);
        if (n < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }

        for (int i = 0; i < n; ++i) {
            const int fd = events[i].data.fd;
            const uint32_t ev = events[i].events;

            if (fd == server->listen_fd) {
                accept_loop(server);
                continue;
            }

            if (find_client(server, fd) == NULL) {
                continue;
            }

            if ((ev & (EPOLLERR | EPOLLHUP | EPOLLRDHUP)) != 0u) {
                ftp_tcp_server_close_client(server, fd);
                continue;
            }

            uint32_t app_ev = 0;
            if ((ev & EPOLLIN) != 0u) {
                app_ev |= FTP_IO_READABLE;
            }
            if ((ev & EPOLLOUT) != 0u) {
                app_ev |= FTP_IO_WRITABLE;
            }

            if (app_ev != 0u && server->callbacks.on_event != NULL) {
                server->callbacks.on_event(server->user, fd, app_ev);
            }
        }
    }

    return 0;
}

void ftp_tcp_server_stop(ftp_tcp_server_t* server) {
    if (server != NULL) {
        server->running = false;
    }
}

void ftp_tcp_server_destroy(ftp_tcp_server_t* server) {
    if (server == NULL) {
        return;
    }

    ftp_client_node_t* cur = server->clients;
    while (cur != NULL) {
        ftp_client_node_t* next = cur->next;
        close(cur->fd);
        free(cur);
        cur = next;
    }

    if (server->listen_fd >= 0) {
        close(server->listen_fd);
    }
    if (server->epoll_fd >= 0) {
        close(server->epoll_fd);
    }

    free(server);
}
