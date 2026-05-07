#include "server/smtp_server.h"

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
    uint16_t port = 2525;
    const char* mail_root = "/mail-test";

    if (argc >= 2) {
        const uint16_t p = parse_port(argv[1]);
        if (p == 0) {
            fprintf(stderr, "invalid port: %s\n", argv[1]);
            return 1;
        }
        port = p;
    }
    if (argc >= 3) {
        mail_root = argv[2];
    }

    smtp_server_t* server = smtp_server_create(port, mail_root);
    if (server == NULL) {
        fprintf(stderr, "failed to create smtp server (mail_root=%s)\n", mail_root);
        return 1;
    }

    if (smtp_server_start(server) < 0) {
        fprintf(stderr, "failed to start smtp server on port %u\n", (unsigned)port);
        smtp_server_destroy(server);
        return 1;
    }

    printf("smtp server running on port %u, mail_root=%s\n", (unsigned)port, mail_root);
    printf("default credential: %s / smtppass\n", "smtpuser");

    const int rc = smtp_server_run(server);
    smtp_server_destroy(server);

    if (rc < 0) {
        fprintf(stderr, "smtp server exited with error\n");
        return 1;
    }

    return 0;
}
