/*
 * session.h - per-connection state for the MQTT 3.1.1 broker.
 *
 * A session owns exactly one accepted (nonblocking) descriptor, one receive
 * buffer holding unconsumed stream bytes, one output buffer holding complete
 * encoded packets waiting to be written, a copy of the client identifier and a
 * copy of every accepted subscription filter.
 *
 * Ownership:
 *   - session_create() takes ownership of the descriptor: it is closed exactly
 *     once by session_close_immediately(), session_destroy() or the flush
 *     completion path of a CLOSING session.
 *   - Buffers and strings reachable through this interface are borrowed and
 *     stay valid until the next mutating session_* call or session_destroy().
 *   - Callers never free the descriptor, the byte buffers or the filter copies.
 *
 * State machine:
 *   AWAITING_CONNECT -> READY        (session_mark_ready)
 *   AWAITING_CONNECT -> CLOSED       (session_close_immediately)
 *   READY            -> CLOSING      (session_close_after_flush)
 *   READY            -> CLOSED       (session_close_immediately)
 *   CLOSING          -> CLOSED       (session_close_immediately)
 *   CLOSED is terminal; every transition function is idempotent.
 *
 * Repeated operations: session_close_immediately() on an already CLOSED session
 * and session_destroy() on a CLOSED session are both no-ops, so a session that
 * has already released its descriptor is never closed twice. session_enqueue()
 * appends to the existing queued output (it never replaces or frees it) and
 * session_set_client_id() releases the previously owned copy before storing the
 * new one.
 */
#ifndef MQTT_BROKER_SESSION_H
#define MQTT_BROKER_SESSION_H

#include <stddef.h>
#include <stdint.h>

#include "buffer.h"

/* Connection lifecycle state. */
enum session_state {
    SESSION_AWAITING_CONNECT = 0, /* created, no valid CONNECT processed yet */
    SESSION_READY = 1,            /* CONNACK sent, protocol traffic allowed */
    SESSION_CLOSING = 2,          /* queue must drain, then the fd is closed */
    SESSION_CLOSED = 3            /* terminal: fd already closed, no output */
};

/* Read-only view of one stored subscription. */
struct session_sub {
    const char *filter;  /* owned by the session, not NUL terminated */
    size_t filter_len;   /* length of filter in bytes */
    uint8_t qos;         /* granted QoS 0..2 returned by SUBACK */
};

/* Opaque per-connection object. */
struct session;

/*
 * Create a session adopting fd (which must already be nonblocking).
 * Returns 0 on success and -1 on failure (invalid arguments or allocation
 * failure); on failure the descriptor is NOT closed and remains the caller's
 * responsibility. On success *out_session is set and the caller owns it.
 */
int session_create(int fd, struct session **out_session);

/* Close the descriptor if still open, release all owned storage, free s. */
void session_destroy(struct session *s);

/* Borrowed descriptor; -1 once the descriptor has been closed. */
int session_fd(const struct session *s);

/* Current lifecycle state, SESSION_CLOSED for a NULL session. */
enum session_state session_state(const struct session *s);

/* Transition AWAITING_CONNECT -> READY. Ignored in any other state. */
void session_mark_ready(struct session *s);

/*
 * Drop all queued output, close the descriptor, enter CLOSED. No-op when the
 * session is NULL or already CLOSED.
 */
void session_close_immediately(struct session *s);

/*
 * Enter CLOSING: stop accepting protocol input, keep queued output, and let the
 * writer flush it before the descriptor is closed. No-op from CLOSED.
 */
void session_close_after_flush(struct session *s);

/* Borrowed receive buffer (unconsumed stream bytes). */
struct byte_buf *session_input(struct session *s);

/* Borrowed queued-output buffer. */
struct byte_buf *session_output(struct session *s);

/*
 * Append len bytes from data to the queued output as one unit. Returns 0 on
 * success and -1 on failure (invalid arguments or allocation failure); on
 * failure the previously queued bytes are untouched and no bytes are appended.
 * data is borrowed and not retained.
 */
int session_enqueue(struct session *s, const void *data, size_t len);

/*
 * Copy id_len bytes of id into session-owned storage, replacing any previously
 * stored client identifier (the old copy is released). Returns 0 on success,
 * -1 on invalid arguments or allocation failure (previous identifier kept).
 */
int session_set_client_id(struct session *s, const char *id, size_t id_len);

/*
 * Borrowed client identifier; NULL when none is stored. When out_len is not
 * NULL it receives the stored length in bytes.
 */
const char *session_client_id(const struct session *s, size_t *out_len);

/*
 * Copy filter and add it to the subscription list. A subscription with an
 * identical filter is replaced in place (same position, new qos) rather than
 * duplicated. Returns 0 on success, -1 on invalid arguments or allocation
 * failure (the subscription list is unchanged).
 */
int session_add_subscription(struct session *s, const char *filter,
                             size_t filter_len, uint8_t qos);

/* Number of stored subscriptions. */
size_t session_subscription_count(const struct session *s);

/* Borrowed subscription at index, NULL when index is out of range. */
const struct session_sub *session_subscription_at(const struct session *s,
                                                  size_t index);

/*
 * Return 1 when any stored filter matches the given topic name and 0 otherwise.
 * topic is borrowed for the duration of the call.
 */
int session_matches_topic(const struct session *s, const char *topic,
                          size_t topic_len);

#endif /* MQTT_BROKER_SESSION_H */
