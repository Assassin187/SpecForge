#pragma once

#include "../protocol/coap_message.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct coap_request {
    const coap_message_t* message;
    coap_method_t method;
    char* path;
    char* query;
    uint16_t content_format;
    bool has_content_format;
    uint16_t accept;
    bool has_accept;
} coap_request_t;

typedef bool (*coap_resource_handler_fn)(void* user, const coap_request_t* req, coap_message_t* resp);

typedef struct coap_resource_router coap_resource_router_t;

coap_resource_router_t* coap_resource_router_create(void);
void coap_resource_router_destroy(coap_resource_router_t* router);

bool coap_resource_router_add(coap_resource_router_t* router,
                              const char* path,
                              bool prefix_match,
                              uint32_t methods_mask,
                              bool discoverable,
                              const char* attributes,
                              coap_resource_handler_fn handler,
                              void* user);

bool coap_resource_router_dispatch(coap_resource_router_t* router, const coap_request_t* req, coap_message_t* resp);
char* coap_resource_router_build_core_links(const coap_resource_router_t* router);

#ifdef __cplusplus
}
#endif
