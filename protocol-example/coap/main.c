#include "server/coap_server.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char** argv) {
    uint16_t port = 5683;
    if (argc >= 2) {
        const int p = atoi(argv[1]);
        if (p > 0 && p <= 65535) {
            port = (uint16_t)p;
        }
    }

    coap_server_t* server = coap_server_create(port);
    if (!server) {
        fprintf(stderr, "failed to create CoAP server\n");
        return 1;
    }
    if (!coap_server_start(server)) {
        fprintf(stderr, "failed to start CoAP server\n");
        coap_server_destroy(server);
        return 1;
    }

    coap_server_run(server);
    coap_server_destroy(server);
    return 0;
}
