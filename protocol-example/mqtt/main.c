#include "broker/broker.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char** argv) {
    uint16_t port = 1884;
    if (argc >= 2) {
        const int p = atoi(argv[1]);
        if (p > 0 && p <= 65535) {
            port = (uint16_t)p;
        }
    }

    mqtt_broker_t* broker = mqtt_broker_create(port);
    if (!broker) {
        fprintf(stderr, "failed to create broker\n");
        return 1;
    }
    if (!mqtt_broker_start(broker)) {
        fprintf(stderr, "failed to start broker\n");
        mqtt_broker_destroy(broker);
        return 1;
    }

    mqtt_broker_run(broker);
    mqtt_broker_destroy(broker);
    return 0;
}
