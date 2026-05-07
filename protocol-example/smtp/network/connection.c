#include "connection.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

struct smtp_connection {
    int fd;
    uint8_t* in_buf;
    size_t in_len;
    size_t in_cap;
    uint8_t* out_buf;
    size_t out_len;
    size_t out_off;
    size_t out_cap;
};

static int ensure_cap(uint8_t** buf, size_t* cap, size_t needed) {
    if (needed <= *cap) {
        return 0;
    }

    size_t next = (*cap == 0) ? 4096 : *cap;
    while (next < needed) {
        next *= 2;
    }

    uint8_t* p = (uint8_t*)realloc(*buf, next);
    if (p == NULL) {
        return -1;
    }

    *buf = p;
    *cap = next;
    return 0;
}

smtp_connection_t* smtp_connection_create(int fd) {
    smtp_connection_t* conn = (smtp_connection_t*)calloc(1, sizeof(*conn));
    if (conn == NULL) {
        return NULL;
    }
    conn->fd = fd;
    return conn;
}

void smtp_connection_destroy(smtp_connection_t* conn) {
    if (conn == NULL) {
        return;
    }
    free(conn->in_buf);
    free(conn->out_buf);
    free(conn);
}

int smtp_connection_fd(const smtp_connection_t* conn) {
    return conn == NULL ? -1 : conn->fd;
}

int smtp_connection_read(smtp_connection_t* conn) {
    if (conn == NULL) {
        return -1;
    }

    int total = 0;
    uint8_t tmp[4096];

    while (1) {
        const ssize_t n = recv(conn->fd, tmp, sizeof(tmp), 0);
        if (n > 0) {
            if (ensure_cap(&conn->in_buf, &conn->in_cap, conn->in_len + (size_t)n + 1) < 0) {
                return -1;
            }
            memcpy(conn->in_buf + conn->in_len, tmp, (size_t)n);
            conn->in_len += (size_t)n;
            conn->in_buf[conn->in_len] = '\0';
            total += (int)n;
            continue;
        }

        if (n == 0) {
            return total > 0 ? total : 0;
        }

        if (errno == EINTR) {
            continue;
        }
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            return total > 0 ? total : -2;
        }
        return -1;
    }
}

char* smtp_connection_pop_line(smtp_connection_t* conn) {
    if (conn == NULL || conn->in_len == 0) {
        return NULL;
    }

    size_t pos = 0;
    while (pos < conn->in_len && conn->in_buf[pos] != '\n') {
        ++pos;
    }
    if (pos == conn->in_len) {
        return NULL;
    }

    size_t line_len = pos;
    if (line_len > 0 && conn->in_buf[line_len - 1] == '\r') {
        --line_len;
    }

    char* line = (char*)malloc(line_len + 1);
    if (line == NULL) {
        return NULL;
    }
    if (line_len > 0) {
        memcpy(line, conn->in_buf, line_len);
    }
    line[line_len] = '\0';

    const size_t consume = pos + 1;
    const size_t remain = conn->in_len - consume;
    if (remain > 0) {
        memmove(conn->in_buf, conn->in_buf + consume, remain);
    }
    conn->in_len = remain;
    if (conn->in_buf != NULL && conn->in_cap > 0) {
        conn->in_buf[conn->in_len] = '\0';
    }

    return line;
}

int smtp_connection_queue(smtp_connection_t* conn, const void* data, size_t len) {
    if (conn == NULL || (data == NULL && len != 0)) {
        return -1;
    }
    if (len == 0) {
        return 0;
    }

    if (ensure_cap(&conn->out_buf, &conn->out_cap, conn->out_len + len) < 0) {
        return -1;
    }

    memcpy(conn->out_buf + conn->out_len, data, len);
    conn->out_len += len;
    return 0;
}

int smtp_connection_queue_str(smtp_connection_t* conn, const char* text) {
    if (text == NULL) {
        return -1;
    }
    return smtp_connection_queue(conn, text, strlen(text));
}

bool smtp_connection_has_pending(const smtp_connection_t* conn) {
    if (conn == NULL) {
        return false;
    }
    return conn->out_off < conn->out_len;
}

int smtp_connection_flush(smtp_connection_t* conn) {
    if (conn == NULL) {
        return -1;
    }

    while (conn->out_off < conn->out_len) {
        const size_t remaining = conn->out_len - conn->out_off;
        const ssize_t n = send(conn->fd, conn->out_buf + conn->out_off, remaining, 0);
        if (n > 0) {
            conn->out_off += (size_t)n;
            continue;
        }

        if (n < 0 && errno == EINTR) {
            continue;
        }
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            return 1;
        }
        return -1;
    }

    conn->out_len = 0;
    conn->out_off = 0;
    return 0;
}
