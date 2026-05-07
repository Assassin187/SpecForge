#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct coap_server coap_server_t;

coap_server_t* coap_server_create(uint16_t port);
void coap_server_destroy(coap_server_t* server);

bool coap_server_start(coap_server_t* server);
void coap_server_run(coap_server_t* server);
void coap_server_stop(coap_server_t* server);

#ifdef __cplusplus
}
#endif
