#include "http_response.h"

#include "../network/connection.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

const char* http_status_text(int code) {
    switch (code) {
        case 200: return "OK";
        case 201: return "Created";
        case 400: return "Bad Request";
        case 403: return "Forbidden";
        case 404: return "Not Found";
        case 405: return "Method Not Allowed";
        case 413: return "Payload Too Large";
        case 500: return "Internal Server Error";
        case 501: return "Not Implemented";
        default:  return "Unknown";
    }
}

static int queuefv(http_connection_t* conn, const char* fmt, ...) {
    char buf[4096];
    va_list ap;
    va_start(ap, fmt);
    const int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n < 0 || (size_t)n >= sizeof(buf)) {
        return -1;
    }
    return http_connection_queue_str(conn, buf);
}

int http_response_send(http_connection_t* conn,
                       int status,
                       const char** extra_headers,
                       const void* body, size_t body_len) {
    if (conn == NULL) {
        return -1;
    }

    /* Status line */
    if (queuefv(conn, "HTTP/1.1 %d %s\r\n", status, http_status_text(status)) < 0) {
        return -1;
    }

    /* Date header */
    {
        char date_buf[128];
        time_t now = time(NULL);
        struct tm tm;
        gmtime_r(&now, &tm);
        strftime(date_buf, sizeof(date_buf), "Date: %a, %d %b %Y %H:%M:%S GMT\r\n", &tm);
        if (http_connection_queue_str(conn, date_buf) < 0) {
            return -1;
        }
    }

    /* Server */
    if (http_connection_queue_str(conn, "Server: httpd/1.0\r\n") < 0) {
        return -1;
    }

    /* Content-Length */
    if (queuefv(conn, "Content-Length: %zu\r\n", body_len) < 0) {
        return -1;
    }

    /* Connection: close by default for simplicity */
    if (http_connection_queue_str(conn, "Connection: close\r\n") < 0) {
        return -1;
    }

    /* Extra headers */
    if (extra_headers != NULL) {
        for (const char** h = extra_headers; *h != NULL; ++h) {
            if (http_connection_queue_str(conn, *h) < 0 ||
                http_connection_queue_str(conn, "\r\n") < 0) {
                return -1;
            }
        }
    }

    /* Blank line */
    if (http_connection_queue_str(conn, "\r\n") < 0) {
        return -1;
    }

    /* Body */
    if (body != NULL && body_len > 0) {
        if (http_connection_queue(conn, body, body_len) < 0) {
            return -1;
        }
    }

    return 0;
}

int http_response_send_html(http_connection_t* conn,
                            int status,
                            const char* title,
                            const char* fmt, ...) {
    if (conn == NULL || title == NULL || fmt == NULL) {
        return -1;
    }

    char content[8192];
    va_list ap;
    va_start(ap, fmt);
    const int content_n = vsnprintf(content, sizeof(content), fmt, ap);
    va_end(ap);
    if (content_n < 0 || (size_t)content_n >= sizeof(content)) {
        return -1;
    }

    char body[16384];
    const int body_n = snprintf(body, sizeof(body),
        "<!DOCTYPE html>\r\n"
        "<html><head><meta charset=\"utf-8\">"
        "<title>%s</title></head>\r\n"
        "<body>%s</body></html>\r\n",
        title, content);
    if (body_n < 0 || (size_t)body_n >= sizeof(body)) {
        return -1;
    }

    const char* extra[] = {"Content-Type: text/html; charset=utf-8", NULL};
    return http_response_send(conn, status, extra, body, (size_t)body_n);
}
