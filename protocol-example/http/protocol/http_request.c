#include "http_request.h"

#include "../network/connection.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

void http_request_init(http_request_t* req) {
    if (req == NULL) {
        return;
    }
    memset(req, 0, sizeof(*req));
}

void http_request_free(http_request_t* req) {
    if (req == NULL) {
        return;
    }
    free(req->body);
    req->body = NULL;
    req->body_len = 0;
}

static http_method_t parse_method(const char* s) {
    if (strcmp(s, "GET") == 0) {
        return HTTP_GET;
    }
    if (strcmp(s, "HEAD") == 0) {
        return HTTP_HEAD;
    }
    if (strcmp(s, "POST") == 0) {
        return HTTP_POST;
    }
    return HTTP_UNKNOWN;
}

int http_request_parse(http_request_t* req, http_connection_t* conn) {
    if (req == NULL || conn == NULL) {
        return -1;
    }

    /* --- Parse request line ------------------------------------------------- */
    char* req_line = http_connection_pop_line(conn);
    if (req_line == NULL) {
        return 1; /* need more data */
    }

    char method_str[16] = {0};
    char uri[HTTP_MAX_URI] = {0};
    char version[16] = {0};

    if (sscanf(req_line, "%15s %2047s %15s", method_str, uri, version) != 3) {
        free(req_line);
        return -1;
    }
    free(req_line);

    req->method = parse_method(method_str);
    strncpy(req->uri, uri, sizeof(req->uri) - 1);
    req->uri[sizeof(req->uri) - 1] = '\0';

    /* --- Parse headers ------------------------------------------------------ */
    req->header_count = 0;
    while (req->header_count < HTTP_MAX_HEADERS) {
        char* line = http_connection_pop_line(conn);
        if (line == NULL) {
            return 1; /* need more data */
        }

        /* Empty line marks end of headers */
        if (line[0] == '\0') {
            free(line);
            break;
        }

        /* Split on first ':' */
        char* colon = strchr(line, ':');
        if (colon == NULL) {
            free(line);
            continue; /* skip malformed lines */
        }

        *colon = '\0';
        const char* name = line;
        char* val = colon + 1;
        while (*val == ' ' || *val == '\t') {
            ++val;
        }

        http_header_t* h = &req->headers[req->header_count];
        strncpy(h->name, name, sizeof(h->name) - 1);
        h->name[sizeof(h->name) - 1] = '\0';
        strncpy(h->value, val, sizeof(h->value) - 1);
        h->value[sizeof(h->value) - 1] = '\0';
        ++req->header_count;
        free(line);
    }

    /* --- Parse body --------------------------------------------------------- */
    const char* clen_str = http_request_header(req, "Content-Length");
    size_t content_length = 0;
    if (clen_str != NULL) {
        content_length = (size_t)strtoul(clen_str, NULL, 10);
        if (content_length > HTTP_MAX_BODY) {
            return -1; /* too large */
        }
    }

    if (content_length > 0) {
        if (http_connection_buffered(conn) < content_length) {
            return 1; /* need more data */
        }
        req->body = http_connection_pop_bytes(conn, content_length);
        if (req->body == NULL) {
            return -1;
        }
        req->body_len = content_length;
    }

    return 0;
}

const char* http_request_header(const http_request_t* req, const char* name) {
    if (req == NULL || name == NULL) {
        return NULL;
    }
    for (size_t i = 0; i < req->header_count; ++i) {
        if (strcasecmp(req->headers[i].name, name) == 0) {
            return req->headers[i].value;
        }
    }
    return NULL;
}
