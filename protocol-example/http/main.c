#include "server/http_server.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static uint16_t parse_port(const char* s) {
    char* end = NULL;
    const long v = strtol(s, &end, 10);
    if (end == s || *end != '\0' || v <= 0 || v > 65535) {
        return 0;
    }
    return (uint16_t)v;
}

int main(int argc, char** argv) {
    uint16_t port = 8080;
    const char* root = ".";

    if (argc >= 2) {
        const uint16_t p = parse_port(argv[1]);
        if (p == 0) {
            fprintf(stderr, "invalid port: %s\n", argv[1]);
            return 1;
        }
        port = p;
    }
    if (argc >= 3) {
        root = argv[2];
    }

    http_server_t* server = http_server_create(port, root);
    if (server == NULL) {
        fprintf(stderr, "failed to create http server (root=%s)\n", root);
        return 1;
    }

    if (http_server_start(server) < 0) {
        fprintf(stderr, "failed to start http server on port %u\n", (unsigned)port);
        http_server_destroy(server);
        return 1;
    }

    printf("HTTP/1.1 server running on http://127.0.0.1:%u  root=%s\n",
           (unsigned)port, root);
    const int rc = http_server_run(server);
    http_server_destroy(server);

    if (rc < 0) {
        fprintf(stderr, "http server exited with error\n");
        return 1;
    }

    return 0;
}
