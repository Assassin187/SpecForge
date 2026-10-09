/*
 * broker.h - MQTT 3.1.1 broker protocol core.
 *
 * The broker owns a registry of sessions. It performs no I/O of its own: the
 * execution layer fills a session's receive buffer and drains its output
 * buffer, while the broker frames packets out of that receive buffer, applies
 * the protocol policy and queues complete responses into the output buffer.
 *
 * Ownership:
 *   - broker_accept() takes ownership of the accepted descriptor on success
 *     only; on failure the descriptor is still owned by the caller.
 *   - Every session in the registry is owned by the broker and destroyed by
 *     broker_destroy() or broker_reap(). Callers only borrow session pointers.
 *   - A session pointer returned by broker_session_at() is invalidated by the
 *     next broker_reap(), broker_accept() or broker_destroy() call.
 *
 * Error isolation: a malformed or unsupported packet closes only the
 * connection that delivered it. A failure to queue output for one fan-out
 * recipient closes only that recipient.
 */
#ifndef MQTT_BROKER_BROKER_H
#define MQTT_BROKER_BROKER_H

#include <stddef.h>

#include "session.h"

/* Outcome of handling one packet inside a session. */
enum broker_result {
    BROKER_CONTINUE = 0, /* session stays usable */
    BROKER_CLOSE = 1     /* session is or must be closed */
};

/* Opaque registry of active connections. */
struct broker;

/* Create an empty registry. Returns 0 on success, -1 on allocation failure. */
int broker_create(struct broker **out_broker);

/* Destroy every session still in the registry, then the registry itself. */
void broker_destroy(struct broker *b);

/*
 * Adopt an accepted nonblocking descriptor as a new session in the
 * AWAITING_CONNECT state. Returns 0 on success (the broker now owns fd) and -1
 * on failure (fd is not closed and remains the caller's responsibility).
 */
int broker_accept(struct broker *b, int fd);

/*
 * Frame and handle every complete packet currently held in s's receive buffer,
 * consuming the bytes of each handled packet. Returns 0 when the session may
 * continue and -1 when the session was closed as a result of its input (the
 * caller must then reap and re-synchronize its interest mask).
 */
int broker_process_input(struct broker *b, struct session *s);

/*
 * The peer closed its write side or a read error occurred: process every
 * complete packet still buffered (so a final PUBLISH is still routed), then
 * close after the queued output has been flushed. Any trailing partial packet
 * bytes are discarded.
 */
void broker_session_eof(struct broker *b, struct session *s);

/*
 * Finish and remove sessions: a CLOSING session with empty queued output is
 * closed, then every CLOSED session is destroyed and removed from the registry.
 */
void broker_reap(struct broker *b);

/* Number of sessions currently in the registry. */
size_t broker_session_count(const struct broker *b);

/* Borrowed session at index, NULL when index is out of range. */
struct session *broker_session_at(const struct broker *b, size_t index);

#endif /* MQTT_BROKER_BROKER_H */
