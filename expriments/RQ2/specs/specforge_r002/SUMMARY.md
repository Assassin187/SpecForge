# Spec bundle

Origin: automatic; revision: 2

Protocol: MQTT 3.1.1; role: Server (broker) accepting multiple concurrent client network connections
Runtime: Linux (POSIX sockets, GCC, GNU make); TCP transport C99; ./mqtt_broker <port>

## Required capabilities

- TCP listener accepting concurrent client connections on a port given on the command line
- MQTT 3.1.1 fixed-header parsing with variable-length Remaining Length decoding and stream framing that handles partial and coalesced frames
- CONNECT handling: anonymous connection, nonempty ASCII client ID, Clean Session=1, no Will, exact successful CONNACK, CONNECT-first enforcement, duplicate CONNECT closes that client
- SUBSCRIBE handling with one or more Topic Filters and requested QoS 0, producing one ordered QoS 0 SUBACK return code per filter with the echoed Packet Identifier
- Topic Filter matching: case-sensitive exact levels, single-level '+' and multi-level '#' wildcards, empty levels and '/' level boundaries
- QoS 0 PUBLISH routing to all connected subscribers with matching subscriptions, preserving topic and binary payload bytes and re-framing correctly
- PINGREQ/PINGRESP exchange
- DISCONNECT and TCP-disconnect cleanup: removing that connection's subscriptions and state without affecting other clients
- Input validation for lengths, fixed-header flags, packet-specific fields and connection state; malformed input closes only the offending connection while the broker stays available
- Deterministic resource ownership and release of input slices, subscriptions, output buffers and connection objects, including shutdown on SIGINT/SIGTERM

## Excluded features

- QoS 1 and QoS 2 delivery (PUBACK, PUBREC, PUBREL, PUBCOMP flows)
- Retained messages
- Will messages
- Persistent sessions / CleanSession=0 session resumption
- UNSUBSCRIBE / UNSUBACK
- Authentication (User Name / Password)
- TLS transport
- Idle keepalive timeout scheduling
- Complete MQTT compliance (only the subset required by REQUIREMENTS.md)

Module Spec: module_spec.json

## Files

- src/broker.c: files/src/broker.c.json (src/broker.c)
- src/buffer.c: files/src/buffer.c.json (src/buffer.c)
- src/loop.c: files/src/loop.c.json (src/loop.c)
- src/main.c: files/src/main.c.json (src/main.c)
- src/net.c: files/src/net.c.json (src/net.c)
- src/session.c: files/src/session.c.json (src/session.c)
- src/topic.c: files/src/topic.c.json (src/topic.c)
- src/wire.c: files/src/wire.c.json (src/wire.c)

## Functions

- src/broker.c/add_session: functions/src_broker.c_add_session.json
- src/broker.c/broker_accept: functions/src_broker.c_broker_accept.json
- src/broker.c/broker_create: functions/src_broker.c_broker_create.json
- src/broker.c/broker_destroy: functions/src_broker.c_broker_destroy.json
- src/broker.c/broker_process_input: functions/src_broker.c_broker_process_input.json
- src/broker.c/broker_reap: functions/src_broker.c_broker_reap.json
- src/broker.c/broker_session_at: functions/src_broker.c_broker_session_at.json
- src/broker.c/broker_session_count: functions/src_broker.c_broker_session_count.json
- src/broker.c/broker_session_eof: functions/src_broker.c_broker_session_eof.json
- src/broker.c/handle_connect: functions/src_broker.c_handle_connect.json
- src/broker.c/handle_disconnect: functions/src_broker.c_handle_disconnect.json
- src/broker.c/handle_pingreq: functions/src_broker.c_handle_pingreq.json
- src/broker.c/handle_publish: functions/src_broker.c_handle_publish.json
- src/broker.c/handle_subscribe: functions/src_broker.c_handle_subscribe.json
- src/broker.c/remove_session_at: functions/src_broker.c_remove_session_at.json
- src/broker.c/route_publish: functions/src_broker.c_route_publish.json
- src/buffer.c/buffer_append: functions/src_buffer.c_buffer_append.json
- src/buffer.c/buffer_commit: functions/src_buffer.c_buffer_commit.json
- src/buffer.c/buffer_consume: functions/src_buffer.c_buffer_consume.json
- src/buffer.c/buffer_free: functions/src_buffer.c_buffer_free.json
- src/buffer.c/buffer_init: functions/src_buffer.c_buffer_init.json
- src/buffer.c/buffer_reserve: functions/src_buffer.c_buffer_reserve.json
- src/buffer.c/buffer_tail: functions/src_buffer.c_buffer_tail.json
- src/loop.c/accept_ready: functions/src_loop.c_accept_ready.json
- src/loop.c/dispatch_event: functions/src_loop.c_dispatch_event.json
- src/loop.c/is_registered: functions/src_loop.c_is_registered.json
- src/loop.c/loop_add_listener: functions/src_loop.c_loop_add_listener.json
- src/loop.c/loop_create: functions/src_loop.c_loop_create.json
- src/loop.c/loop_destroy: functions/src_loop.c_loop_destroy.json
- src/loop.c/loop_run: functions/src_loop.c_loop_run.json
- src/loop.c/loop_set_stop_flag: functions/src_loop.c_loop_set_stop_flag.json
- src/loop.c/loop_stop: functions/src_loop.c_loop_stop.json
- src/loop.c/mask_sync: functions/src_loop.c_mask_sync.json
- src/loop.c/pump_read: functions/src_loop.c_pump_read.json
- src/loop.c/pump_write: functions/src_loop.c_pump_write.json
- src/main.c/main: functions/src_main.c_main.json
- src/main.c/on_signal: functions/src_main.c_on_signal.json
- src/net.c/net_accept: functions/src_net.c_net_accept.json
- src/net.c/net_close_fd: functions/src_net.c_net_close_fd.json
- src/net.c/net_listen: functions/src_net.c_net_listen.json
- src/net.c/net_parse_port: functions/src_net.c_net_parse_port.json
- src/net.c/net_read_some: functions/src_net.c_net_read_some.json
- src/net.c/net_write_some: functions/src_net.c_net_write_some.json
- src/session.c/free_subscriptions: functions/src_session.c_free_subscriptions.json
- src/session.c/release_fd: functions/src_session.c_release_fd.json
- src/session.c/session_add_subscription: functions/src_session.c_session_add_subscription.json
- src/session.c/session_client_id: functions/src_session.c_session_client_id.json
- src/session.c/session_close_after_flush: functions/src_session.c_session_close_after_flush.json
- src/session.c/session_close_immediately: functions/src_session.c_session_close_immediately.json
- src/session.c/session_create: functions/src_session.c_session_create.json
- src/session.c/session_destroy: functions/src_session.c_session_destroy.json
- src/session.c/session_enqueue: functions/src_session.c_session_enqueue.json
- src/session.c/session_fd: functions/src_session.c_session_fd.json
- src/session.c/session_input: functions/src_session.c_session_input.json
- src/session.c/session_mark_ready: functions/src_session.c_session_mark_ready.json
- src/session.c/session_matches_topic: functions/src_session.c_session_matches_topic.json
- src/session.c/session_output: functions/src_session.c_session_output.json
- src/session.c/session_set_client_id: functions/src_session.c_session_set_client_id.json
- src/session.c/session_state: functions/src_session.c_session_state.json
- src/session.c/session_subscription_at: functions/src_session.c_session_subscription_at.json
- src/session.c/session_subscription_count: functions/src_session.c_session_subscription_count.json
- src/topic.c/topic_filter_matches: functions/src_topic.c_topic_filter_matches.json
- src/topic.c/topic_filter_validate: functions/src_topic.c_topic_filter_validate.json
- src/topic.c/topic_name_validate: functions/src_topic.c_topic_name_validate.json
- src/topic.c/topic_utf8_validate: functions/src_topic.c_topic_utf8_validate.json
- src/wire.c/wire_declared_body_len: functions/src_wire.c_wire_declared_body_len.json
- src/wire.c/wire_decode_connect: functions/src_wire.c_wire_decode_connect.json
- src/wire.c/wire_decode_empty: functions/src_wire.c_wire_decode_empty.json
- src/wire.c/wire_decode_header: functions/src_wire.c_wire_decode_header.json
- src/wire.c/wire_decode_publish: functions/src_wire.c_wire_decode_publish.json
- src/wire.c/wire_decode_string: functions/src_wire.c_wire_decode_string.json
- src/wire.c/wire_decode_subscribe: functions/src_wire.c_wire_decode_subscribe.json
- src/wire.c/wire_encode_connack: functions/src_wire.c_wire_encode_connack.json
- src/wire.c/wire_encode_pingresp: functions/src_wire.c_wire_encode_pingresp.json
- src/wire.c/wire_encode_publish: functions/src_wire.c_wire_encode_publish.json
- src/wire.c/wire_encode_suback: functions/src_wire.c_wire_encode_suback.json
- src/wire.c/wire_read_u16: functions/src_wire.c_wire_read_u16.json
- src/wire.c/wire_subscribe_next: functions/src_wire.c_wire_subscribe_next.json
- src/wire.c/wire_write_remaining_length: functions/src_wire.c_wire_write_remaining_length.json

## Processing chain

- accept-and-register: net_listen creates the nonblocking listener; loop_run sees data.ptr NULL and calls accept_ready, which accepts a bounded burst through net_accept and hands each nonblocking, close-on-exec descriptor to broker_accept/add_session, so every new connection starts as an AWAITING_CONNECT session with empty buffers that mask_sync registers for EPOLLIN.
- frame-and-dispatch: pump_read appends recv bytes into the session input buffer and broker_process_input repeatedly frames one packet with wire_decode_header, dispatches to the CONNECT/SUBSCRIBE/PUBLISH/PINGREQ/DISCONNECT handler and consumes exactly header total_len; INCOMPLETE leaves the tail buffered, INVALID closes only that session.
- connect-answer: handle_connect rejects a non-CONNECT-first or duplicate CONNECT target, stores the client id copy through session_set_client_id, queues the exact 20 02 00 00 CONNACK, marks the session READY and evicts an older READY session with the same identifier; unsupported level or identifier gets return code 1/2 then close-after-flush.
- subscribe-register: handle_subscribe walks the validated filter list with wire_subscribe_next, replaces or appends each QoS 0 subscription through session_add_subscription and queues one SUBACK whose variable part echoes the nonzero Packet Identifier and holds one 0x00 code per filter in order.
- publish-fanout: handle_publish rejects QoS 1/2 and DUP-at-QoS-0 by closing the publisher, otherwise route_publish encodes one QoS 0 PUBLISH from a private scratch buffer and enqueues it to every READY session whose stored filters match the topic (publisher included); only a recipient whose enqueue fails is closed, and mask_sync then arms EPOLLOUT for the queued recipients.
- pingreq-response: handle_pingreq queues the exact two-byte PINGRESP through session_output, leaving the READY state and all other queued bytes untouched so business traffic continues.
- eof-and-teardown: broker_session_eof processes every complete buffered frame first (including a final PUBLISH followed by write-side shutdown), leaves an incomplete tail discarded, moves the session to CLOSING with its queued output preserved; pump_write drains it, broker_reap closes and destroys drained sessions, and handle_disconnect closes immediately without flushing.
- signal-shutdown: on_signal only stores 1 into the sig_atomic_t flag; loop_run observes the flag or loop_stop and returns, then main tears down in order loop_destroy, net_close_fd(&listener), broker_destroy, so every session descriptor, buffer, copied identifier, subscription and array is released.
