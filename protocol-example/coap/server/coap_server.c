#include "coap_server.h"

#include "../network/udp_server.h"
#include "../protocol/coap_codec.h"
#include "../resource/resource_router.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct kv_entry {
    char* key;
    uint8_t* value;
    size_t value_len;
    uint16_t content_format;
    bool has_content_format;
} kv_entry_t;

struct coap_server {
    uint16_t port;
    coap_udp_server_t* udp;
    coap_resource_router_t* router;
    kv_entry_t* kv_entries;
    size_t kv_count;
};

static char* dup_string(const char* s) {
    if (!s) {
        return NULL;
    }
    const size_t n = strlen(s);
    char* out = (char*)malloc(n + 1);
    if (!out) {
        return NULL;
    }
    memcpy(out, s, n + 1);
    return out;
}

static bool response_set_text(coap_message_t* resp, uint8_t code, uint16_t format, const char* text) {
    resp->code = code;
    if (!coap_message_add_option_uint(resp, COAP_OPT_CONTENT_FORMAT, format)) {
        return false;
    }
    return coap_message_set_payload_string(resp, text);
}

static bool response_set_bytes(coap_message_t* resp,
                               uint8_t code,
                               uint16_t format,
                               const uint8_t* data,
                               size_t len) {
    resp->code = code;
    if (!coap_message_add_option_uint(resp, COAP_OPT_CONTENT_FORMAT, format)) {
        return false;
    }
    return coap_message_set_payload(resp, data, len);
}

static kv_entry_t* find_kv(coap_server_t* s, const char* key) {
    if (!s || !key) {
        return NULL;
    }
    for (size_t i = 0; i < s->kv_count; ++i) {
        if (strcmp(s->kv_entries[i].key, key) == 0) {
            return &s->kv_entries[i];
        }
    }
    return NULL;
}

static bool kv_put(coap_server_t* s,
                   const char* key,
                   const uint8_t* value,
                   size_t len,
                   bool has_content_format,
                   uint16_t content_format,
                   bool* created) {
    if (created) {
        *created = false;
    }
    if (!s || !key) {
        return false;
    }

    kv_entry_t* entry = find_kv(s, key);
    if (!entry) {
        kv_entry_t* entries =
            (kv_entry_t*)realloc(s->kv_entries, (s->kv_count + 1) * sizeof(*entries));
        if (!entries) {
            return false;
        }
        s->kv_entries = entries;
        entry = &s->kv_entries[s->kv_count];
        memset(entry, 0, sizeof(*entry));
        entry->key = dup_string(key);
        if (!entry->key) {
            return false;
        }
        s->kv_count += 1;
        if (created) {
            *created = true;
        }
    }

    uint8_t* copy = NULL;
    if (len > 0) {
        copy = (uint8_t*)malloc(len);
        if (!copy) {
            return false;
        }
        memcpy(copy, value, len);
    }
    free(entry->value);
    entry->value = copy;
    entry->value_len = len;
    entry->has_content_format = has_content_format;
    entry->content_format = content_format;
    return true;
}

static bool kv_delete(coap_server_t* s, const char* key) {
    if (!s || !key) {
        return false;
    }
    for (size_t i = 0; i < s->kv_count; ++i) {
        if (strcmp(s->kv_entries[i].key, key) != 0) {
            continue;
        }
        free(s->kv_entries[i].key);
        free(s->kv_entries[i].value);
        if (i + 1 < s->kv_count) {
            memmove(&s->kv_entries[i], &s->kv_entries[i + 1], (s->kv_count - i - 1) * sizeof(kv_entry_t));
        }
        s->kv_count -= 1;
        if (s->kv_count == 0) {
            free(s->kv_entries);
            s->kv_entries = NULL;
        }
        return true;
    }
    return false;
}

static bool handle_discovery(void* user, const coap_request_t* req, coap_message_t* resp) {
    coap_server_t* s = (coap_server_t*)user;
    if (!s || !req || !resp) {
        return false;
    }
    if (req->accept && req->accept != COAP_FORMAT_LINK_FORMAT) {
        resp->code = coap_make_code(4, 6);
        return true;
    }

    char* links = coap_resource_router_build_core_links(s->router);
    if (!links) {
        resp->code = coap_make_code(5, 0);
        return true;
    }
    const bool ok = response_set_text(resp, coap_make_code(2, 5), COAP_FORMAT_LINK_FORMAT, links);
    free(links);
    return ok;
}

static bool handle_hello(void* user, const coap_request_t* req, coap_message_t* resp) {
    (void)user;
    const char* text = "hello from CoAP server";
    if (req->query && req->query[0] != '\0') {
        text = "hello from CoAP server (query received)";
    }
    return response_set_text(resp, coap_make_code(2, 5), COAP_FORMAT_TEXT_PLAIN, text);
}

static bool handle_echo(void* user, const coap_request_t* req, coap_message_t* resp) {
    (void)user;
    uint16_t format = COAP_FORMAT_TEXT_PLAIN;
    if (req->has_content_format) {
        format = req->content_format;
    }
    return response_set_bytes(resp,
                              coap_make_code(2, 4),
                              format,
                              req->message->payload,
                              req->message->payload_len);
}

static bool handle_kv(void* user, const coap_request_t* req, coap_message_t* resp) {
    coap_server_t* s = (coap_server_t*)user;
    if (!s || !req || !resp) {
        return false;
    }

    const char* suffix = req->path + 3;
    while (*suffix == '/') {
        ++suffix;
    }

    if (req->method == COAP_METHOD_GET && *suffix == '\0') {
        size_t total = 2;
        for (size_t i = 0; i < s->kv_count; ++i) {
            total += strlen(s->kv_entries[i].key) + 4;
        }
        char* json = (char*)malloc(total + 1);
        if (!json) {
            resp->code = coap_make_code(5, 0);
            return true;
        }
        size_t off = 0;
        json[off++] = '[';
        for (size_t i = 0; i < s->kv_count; ++i) {
            if (i > 0) {
                json[off++] = ',';
            }
            json[off++] = '"';
            memcpy(json + off, s->kv_entries[i].key, strlen(s->kv_entries[i].key));
            off += strlen(s->kv_entries[i].key);
            json[off++] = '"';
        }
        json[off++] = ']';
        json[off] = '\0';
        const bool ok = response_set_text(resp, coap_make_code(2, 5), COAP_FORMAT_APPLICATION_JSON, json);
        free(json);
        return ok;
    }

    if (*suffix == '\0') {
        resp->code = coap_make_code(4, 0);
        return true;
    }

    kv_entry_t* entry = find_kv(s, suffix);
    switch (req->method) {
        case COAP_METHOD_GET:
            if (!entry) {
                resp->code = coap_make_code(4, 4);
                return true;
            }
            return response_set_bytes(resp,
                                      coap_make_code(2, 5),
                                      entry->has_content_format ? entry->content_format : COAP_FORMAT_OCTET_STREAM,
                                      entry->value,
                                      entry->value_len);
        case COAP_METHOD_PUT:
        case COAP_METHOD_POST: {
            bool created = false;
            if (!kv_put(s,
                        suffix,
                        req->message->payload,
                        req->message->payload_len,
                        req->has_content_format,
                        req->content_format,
                        &created)) {
                resp->code = coap_make_code(5, 0);
                return true;
            }
            resp->code = created ? coap_make_code(2, 1) : coap_make_code(2, 4);
            return true;
        }
        case COAP_METHOD_DELETE:
            if (!entry) {
                resp->code = coap_make_code(4, 4);
                return true;
            }
            (void)kv_delete(s, suffix);
            resp->code = coap_make_code(2, 2);
            return true;
        default:
            resp->code = coap_make_code(4, 5);
            return true;
    }
}

static void setup_response_envelope(const coap_message_t* req, coap_message_t* resp) {
    coap_message_init(resp);
    resp->version = 1;
    resp->type = (req->type == COAP_TYPE_CON) ? COAP_TYPE_ACK : COAP_TYPE_NON;
    resp->message_id = req->message_id;
    (void)coap_message_set_token(resp, req->token, req->token_len);
}

static bool build_request_view(const coap_message_t* req, coap_request_t* out) {
    if (!req || !out || !coap_code_is_method(req->code)) {
        return false;
    }
    memset(out, 0, sizeof(*out));
    out->message = req;
    out->method = (coap_method_t)req->code;
    out->path = coap_message_join_options(req, COAP_OPT_URI_PATH, '/');
    out->query = coap_message_join_options(req, COAP_OPT_URI_QUERY, '&');
    if (!out->path || !out->query) {
        free(out->path);
        free(out->query);
        return false;
    }
    if (out->path[0] == '\0') {
        free(out->path);
        out->path = dup_string("/");
        if (!out->path) {
            free(out->query);
            out->query = NULL;
            return false;
        }
    } else if (out->path[0] != '/') {
        char* p = (char*)malloc(strlen(out->path) + 2);
        if (!p) {
            free(out->path);
            free(out->query);
            out->path = NULL;
            out->query = NULL;
            return false;
        }
        p[0] = '/';
        memcpy(p + 1, out->path, strlen(out->path) + 1);
        free(out->path);
        out->path = p;
    }

    uint32_t val = 0;
    if (coap_message_get_uint_option(req, COAP_OPT_CONTENT_FORMAT, &val)) {
        out->has_content_format = true;
        out->content_format = (uint16_t)val;
    }
    if (coap_message_get_uint_option(req, COAP_OPT_ACCEPT, &val)) {
        out->has_accept = true;
        out->accept = (uint16_t)val;
    }
    return true;
}

static void free_request_view(coap_request_t* req) {
    if (!req) {
        return;
    }
    free(req->path);
    free(req->query);
    req->path = NULL;
    req->query = NULL;
}

static void send_response(coap_server_t* s, const coap_endpoint_t* peer, const coap_message_t* resp) {
    coap_bytes_t bytes = {0};
    if (!coap_encode_message(resp, &bytes)) {
        return;
    }
    (void)coap_udp_server_sendto(s->udp, peer, bytes.data, bytes.len);
    coap_bytes_free(&bytes);
}

static void send_reset(coap_server_t* s, const coap_endpoint_t* peer, uint16_t message_id) {
    coap_message_t rst;
    coap_message_init(&rst);
    rst.type = COAP_TYPE_RST;
    rst.code = 0;
    rst.message_id = message_id;
    send_response(s, peer, &rst);
    coap_message_free(&rst);
}

static void on_datagram(void* user, const coap_endpoint_t* peer, const uint8_t* data, size_t len) {
    coap_server_t* s = (coap_server_t*)user;
    if (!s || !peer || !data || len == 0) {
        return;
    }

    char peer_buf[128];
    coap_message_t req;
    coap_decode_status_t st = coap_decode_message(data, len, &req);
    if (st != COAP_DECODE_OK) {
        if (len >= 4) {
            const uint16_t message_id = (uint16_t)(((uint16_t)data[2] << 8) | data[3]);
            const uint8_t type = (uint8_t)((data[0] >> 4) & 0x3u);
            if (type == COAP_TYPE_CON) {
                send_reset(s, peer, message_id);
            }
        }
        return;
    }

    printf("recv from %s type=%u code=%u.%02u mid=%u\n",
           coap_endpoint_to_string(peer, peer_buf, sizeof(peer_buf)),
           (unsigned)req.type,
           (unsigned)coap_code_class(req.code),
           (unsigned)coap_code_detail(req.code),
           (unsigned)req.message_id);

    if (!coap_code_is_method(req.code)) {
        coap_message_free(&req);
        return;
    }

    coap_request_t view;
    coap_message_t resp;
    setup_response_envelope(&req, &resp);

    if (!build_request_view(&req, &view)) {
        resp.code = coap_make_code(5, 0);
        send_response(s, peer, &resp);
        coap_message_free(&resp);
        coap_message_free(&req);
        return;
    }

    if (!coap_resource_router_dispatch(s->router, &view, &resp)) {
        resp.code = coap_make_code(4, 4);
    }

    send_response(s, peer, &resp);
    coap_message_free(&resp);
    free_request_view(&view);
    coap_message_free(&req);
}

coap_server_t* coap_server_create(uint16_t port) {
    coap_server_t* s = (coap_server_t*)calloc(1, sizeof(*s));
    if (!s) {
        return NULL;
    }
    s->port = port;
    s->router = coap_resource_router_create();
    if (!s->router) {
        free(s);
        return NULL;
    }

    coap_udp_callbacks_t cb;
    memset(&cb, 0, sizeof(cb));
    cb.on_datagram = on_datagram;

    s->udp = coap_udp_server_create(port, cb, s);
    if (!s->udp) {
        coap_resource_router_destroy(s->router);
        free(s);
        return NULL;
    }

    const uint32_t get_mask = 1u << COAP_METHOD_GET;
    const uint32_t read_write_mask =
        (1u << COAP_METHOD_GET) | (1u << COAP_METHOD_POST) | (1u << COAP_METHOD_PUT) |
        (1u << COAP_METHOD_DELETE);
    const uint32_t echo_mask = (1u << COAP_METHOD_POST) | (1u << COAP_METHOD_PUT);

    if (!coap_resource_router_add(s->router,
                                  "/.well-known/core",
                                  false,
                                  get_mask,
                                  false,
                                  NULL,
                                  handle_discovery,
                                  s) ||
        !coap_resource_router_add(s->router,
                                  "/hello",
                                  false,
                                  get_mask,
                                  true,
                                  "ct=0;title=\"hello\"",
                                  handle_hello,
                                  s) ||
        !coap_resource_router_add(s->router,
                                  "/echo",
                                  false,
                                  echo_mask,
                                  true,
                                  "title=\"echo\"",
                                  handle_echo,
                                  s) ||
        !coap_resource_router_add(s->router,
                                  "/kv",
                                  true,
                                  read_write_mask,
                                  true,
                                  "title=\"key-value store\"",
                                  handle_kv,
                                  s)) {
        coap_server_destroy(s);
        return NULL;
    }

    return s;
}

void coap_server_destroy(coap_server_t* s) {
    if (!s) {
        return;
    }
    coap_server_stop(s);
    coap_udp_server_destroy(s->udp);
    coap_resource_router_destroy(s->router);
    for (size_t i = 0; i < s->kv_count; ++i) {
        free(s->kv_entries[i].key);
        free(s->kv_entries[i].value);
    }
    free(s->kv_entries);
    free(s);
}

bool coap_server_start(coap_server_t* s) {
    if (!s || !s->udp) {
        return false;
    }
    return coap_udp_server_start(s->udp);
}

void coap_server_run(coap_server_t* s) {
    if (!s || !s->udp) {
        return;
    }
    coap_udp_server_run(s->udp);
}

void coap_server_stop(coap_server_t* s) {
    if (!s || !s->udp) {
        return;
    }
    coap_udp_server_stop(s->udp);
}
