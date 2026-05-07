#include "broker/broker.h"

#include "protocol/mqtt_decoder.h"
#include "protocol/mqtt_encoder.h"
#include "broker/session_manager.h"
#include "router/message_router.h"
#include "network/tcp_server.h"

#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include <stdint.h>

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

static void handle_packet(mqtt_broker_t* b, mqtt_session_t* sess, const mqtt_packet_t* pkt);
static void on_packet(void* user, const mqtt_packet_t* pkt);
static void on_accept_cb(void* user, mqtt_connection_t* c);
static void on_close_cb(void* user, mqtt_connection_t* c);
static void on_data_cb(void* user, mqtt_connection_t* c);

mqtt_broker_t* mqtt_broker_create(uint16_t port)
{
    mqtt_broker_t* b = calloc(1, sizeof(mqtt_broker_t));
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

    mqtt_tcp_callbacks_t callbacks = {
        .on_accept = on_accept_cb,
        .on_data = on_data_cb,
        .on_close = on_close_cb
    };

    b->server = mqtt_tcp_server_create(port, callbacks, b);
    if (!b->server) {
        mqtt_message_router_destroy(b->router);
        mqtt_session_manager_destroy(b->sessions);
        free(b);
        return NULL;
    }

    return b;
}

void mqtt_broker_destroy(mqtt_broker_t* b)
{
    if (!b) {
        return;
    }

    mqtt_broker_stop(b);
    mqtt_tcp_server_destroy(b->server);
    mqtt_message_router_destroy(b->router);
    mqtt_session_manager_destroy(b->sessions);
    free(b);
}

bool mqtt_broker_start(mqtt_broker_t* b)
{
    if (!b || !b->server) {
        return false;
    }
    return mqtt_tcp_server_start(b->server);
}

void mqtt_broker_run(mqtt_broker_t* b)
{
    if (!b || !b->server) {
        return;
    }
    mqtt_tcp_server_run(b->server);
}

void mqtt_broker_stop(mqtt_broker_t* b)
{
    if (!b || !b->server) {
        return;
    }
    mqtt_tcp_server_stop(b->server);
}

static void handle_packet(mqtt_broker_t* b, mqtt_session_t* sess, const mqtt_packet_t* pkt)
{
    if (!b || !sess || !pkt) {
        return;
    }

    switch (pkt->type) {
        case MQTT_PKT_CONNECT: {
            if (mqtt_session_connected(sess)) {
                mqtt_connection_close(mqtt_session_connection(sess));
                return;
            }

            const char* client_id = pkt->v.connect.client_id;
            bool clean_session = pkt->v.connect.clean_session;
            uint16_t keep_alive = pkt->v.connect.keep_alive;

            mqtt_session_mark_connected(sess, client_id, clean_session, keep_alive);

            mqtt_bytes_t ack = mqtt_encode_connack(false, 0);
            mqtt_session_send(sess, ack.data, ack.len);
            mqtt_bytes_free(&ack);
            break;
        }

        case MQTT_PKT_SUBSCRIBE: {
            if (!mqtt_session_connected(sess)) {
                mqtt_connection_close(mqtt_session_connection(sess));
                return;
            }

            size_t topic_count = pkt->v.subscribe.topic_count;
            uint16_t packet_id = pkt->v.subscribe.packet_id;

            for (size_t i = 0; i < topic_count; ++i) {
                const char* filter = pkt->v.subscribe.topics[i].filter;
                mqtt_message_router_subscribe(b->router, mqtt_session_id(sess), filter);
            }

            uint8_t* return_codes = malloc(topic_count);
            if (!return_codes) {
                mqtt_connection_close(mqtt_session_connection(sess));
                return;
            }

            memset(return_codes, 0x00, topic_count); // QoS 0 granted

            mqtt_bytes_t suback = mqtt_encode_suback(packet_id, return_codes, topic_count);
            free(return_codes);

            if (suback.data) {
                mqtt_session_send(sess, suback.data, suback.len);
                mqtt_bytes_free(&suback);
            } else {
                mqtt_connection_close(mqtt_session_connection(sess));
            }
            break;
        }

        case MQTT_PKT_PUBLISH: {
            if (!mqtt_session_connected(sess)) {
                return;
            }

            if (pkt->v.publish.qos != 0) {
                // Minimal broker only supports QoS 0
                return;
            }

            mqtt_message_router_publish(b->router, mqtt_session_id(sess), &pkt->v.publish);
            break;
        }

        case MQTT_PKT_PINGREQ: {
            if (!mqtt_session_connected(sess)) {
                return;
            }

            mqtt_bytes_t pingresp = mqtt_encode_pingresp();
            mqtt_session_send(sess, pingresp.data, pingresp.len);
            mqtt_bytes_free(&pingresp);
            break;
        }

        case MQTT_PKT_DISCONNECT: {
            mqtt_connection_close(mqtt_session_connection(sess));
            break;
        }

        default:
            // Unsupported packet type; ignore or close?
            break;
    }
}

static void on_packet(void* user, const mqtt_packet_t* pkt)
{
    packet_ctx_t* ctx = (packet_ctx_t*)user;
    if (!ctx || !ctx->broker || !ctx->sess || !pkt) {
        return;
    }
    handle_packet(ctx->broker, ctx->sess, pkt);
}

static void on_accept_cb(void* user, mqtt_connection_t* c)
{
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

    int fd = mqtt_connection_fd(c);
    printf("Broker: new connection accepted, fd=%d\n", fd);
}

static void on_close_cb(void* user, mqtt_connection_t* c)
{
    mqtt_broker_t* b = (mqtt_broker_t*)user;
    if (!b || !c) {
        return;
    }

    int sid = mqtt_connection_fd(c);
    mqtt_message_router_remove_session(b->router, sid);
    mqtt_session_manager_remove(b->sessions, sid);

    printf("Broker: connection closed, fd=%d\n", sid);
}

static void on_data_cb(void* user, mqtt_connection_t* c)
{
    mqtt_broker_t* b = (mqtt_broker_t*)user;
    if (!b || !c) {
        return;
    }

    int fd = mqtt_connection_fd(c);
    mqtt_session_t* sess = mqtt_session_manager_get(b->sessions, fd);
    if (!sess) {
        return;
    }

    const uint8_t* data = mqtt_connection_in_data(c);
    size_t len = mqtt_connection_in_len(c);
    if (len == 0) {
        return;
    }

    packet_ctx_t ctx = {
        .broker = b,
        .sess = sess
    };

    size_t consumed = 0;
    bool ok = mqtt_decoder_feed(data, len, &consumed, on_packet, &ctx);
    if (!ok) {
        mqtt_connection_close(c);
        return;
    }

    if (consumed > 0) {
        mqtt_connection_in_consume(c, consumed);
    }
}
