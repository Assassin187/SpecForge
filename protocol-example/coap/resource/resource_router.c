#include "resource_router.h"

#include <stdlib.h>
#include <string.h>

typedef struct coap_resource_entry {
    char* path;
    bool prefix_match;
    uint32_t methods_mask;
    bool discoverable;
    char* attributes;
    coap_resource_handler_fn handler;
    void* user;
} coap_resource_entry_t;

struct coap_resource_router {
    coap_resource_entry_t* entries;
    size_t count;
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

static bool path_matches(const coap_resource_entry_t* e, const char* path) {
    if (!e || !path) {
        return false;
    }
    if (!e->prefix_match) {
        return strcmp(e->path, path) == 0;
    }
    const size_t n = strlen(e->path);
    if (strncmp(e->path, path, n) != 0) {
        return false;
    }
    return path[n] == '\0' || path[n] == '/';
}

coap_resource_router_t* coap_resource_router_create(void) {
    return (coap_resource_router_t*)calloc(1, sizeof(coap_resource_router_t));
}

void coap_resource_router_destroy(coap_resource_router_t* router) {
    if (!router) {
        return;
    }
    for (size_t i = 0; i < router->count; ++i) {
        free(router->entries[i].path);
        free(router->entries[i].attributes);
    }
    free(router->entries);
    free(router);
}

bool coap_resource_router_add(coap_resource_router_t* router,
                              const char* path,
                              bool prefix_match,
                              uint32_t methods_mask,
                              bool discoverable,
                              const char* attributes,
                              coap_resource_handler_fn handler,
                              void* user) {
    if (!router || !path || !handler) {
        return false;
    }

    coap_resource_entry_t* entries =
        (coap_resource_entry_t*)realloc(router->entries, (router->count + 1) * sizeof(*entries));
    if (!entries) {
        return false;
    }
    router->entries = entries;

    coap_resource_entry_t* e = &router->entries[router->count];
    memset(e, 0, sizeof(*e));
    e->path = dup_string(path);
    e->attributes = dup_string(attributes);
    if (!e->path) {
        free(e->attributes);
        return false;
    }
    e->prefix_match = prefix_match;
    e->methods_mask = methods_mask;
    e->discoverable = discoverable;
    e->handler = handler;
    e->user = user;
    router->count += 1;
    return true;
}

bool coap_resource_router_dispatch(coap_resource_router_t* router, const coap_request_t* req, coap_message_t* resp) {
    if (!router || !req || !resp || !req->path) {
        return false;
    }
    const uint32_t method_bit = 1u << (uint32_t)req->method;

    for (size_t i = 0; i < router->count; ++i) {
        coap_resource_entry_t* e = &router->entries[i];
        if (!path_matches(e, req->path)) {
            continue;
        }
        if ((e->methods_mask & method_bit) == 0) {
            resp->code = coap_make_code(4, 5);
            return true;
        }
        return e->handler(e->user, req, resp);
    }
    return false;
}

char* coap_resource_router_build_core_links(const coap_resource_router_t* router) {
    if (!router) {
        return NULL;
    }

    size_t total = 0;
    size_t count = 0;
    for (size_t i = 0; i < router->count; ++i) {
        const coap_resource_entry_t* e = &router->entries[i];
        if (!e->discoverable) {
            continue;
        }
        total += strlen(e->path) + 2;
        if (e->attributes) {
            total += 1 + strlen(e->attributes);
        }
        if (count > 0) {
            total += 1;
        }
        count += 1;
    }

    char* out = (char*)malloc(total + 1);
    if (!out) {
        return NULL;
    }

    size_t off = 0;
    size_t seen = 0;
    for (size_t i = 0; i < router->count; ++i) {
        const coap_resource_entry_t* e = &router->entries[i];
        if (!e->discoverable) {
            continue;
        }
        if (seen > 0) {
            out[off++] = ',';
        }
        out[off++] = '<';
        memcpy(out + off, e->path, strlen(e->path));
        off += strlen(e->path);
        out[off++] = '>';
        if (e->attributes && e->attributes[0] != '\0') {
            out[off++] = ';';
            memcpy(out + off, e->attributes, strlen(e->attributes));
            off += strlen(e->attributes);
        }
        seen += 1;
    }
    out[off] = '\0';
    return out;
}
