#include "message_router.h"

#include "../protocol/mqtt_encoder.h"

#include <stdlib.h>

struct mqtt_message_router {
    mqtt_session_manager_t* sessions; // non-owning
    mqtt_topic_tree_t* topics;
};

mqtt_message_router_t* mqtt_message_router_create(mqtt_session_manager_t* sessions) {
    if (!sessions) {
        return NULL;
    }
    mqtt_message_router_t* r = (mqtt_message_router_t*)calloc(1, sizeof(*r));
    if (!r) {
        return NULL;
    }
    r->sessions = sessions;
    r->topics = mqtt_topic_tree_create();
    if (!r->topics) {
        free(r);
        return NULL;
    }
    return r;
}

void mqtt_message_router_destroy(mqtt_message_router_t* r) {
    if (!r) {
        return;
    }
    mqtt_topic_tree_destroy(r->topics);
    free(r);
}

void mqtt_message_router_subscribe(mqtt_message_router_t* r, int session_id, const char* filter) {
    if (!r) {
        return;
    }
    mqtt_topic_tree_subscribe(r->topics, session_id, filter);
}

void mqtt_message_router_unsubscribe(mqtt_message_router_t* r, int session_id, const char* filter) {
    if (!r) {
        return;
    }
    mqtt_topic_tree_unsubscribe(r->topics, session_id, filter);
}

void mqtt_message_router_remove_session(mqtt_message_router_t* r, int session_id) {
    if (!r) {
        return;
    }
    mqtt_topic_tree_remove_session(r->topics, session_id);
}

void mqtt_message_router_publish(mqtt_message_router_t* r, int from_session_id, const mqtt_publish_payload_t* publish) {
    (void)from_session_id;
    if (!r || !publish || !publish->topic_name) {
        return;
    }

    int* subs = NULL;
    size_t sub_count = 0;
    if (!mqtt_topic_tree_match_subscribers(r->topics, publish->topic_name, &subs, &sub_count)) {
        return;
    }

    for (size_t i = 0; i < sub_count; ++i) {
        mqtt_session_t* sess = mqtt_session_manager_get(r->sessions, subs[i]);
        if (!sess) {
            continue;
        }
        mqtt_bytes_t out = mqtt_encode_publish_qos0(publish->topic_name, publish->payload, publish->payload_len, publish->retain);
        if (out.data && out.len) {
            mqtt_session_send(sess, out.data, out.len);
        }
        mqtt_bytes_free(&out);
    }

    free(subs);
}
