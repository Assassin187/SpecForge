#include "mqtt_packet.h"

#include <stdlib.h>

void mqtt_packet_free(mqtt_packet_t* p) {
    if (!p) {
        return;
    }

    switch (p->type) {
        case MQTT_PKT_CONNECT:
            free(p->v.connect.client_id);
            p->v.connect.client_id = NULL;
            break;
        case MQTT_PKT_PUBLISH:
            free(p->v.publish.topic_name);
            p->v.publish.topic_name = NULL;
            free(p->v.publish.payload);
            p->v.publish.payload = NULL;
            p->v.publish.payload_len = 0;
            break;
        case MQTT_PKT_SUBSCRIBE:
            if (p->v.subscribe.topics) {
                for (size_t i = 0; i < p->v.subscribe.topic_count; ++i) {
                    free(p->v.subscribe.topics[i].filter);
                    p->v.subscribe.topics[i].filter = NULL;
                }
                free(p->v.subscribe.topics);
                p->v.subscribe.topics = NULL;
            }
            p->v.subscribe.topic_count = 0;
            break;
        default:
            break;
    }

    p->type = MQTT_PKT_RESERVED;
}
