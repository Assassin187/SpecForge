#include "broker/broker.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static uint16_t parse_port(int argc, char** argv) {
    if (argc < 2) {
        return 1884;
    }
    char* end = NULL;
    unsigned long raw = strtoul(argv[1], &end, 10);
    if (!argv[1][0] || (end && *end != '\0') || raw == 0 || raw > 65535UL) {
        return 1884;
    }
    return (uint16_t)raw;
}

int main(int argc, char** argv) {
    const uint16_t port = parse_port(argc, argv);
    mqtt_broker_t* app = mqtt_broker_create(port);
    if (!app) {
        fprintf(stderr, "failed to create application on port %u\n", (unsigned)port);
        return 1;
    }
    if (!mqtt_broker_start(app)) {
        fprintf(stderr, "failed to start application on port %u\n", (unsigned)port);
        mqtt_broker_destroy(app);
        return 1;
    }
    mqtt_broker_run(app);
    mqtt_broker_destroy(app);
    return 0;
}
