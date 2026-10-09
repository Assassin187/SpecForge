/*
 * loop.h - epoll level-triggered execution layer for the broker.
 *
 * Descriptor modes and readiness preconditions
 * --------------------------------------------
 * The listener descriptor is created by net_listen() and the peer descriptors
 * by net_accept(); both are O_NONBLOCK|FD_CLOEXEC before they reach this layer,
 * so the accept path never depends on inheriting the listener's flags.
 *
 * Every descriptor is registered level-triggered. The listener is registered
 * with event data pointer NULL; every session descriptor is registered with its
 * struct session pointer as the data pointer.
 *
 *   - A read pump is dispatched ONLY when the returned event contains
 *     EPOLLIN, EPOLLRDHUP, EPOLLHUP or EPOLLERR, and only while the session is
 *     in AWAITING_CONNECT or READY. EPOLLOUT alone never permits a read.
 *   - A write pump is dispatched only when the event contains EPOLLOUT and the
 *     session already has queued output; EPOLLOUT while idle is ignored.
 *   - Readiness is recomputed after every dispatched event: a session is
 *     registered for EPOLLIN while it may still accept protocol input and for
 *     EPOLLOUT while its queued output is non-empty. Queued output is therefore
 *     always delivered to an idle recipient that sends nothing further.
 *   - Work per descriptor per event batch is bounded (read/write calls and
 *     accepted peers per listener), so one unread peer cannot stall another.
 *
 * Stopping: a signal handler may only store a value into a volatile
 * sig_atomic_t object handed to loop_set_stop_flag(); loop_run() observes that
 * flag and also loop_stop(), and returns so that ordinary code performs all
 * cleanup.
 *
 * Ownership: the loop borrows the broker and the listener descriptor. Neither
 * is closed or destroyed by loop_destroy(); the caller destroys the broker and
 * closes the listener after loop_destroy().
 */
#ifndef MQTT_BROKER_LOOP_H
#define MQTT_BROKER_LOOP_H

#include <signal.h>
#include <stddef.h>

#include "broker.h"

/* Opaque reactor instance. */
struct loop;

/*
 * Create a reactor bound to broker. Returns 0 on success and -1 on failure
 * (invalid argument or epoll_create1 failure). The caller owns the loop.
 */
int loop_create(struct broker *broker, struct loop **out_loop);

/*
 * Register a nonblocking listener descriptor. Returns 0 on success and -1 on
 * failure; on failure the listener stays usable and owned by the caller.
 */
int loop_add_listener(struct loop *l, int listener_fd);

/*
 * Point the loop at a flag written only by a signal handler. Passing NULL
 * clears the pointer. The object must outlive the running loop.
 */
void loop_set_stop_flag(struct loop *l, volatile sig_atomic_t *stop_flag);

/*
 * Run until the stop flag is set, loop_stop() is called, or a fatal reactor
 * error occurs. Returns 0 for a clean stop and -1 on a fatal error.
 */
int loop_run(struct loop *l);

/* Request that a running loop_run() return after the current event batch. */
void loop_stop(struct loop *l);

/* Release the epoll descriptor and the loop object; does not touch the broker. */
void loop_destroy(struct loop *l);

#endif /* MQTT_BROKER_LOOP_H */
