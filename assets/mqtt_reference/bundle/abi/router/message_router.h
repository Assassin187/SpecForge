#pragma once

#include "../broker/session_manager.h"
#include "../protocol/mqtt_packet.h"
#include "../topic/topic_tree.h"

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct mqtt_message_router mqtt_message_router_t;

mqtt_message_router_t* mqtt_message_router_create(mqtt_session_manager_t* sessions);
void mqtt_message_router_destroy(mqtt_message_router_t* r);

void mqtt_message_router_subscribe(mqtt_message_router_t* r, int session_id, const char* filter);
void mqtt_message_router_unsubscribe(mqtt_message_router_t* r, int session_id, const char* filter);
void mqtt_message_router_remove_session(mqtt_message_router_t* r, int session_id);

void mqtt_message_router_publish(mqtt_message_router_t* r, int from_session_id, const mqtt_publish_payload_t* publish);

#ifdef __cplusplus
}
#endif
