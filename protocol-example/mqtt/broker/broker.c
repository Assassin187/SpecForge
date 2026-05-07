#include "broker.h"

#include "../protocol/mqtt_decoder.h"
#include "../protocol/mqtt_encoder.h"
#include "session_manager.h"

#include "../router/message_router.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct mqtt_broker {
    uint16_t port;
    mqtt_tcp_server_t* server;

    mqtt_session_manager_t* sessions;
    mqtt_message_router_t* router;
};

typedef struct packet_ctx {
    mqtt_broker_t* broker;
    mqtt_session_t* sess;
} packet_ctx_t;

static void handle_packet(mqtt_broker_t* b, mqtt_session_t* sess, const mqtt_packet_t* pkt) {
    if (!b || !sess || !pkt) {
        return;
    }

    mqtt_connection_t* conn = mqtt_session_connection(sess);
    if (!conn) {
        return;
    }

    switch (pkt->type) {
        case MQTT_PKT_CONNECT: {
            const mqtt_connect_payload_t* cp = &pkt->v.connect;
            mqtt_session_mark_connected(sess, cp->client_id, cp->clean_session, cp->keep_alive);

            mqtt_bytes_t out = mqtt_encode_connack(false, 0x00);
            if (out.data && out.len) {
                mqtt_session_send(sess, out.data, out.len);
            }
            mqtt_bytes_free(&out);

            printf("CONNECT client_id=%s sid=%d\n", cp->client_id ? cp->client_id : "", mqtt_session_id(sess));
            return;
        }
        case MQTT_PKT_SUBSCRIBE: {
            if (!mqtt_session_connected(sess)) {
                mqtt_connection_close(conn);
                return;
            }

            const mqtt_subscribe_payload_t* sp = &pkt->v.subscribe;
            uint8_t* rc = NULL;
            size_t rc_cnt = 0;
            if (sp->topic_count > 0) {
                rc = (uint8_t*)calloc(sp->topic_count, 1);
                if (!rc) {
                    mqtt_connection_close(conn);
                    return;
                }
            }

            for (size_t i = 0; i < sp->topic_count; ++i) {
                const mqtt_subscribe_topic_t* t = &sp->topics[i];
                mqtt_message_router_subscribe(b->router, mqtt_session_id(sess), t->filter);
                rc[rc_cnt++] = 0x00; // granted QoS0
                printf("SUBSCRIBE sid=%d filter=%s\n", mqtt_session_id(sess), t->filter ? t->filter : "");
            }

            mqtt_bytes_t out = mqtt_encode_suback(sp->packet_id, rc, rc_cnt);
            if (out.data && out.len) {
                mqtt_session_send(sess, out.data, out.len);
            }
            mqtt_bytes_free(&out);
            free(rc);
            return;
        }
        case MQTT_PKT_PUBLISH: {
            if (!mqtt_session_connected(sess)) {
                mqtt_connection_close(conn);
                return;
            }
            const mqtt_publish_payload_t* pub = &pkt->v.publish;
            if (pub->qos != 0) {
                return; // minimal broker supports only QoS0
            }
            mqtt_message_router_publish(b->router, mqtt_session_id(sess), pub);
            printf("PUBLISH sid=%d topic=%s bytes=%zu\n",
                   mqtt_session_id(sess),
                   pub->topic_name ? pub->topic_name : "",
                   pub->payload_len);
            return;
        }
        case MQTT_PKT_PINGREQ: {
            if (!mqtt_session_connected(sess)) {
                mqtt_connection_close(conn);
                return;
            }
            mqtt_bytes_t out = mqtt_encode_pingresp();
            if (out.data && out.len) {
                mqtt_session_send(sess, out.data, out.len);
            }
            mqtt_bytes_free(&out);
            return;
        }
        case MQTT_PKT_DISCONNECT: {
            mqtt_connection_close(conn);
            return;
        }
        default:
            return;
    }
}

static void on_packet(void* user, const mqtt_packet_t* pkt) {
    packet_ctx_t* ctx = (packet_ctx_t*)user;
    if (!ctx || !ctx->broker || !ctx->sess) {
        return;
    }
    handle_packet(ctx->broker, ctx->sess, pkt);
}

static void on_accept_cb(void* user, mqtt_connection_t* c) {
    mqtt_broker_t* b = (mqtt_broker_t*)user;
    if (!b || !c) {
        return;
    }
    mqtt_session_t* sess = mqtt_session_create(c);
    if (!sess) {
        mqtt_connection_close(c);
        return;
    }
    mqtt_session_manager_add(b->sessions, sess);
    printf("accept %s fd=%d\n", mqtt_connection_peer(c), mqtt_connection_fd(c));
}

static void on_close_cb(void* user, mqtt_connection_t* c) {
    mqtt_broker_t* b = (mqtt_broker_t*)user;
    if (!b || !c) {
        return;
    }
    const int sid = mqtt_connection_fd(c);
    mqtt_message_router_remove_session(b->router, sid);
    mqtt_session_manager_remove(b->sessions, sid);
    printf("close fd=%d\n", sid);
}

static void on_data_cb(void* user, mqtt_connection_t* c) {
    mqtt_broker_t* b = (mqtt_broker_t*)user;
    if (!b || !c) {
        return;
    }

    const int sid = mqtt_connection_fd(c);
    mqtt_session_t* sess = mqtt_session_manager_get(b->sessions, sid);
    if (!sess) {
        return;
    }

    const uint8_t* data = mqtt_connection_in_data(c);
    const size_t len = mqtt_connection_in_len(c);
    if (!data || len == 0) {
        return;
    }

    size_t consumed = 0;
    packet_ctx_t ctx;
    ctx.broker = b;
    ctx.sess = sess;

    (void)mqtt_decoder_feed(data, len, &consumed, on_packet, &ctx);
    if (consumed > 0) {
        mqtt_connection_in_consume(c, consumed);
    }
}

mqtt_broker_t* mqtt_broker_create(uint16_t port) {
    mqtt_broker_t* b = (mqtt_broker_t*)calloc(1, sizeof(*b));
    if (!b) {
        return NULL;
    }

    b->port = port;
    b->sessions = mqtt_session_manager_create();
    if (!b->sessions) {
        free(b);
        return NULL;
    }
    b->router = mqtt_message_router_create(b->sessions);
    if (!b->router) {
        mqtt_session_manager_destroy(b->sessions);
        free(b);
        return NULL;
    }

    mqtt_tcp_callbacks_t cb;
    memset(&cb, 0, sizeof(cb));
    cb.on_accept = on_accept_cb;
    cb.on_data = on_data_cb;
    cb.on_close = on_close_cb;

    b->server = mqtt_tcp_server_create(b->port, cb, b);
    if (!b->server) {
        mqtt_message_router_destroy(b->router);
        mqtt_session_manager_destroy(b->sessions);
        free(b);
        return NULL;
    }

    return b;
}

void mqtt_broker_destroy(mqtt_broker_t* b) {
    if (!b) {
        return;
    }
    mqtt_broker_stop(b);
    mqtt_tcp_server_destroy(b->server);
    mqtt_message_router_destroy(b->router);
    mqtt_session_manager_destroy(b->sessions);
    free(b);
}

bool mqtt_broker_start(mqtt_broker_t* b) {
    if (!b || !b->server) {
        return false;
    }
    return mqtt_tcp_server_start(b->server);
}

void mqtt_broker_run(mqtt_broker_t* b) {
    if (!b || !b->server) {
        return;
    }
    mqtt_tcp_server_run(b->server);
}

void mqtt_broker_stop(mqtt_broker_t* b) {
    if (!b || !b->server) {
        return;
    }
    mqtt_tcp_server_stop(b->server);
}
