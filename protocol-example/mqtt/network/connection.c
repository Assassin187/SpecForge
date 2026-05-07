#include "connection.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif

enum { READ_CHUNK = 16 * 1024 };

typedef struct out_chunk {
    uint8_t* data;
    size_t len;
    size_t off;
    struct out_chunk* next;
} out_chunk_t;

struct mqtt_connection {
    int fd;
    bool closed;

    uint8_t* in_data;
    size_t in_len;
    size_t in_cap;

    out_chunk_t* out_head;
    out_chunk_t* out_tail;

    char* peer;
};

static bool ensure_in_cap(mqtt_connection_t* c, size_t need) {
    if (need <= c->in_cap) {
        return true;
    }
    size_t cap = c->in_cap ? c->in_cap : (size_t)READ_CHUNK;
    while (cap < need) {
        cap *= 2;
    }
    uint8_t* p = (uint8_t*)realloc(c->in_data, cap);
    if (!p) {
        return false;
    }
    c->in_data = p;
    c->in_cap = cap;
    return true;
}

mqtt_connection_t* mqtt_connection_create(int fd) {
    mqtt_connection_t* c = (mqtt_connection_t*)calloc(1, sizeof(*c));
    if (!c) {
        return NULL;
    }
    c->fd = fd;
    c->closed = false;
    c->in_data = NULL;
    c->in_len = 0;
    c->in_cap = 0;
    c->out_head = NULL;
    c->out_tail = NULL;
    c->peer = NULL;
    return c;
}

void mqtt_connection_destroy(mqtt_connection_t* c) {
    if (!c) {
        return;
    }
    mqtt_connection_close(c);

    free(c->in_data);

    out_chunk_t* cur = c->out_head;
    while (cur) {
        out_chunk_t* next = cur->next;
        free(cur->data);
        free(cur);
        cur = next;
    }

    free(c->peer);
    free(c);
}

int mqtt_connection_fd(const mqtt_connection_t* c) {
    return c ? c->fd : -1;
}

bool mqtt_connection_closed(const mqtt_connection_t* c) {
    return c ? c->closed : true;
}

const char* mqtt_connection_peer(const mqtt_connection_t* c) {
    return c && c->peer ? c->peer : "";
}

void mqtt_connection_set_peer(mqtt_connection_t* c, const char* peer) {
    if (!c) {
        return;
    }
    free(c->peer);
    c->peer = NULL;
    if (peer) {
        c->peer = strdup(peer);
    }
}

void mqtt_connection_close(mqtt_connection_t* c) {
    if (!c || c->closed) {
        return;
    }
    c->closed = true;
    if (c->fd >= 0) {
        close(c->fd);
        c->fd = -1;
    }
}

bool mqtt_connection_read(mqtt_connection_t* c, bool* peer_closed) {
    if (peer_closed) {
        *peer_closed = false;
    }
    if (!c || c->closed || c->fd < 0) {
        if (peer_closed) {
            *peer_closed = true;
        }
        return false;
    }

    uint8_t buf[READ_CHUNK];
    while (1) {
        const ssize_t n = recv(c->fd, buf, sizeof(buf), 0);
        if (n > 0) {
            const size_t new_len = c->in_len + (size_t)n;
            if (!ensure_in_cap(c, new_len)) {
                return false;
            }
            memcpy(c->in_data + c->in_len, buf, (size_t)n);
            c->in_len = new_len;
            continue;
        }
        if (n == 0) {
            if (peer_closed) {
                *peer_closed = true;
            }
            return true; // allow processing buffered bytes
        }
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            return true;
        }
        if (errno == EINTR) {
            continue;
        }
        if (peer_closed) {
            *peer_closed = true;
        }
        return false;
    }
}

bool mqtt_connection_flush(mqtt_connection_t* c) {
    if (!c || c->closed || c->fd < 0) {
        return false;
    }

    while (c->out_head) {
        out_chunk_t* ch = c->out_head;
        const uint8_t* data = ch->data + ch->off;
        const size_t len = ch->len - ch->off;
        const ssize_t n = send(c->fd, data, len, MSG_NOSIGNAL);
        if (n > 0) {
            ch->off += (size_t)n;
            if (ch->off >= ch->len) {
                c->out_head = ch->next;
                if (!c->out_head) {
                    c->out_tail = NULL;
                }
                free(ch->data);
                free(ch);
            }
            continue;
        }
        if (n == 0) {
            return false;
        }
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            return true;
        }
        if (errno == EINTR) {
            continue;
        }
        return false;
    }

    return true;
}

void mqtt_connection_send(mqtt_connection_t* c, const uint8_t* data, size_t len) {
    if (!c || c->closed || !data || len == 0) {
        return;
    }

    out_chunk_t* ch = (out_chunk_t*)calloc(1, sizeof(*ch));
    if (!ch) {
        return;
    }
    ch->data = (uint8_t*)malloc(len);
    if (!ch->data) {
        free(ch);
        return;
    }
    memcpy(ch->data, data, len);
    ch->len = len;
    ch->off = 0;
    ch->next = NULL;

    if (!c->out_tail) {
        c->out_head = c->out_tail = ch;
    } else {
        c->out_tail->next = ch;
        c->out_tail = ch;
    }
}

uint8_t* mqtt_connection_in_data(mqtt_connection_t* c) {
    return c ? c->in_data : NULL;
}

size_t mqtt_connection_in_len(const mqtt_connection_t* c) {
    return c ? c->in_len : 0;
}

void mqtt_connection_in_consume(mqtt_connection_t* c, size_t n) {
    if (!c || n == 0) {
        return;
    }
    if (n >= c->in_len) {
        c->in_len = 0;
        return;
    }
    memmove(c->in_data, c->in_data + n, c->in_len - n);
    c->in_len -= n;
}

bool mqtt_connection_want_write(const mqtt_connection_t* c) {
    return c && c->out_head != NULL;
}
