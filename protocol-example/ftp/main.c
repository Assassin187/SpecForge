#include "server/ftp_server.h"

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
    uint16_t port = 2121;
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

    ftp_server_t* server = ftp_server_create(port, root);
    if (server == NULL) {
        fprintf(stderr, "failed to create ftp server (root=%s)\n", root);
        return 1;
    }

    if (ftp_server_start(server) < 0) {
        fprintf(stderr, "failed to start ftp server on port %u\n", (unsigned)port);
        ftp_server_destroy(server);
        return 1;
    }

    printf("ftp server running on port %u, root=%s\n", (unsigned)port, root);
    const int rc = ftp_server_run(server);
    ftp_server_destroy(server);

    if (rc < 0) {
        fprintf(stderr, "ftp server exited with error\n");
        return 1;
    }

    return 0;
}
