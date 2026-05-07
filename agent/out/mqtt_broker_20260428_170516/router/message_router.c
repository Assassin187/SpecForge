#include "router/message_router.h"
#include "protocol/mqtt_encoder.h"
#include <stdlib.h>

struct mqtt_message_router {
    mqtt_session_manager_t* sessions;
    mqtt_topic_tree_t* topics;
};

mqtt_message_router_t* mqtt_message_router_create(mqtt_session_manager_t* sessions)
{
    if (sessions == NULL) {
        return NULL;
    }

    mqtt_message_router_t* r = malloc(sizeof(mqtt_message_router_t));
    if (r == NULL) {
        return NULL;
    }

    r->sessions = sessions;
    r->topics = mqtt_topic_tree_create();
    if (r->topics == NULL) {
        free(r);
        return NULL;
    }

    return r;
}

void mqtt_message_router_destroy(mqtt_message_router_t* r)
{
    if (r == NULL) {
        return;
    }

    mqtt_topic_tree_destroy(r->topics);
    free(r);
}

void mqtt_message_router_subscribe(mqtt_message_router_t* r, int session_id, const char* filter)
{
    if (r == NULL) {
        return;
    }

    mqtt_topic_tree_subscribe(r->topics, session_id, filter);
}

void mqtt_message_router_unsubscribe(mqtt_message_router_t* r, int session_id, const char* filter)
{
    if (r == NULL) {
        return;
    }

    mqtt_topic_tree_unsubscribe(r->topics, session_id, filter);
}

void mqtt_message_router_remove_session(mqtt_message_router_t* r, int session_id)
{
    if (r == NULL) {
        return;
    }

    mqtt_topic_tree_remove_session(r->topics, session_id);
}

void mqtt_message_router_publish(mqtt_message_router_t* r, int from_session_id, const mqtt_publish_payload_t* publish)
{
    (void)from_session_id; /* unused */

    if (r == NULL || publish == NULL || publish->topic_name == NULL) {
        return;
    }

    int* session_ids = NULL;
    size_t count = 0;

    if (!mqtt_topic_tree_match_subscribers(r->topics, publish->topic_name, &session_ids, &count)) {
        return;
    }

    for (size_t i = 0; i < count; ++i) {
        mqtt_session_t* session = mqtt_session_manager_get(r->sessions, session_ids[i]);
        if (session == NULL) {
            continue;
        }

        mqtt_bytes_t encoded = mqtt_encode_publish_qos0(
            publish->topic_name,
            publish->payload,
            publish->payload_len,
            publish->retain
        );

        if (encoded.data != NULL) {
            mqtt_session_send(session, encoded.data, encoded.len);
            mqtt_bytes_free(&encoded);
        }
    }

    free(session_ids);
}
