#include "http_server.h"

#include "../network/tcp_server.h"
#include "../protocol/http_request.h"
#include "../protocol/http_response.h"
#include "../resource/file_ops.h"
#include "http_session.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

typedef struct http_session_node {
    http_session_t* session;
    struct http_session_node* next;
} http_session_node_t;

struct http_server {
    uint16_t port;
    char root[PATH_MAX];
    http_tcp_server_t* tcp;
    http_session_node_t* sessions;
};

/* --- session list helpers ------------------------------------------------- */

static http_session_t* find_session(http_server_t* server, int fd) {
    http_session_node_t* cur = server->sessions;
    while (cur != NULL) {
        if (cur->session->fd == fd) {
            return cur->session;
        }
        cur = cur->next;
    }
    return NULL;
}

static void remove_session(http_server_t* server, int fd) {
    http_session_node_t** cur = &server->sessions;
    while (*cur != NULL) {
        if ((*cur)->session->fd == fd) {
            http_session_node_t* dead = *cur;
            *cur = dead->next;
            http_session_destroy(dead->session);
            free(dead);
            return;
        }
        cur = &(*cur)->next;
    }
}

static int add_session(http_server_t* server, http_session_t* session) {
    http_session_node_t* node = (http_session_node_t*)calloc(1, sizeof(*node));
    if (node == NULL) {
        return -1;
    }
    node->session = session;
    node->next = server->sessions;
    server->sessions = node;
    return 0;
}

/* --- response helpers ----------------------------------------------------- */

static int send_error(http_server_t* server, http_session_t* session, int code) {
    const char* text = http_status_text(code);
    char body[1024];
    snprintf(body, sizeof(body),
        "<!DOCTYPE html>\r\n"
        "<html><head><meta charset=\"utf-8\">"
        "<title>%d %s</title></head>\r\n"
        "<body><h1>%d %s</h1></body></html>\r\n",
        code, text, code, text);

    const char* extra[] = {"Content-Type: text/html; charset=utf-8", NULL};
    const int rc = http_response_send(session->conn, code, extra, body, strlen(body));
    if (rc < 0) {
        return -1;
    }
    return http_tcp_server_update_interest(server->tcp, session->fd, true);
}

static int flush_and_update(http_server_t* server, http_session_t* session) {
    const int rc = http_connection_flush(session->conn);
    if (rc < 0) {
        return -1;
    }
    return http_tcp_server_update_interest(server->tcp, session->fd,
        http_connection_has_pending(session->conn));
}

/* --- request handlers ----------------------------------------------------- */

/* GET and HEAD share file-serving logic; HEAD omits the body. */
static int serve_file(http_server_t* server, http_session_t* session,
                      const http_request_t* req, bool head_only) {
    /* Resolve URI to filesystem path */
    char abs_path[PATH_MAX];
    if (http_resolve_path(server->root, req->uri, abs_path, sizeof(abs_path)) < 0) {
        return send_error(server, session, 403);
    }

    const int st = http_stat_path(abs_path);
    if (st < 0) {
        return send_error(server, session, 404);
    }

    /* Directory -> try index.html first, else listing */
    if (st == 1) {
        /* Try index.html inside the directory */
        char index_path[PATH_MAX + 12];
        snprintf(index_path, sizeof(index_path), "%s/index.html", abs_path);

        struct stat idx_st;
        if (stat(index_path, &idx_st) == 0 && S_ISREG(idx_st.st_mode)) {
            /* Serve index.html */
            strncpy(abs_path, index_path, sizeof(abs_path) - 1);
            abs_path[sizeof(abs_path) - 1] = '\0';
            goto serve_reg;
        }

        /* Generate directory listing */
        char* listing = http_dir_listing_html(abs_path, req->uri);
        if (listing == NULL) {
            return send_error(server, session, 500);
        }

        const char* extra[] = {"Content-Type: text/html; charset=utf-8", NULL};
        if (head_only) {
            http_response_send(session->conn, 200, extra, NULL, strlen(listing));
        } else {
            http_response_send(session->conn, 200, extra, listing, strlen(listing));
        }
        free(listing);
        return http_tcp_server_update_interest(server->tcp, session->fd, true);
    }

serve_reg:
    ;
    /* Read file */
    char* file_data = NULL;
    size_t file_len = 0;
    if (http_read_file(abs_path, &file_data, &file_len) < 0) {
        return send_error(server, session, 500);
    }

    /* Build Content-Type header */
    char ct_buf[256];
    snprintf(ct_buf, sizeof(ct_buf), "Content-Type: %s", http_mime_by_ext(abs_path));

    const char* extra[] = {ct_buf, NULL};

    if (head_only) {
        http_response_send(session->conn, 200, extra, NULL, file_len);
    } else {
        http_response_send(session->conn, 200, extra, file_data, file_len);
    }
    free(file_data);

    return http_tcp_server_update_interest(server->tcp, session->fd, true);
}

static int handle_get(http_server_t* server, http_session_t* session,
                      const http_request_t* req) {
    return serve_file(server, session, req, false);
}

static int handle_head(http_server_t* server, http_session_t* session,
                       const http_request_t* req) {
    return serve_file(server, session, req, true);
}

static int handle_post(http_server_t* server, http_session_t* session,
                       const http_request_t* req) {
    if (req->body == NULL || req->body_len == 0) {
        return send_error(server, session, 400);
    }

    /* Resolve the target path */
    char abs_path[PATH_MAX];
    if (http_resolve_path(server->root, req->uri, abs_path, sizeof(abs_path)) < 0) {
        return send_error(server, session, 403);
    }

    /* Don't allow POST to existing directories */
    struct stat st;
    if (stat(abs_path, &st) == 0 && S_ISDIR(st.st_mode)) {
        return send_error(server, session, 405);
    }

    /* Write the body to the file */
    FILE* fp = fopen(abs_path, "wb");
    if (fp == NULL) {
        return send_error(server, session, 500);
    }

    const size_t written = fwrite(req->body, 1, req->body_len, fp);
    fclose(fp);

    if (written != req->body_len) {
        unlink(abs_path);
        return send_error(server, session, 500);
    }

    /* Respond with 201 Created */
    char* body = NULL;
    const int body_n = snprintf(NULL, 0,
        "<!DOCTYPE html>\r\n"
        "<html><head><meta charset=\"utf-8\">"
        "<title>201 Created</title></head>\r\n"
        "<body><h1>201 Created</h1><p>%s</p></body></html>\r\n",
        req->uri);
    if (body_n <= 0) {
        return send_error(server, session, 500);
    }
    body = (char*)malloc((size_t)(body_n + 1));
    if (body == NULL) {
        return send_error(server, session, 500);
    }
    snprintf(body, (size_t)(body_n + 1),
        "<!DOCTYPE html>\r\n"
        "<html><head><meta charset=\"utf-8\">"
        "<title>201 Created</title></head>\r\n"
        "<body><h1>201 Created</h1><p>%s</p></body></html>\r\n",
        req->uri);

    const char* extra[] = {
        "Content-Type: text/html; charset=utf-8",
        NULL,
    };
    http_response_send(session->conn, 201, extra, body, (size_t)body_n);
    free(body);
    return http_tcp_server_update_interest(server->tcp, session->fd, true);
}

/* --- dispatch by method --------------------------------------------------- */

static int dispatch(http_server_t* server, http_session_t* session,
                    const http_request_t* req) {
    switch (req->method) {
        case HTTP_GET:
            return handle_get(server, session, req);
        case HTTP_HEAD:
            return handle_head(server, session, req);
        case HTTP_POST:
            return handle_post(server, session, req);
        case HTTP_UNKNOWN:
        default:
            return send_error(server, session, 501);
    }
}

/* --- event loop callbacks ------------------------------------------------- */

static void on_accept_cb(void* user, int fd,
                         const struct sockaddr_storage* peer,
                         socklen_t peer_len) {
    (void)peer;
    (void)peer_len;

    http_server_t* server = (http_server_t*)user;
    http_session_t* session = http_session_create(fd);
    if (session == NULL) {
        http_tcp_server_close_client(server->tcp, fd);
        return;
    }

    if (add_session(server, session) < 0) {
        http_session_destroy(session);
        http_tcp_server_close_client(server->tcp, fd);
        return;
    }
}

static void close_client(http_server_t* server, int fd) {
    http_tcp_server_close_client(server->tcp, fd);
}

static void on_event_cb(void* user, int fd, uint32_t events) {
    http_server_t* server = (http_server_t*)user;
    http_session_t* session = find_session(server, fd);
    if (session == NULL) {
        return;
    }

    /* --- Write-ready ------------------------------------------------------- */
    if ((events & HTTP_IO_WRITABLE) != 0u) {
        const int frc = http_connection_flush(session->conn);
        if (frc < 0) {
            close_client(server, fd);
            return;
        }
        if (!http_connection_has_pending(session->conn)) {
            http_tcp_server_update_interest(server->tcp, session->fd, false);
        }
    }

    /* --- Read-ready -------------------------------------------------------- */
    if ((events & HTTP_IO_READABLE) != 0u) {
        const int rrc = http_connection_read(session->conn);
        if (rrc == 0 || rrc == -1) {
            close_client(server, fd);
            return;
        }

        /* Process all complete requests in the buffer */
        for (;;) {
            http_request_t req;
            http_request_init(&req);

            const int prc = http_request_parse(&req, session->conn);
            if (prc == 1) {
                /* Incomplete request — need more data */
                http_request_free(&req);
                break;
            }
            if (prc < 0) {
                /* Parse error */
                http_request_free(&req);
                send_error(server, session, 400);
                flush_and_update(server, session);
                close_client(server, fd);
                return;
            }

            /* Dispatch */
            dispatch(server, session, &req);
            http_request_free(&req);

            /* Re-lookup — session may have been removed */
            session = find_session(server, fd);
            if (session == NULL) {
                return;
            }
        }

        /* Flush after processing */
        if (http_connection_has_pending(session->conn)) {
            flush_and_update(server, session);
        }

        /* HTTP always sets Connection: close — close after first response set */
        /* We close after all queued data is flushed */
        if (!http_connection_has_pending(session->conn)) {
            close_client(server, fd);
        }
    }
}

static void on_close_cb(void* user, int fd) {
    http_server_t* server = (http_server_t*)user;
    remove_session(server, fd);
}

/* --- public API ----------------------------------------------------------- */

http_server_t* http_server_create(uint16_t port, const char* root_dir) {
    if (root_dir == NULL) {
        return NULL;
    }

    http_server_t* server = (http_server_t*)calloc(1, sizeof(*server));
    if (server == NULL) {
        return NULL;
    }

    char real_root[PATH_MAX];
    if (realpath(root_dir, real_root) == NULL) {
        free(server);
        return NULL;
    }

    struct stat st;
    if (stat(real_root, &st) < 0 || !S_ISDIR(st.st_mode)) {
        free(server);
        return NULL;
    }

    server->port = port;
    strncpy(server->root, real_root, sizeof(server->root) - 1);
    server->root[sizeof(server->root) - 1] = '\0';

    http_tcp_callbacks_t cb;
    memset(&cb, 0, sizeof(cb));
    cb.on_accept = on_accept_cb;
    cb.on_event = on_event_cb;
    cb.on_close = on_close_cb;

    server->tcp = http_tcp_server_create(port, cb, server);
    if (server->tcp == NULL) {
        free(server);
        return NULL;
    }

    return server;
}

int http_server_start(http_server_t* server) {
    if (server == NULL) {
        return -1;
    }
    return http_tcp_server_start(server->tcp);
}

int http_server_run(http_server_t* server) {
    if (server == NULL) {
        return -1;
    }
    return http_tcp_server_run(server->tcp);
}

void http_server_stop(http_server_t* server) {
    if (server != NULL) {
        http_tcp_server_stop(server->tcp);
    }
}

void http_server_destroy(http_server_t* server) {
    if (server == NULL) {
        return;
    }

    http_session_node_t* cur = server->sessions;
    while (cur != NULL) {
        http_session_node_t* next = cur->next;
        http_session_destroy(cur->session);
        free(cur);
        cur = next;
    }

    http_tcp_server_destroy(server->tcp);
    free(server);
}
