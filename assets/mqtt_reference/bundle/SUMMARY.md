# Spec bundle

Origin: manual_reference; revision: 1

Runtime: Linux C99 TCP, ./mqtt_broker <port>

Module Spec: module_spec.json

## Files

- src/broker/broker.c: files/mqtt__broker__broker.json (mqtt/broker/broker)
- src/broker/session.c: files/mqtt__broker__session.json (mqtt/broker/session)
- src/broker/session_manager.c: files/mqtt__broker__session_manager.json (mqtt/broker/session_manager)
- src/main.c: files/mqtt__main.json (mqtt/main)
- src/network/connection.c: files/mqtt__network__connection.json (mqtt/network/connection)
- src/network/tcp_server.c: files/mqtt__network__tcp_server.json (mqtt/network/tcp_server)
- src/protocol/mqtt_decoder.c: files/mqtt__protocol__mqtt_decoder.json (mqtt/protocol/mqtt_decoder)
- src/protocol/mqtt_encoder.c: files/mqtt__protocol__mqtt_encoder.json (mqtt/protocol/mqtt_encoder)
- src/protocol/mqtt_packet.c: files/mqtt__protocol__mqtt_packet.json (mqtt/protocol/mqtt_packet)
- src/router/message_router.c: files/mqtt__router__message_router.json (mqtt/router/message_router)
- src/topic/topic_tree.c: files/mqtt__topic__topic_tree.json (mqtt/topic/topic_tree)

## Functions

- mqtt/broker/broker/handle_packet: functions/mqtt/broker/broker/handle_packet.json
- mqtt/broker/broker/mqtt_broker_create: functions/mqtt/broker/broker/mqtt_broker_create.json
- mqtt/broker/broker/mqtt_broker_destroy: functions/mqtt/broker/broker/mqtt_broker_destroy.json
- mqtt/broker/broker/mqtt_broker_run: functions/mqtt/broker/broker/mqtt_broker_run.json
- mqtt/broker/broker/mqtt_broker_start: functions/mqtt/broker/broker/mqtt_broker_start.json
- mqtt/broker/broker/mqtt_broker_stop: functions/mqtt/broker/broker/mqtt_broker_stop.json
- mqtt/broker/broker/on_accept_cb: functions/mqtt/broker/broker/on_accept_cb.json
- mqtt/broker/broker/on_close_cb: functions/mqtt/broker/broker/on_close_cb.json
- mqtt/broker/broker/on_data_cb: functions/mqtt/broker/broker/on_data_cb.json
- mqtt/broker/broker/on_packet: functions/mqtt/broker/broker/on_packet.json
- mqtt/broker/session/mqtt_session_client_id: functions/mqtt/broker/session/mqtt_session_client_id.json
- mqtt/broker/session/mqtt_session_connected: functions/mqtt/broker/session/mqtt_session_connected.json
- mqtt/broker/session/mqtt_session_connection: functions/mqtt/broker/session/mqtt_session_connection.json
- mqtt/broker/session/mqtt_session_create: functions/mqtt/broker/session/mqtt_session_create.json
- mqtt/broker/session/mqtt_session_destroy: functions/mqtt/broker/session/mqtt_session_destroy.json
- mqtt/broker/session/mqtt_session_id: functions/mqtt/broker/session/mqtt_session_id.json
- mqtt/broker/session/mqtt_session_mark_connected: functions/mqtt/broker/session/mqtt_session_mark_connected.json
- mqtt/broker/session/mqtt_session_send: functions/mqtt/broker/session/mqtt_session_send.json
- mqtt/broker/session_manager/ensure_cap: functions/mqtt/broker/session_manager/ensure_cap.json
- mqtt/broker/session_manager/mqtt_session_manager_add: functions/mqtt/broker/session_manager/mqtt_session_manager_add.json
- mqtt/broker/session_manager/mqtt_session_manager_create: functions/mqtt/broker/session_manager/mqtt_session_manager_create.json
- mqtt/broker/session_manager/mqtt_session_manager_destroy: functions/mqtt/broker/session_manager/mqtt_session_manager_destroy.json
- mqtt/broker/session_manager/mqtt_session_manager_get: functions/mqtt/broker/session_manager/mqtt_session_manager_get.json
- mqtt/broker/session_manager/mqtt_session_manager_remove: functions/mqtt/broker/session_manager/mqtt_session_manager_remove.json
- mqtt/main/main: functions/mqtt/main/main.json
- mqtt/network/connection/ensure_in_cap: functions/mqtt/network/connection/ensure_in_cap.json
- mqtt/network/connection/mqtt_connection_close: functions/mqtt/network/connection/mqtt_connection_close.json
- mqtt/network/connection/mqtt_connection_closed: functions/mqtt/network/connection/mqtt_connection_closed.json
- mqtt/network/connection/mqtt_connection_create: functions/mqtt/network/connection/mqtt_connection_create.json
- mqtt/network/connection/mqtt_connection_destroy: functions/mqtt/network/connection/mqtt_connection_destroy.json
- mqtt/network/connection/mqtt_connection_fd: functions/mqtt/network/connection/mqtt_connection_fd.json
- mqtt/network/connection/mqtt_connection_flush: functions/mqtt/network/connection/mqtt_connection_flush.json
- mqtt/network/connection/mqtt_connection_in_consume: functions/mqtt/network/connection/mqtt_connection_in_consume.json
- mqtt/network/connection/mqtt_connection_in_data: functions/mqtt/network/connection/mqtt_connection_in_data.json
- mqtt/network/connection/mqtt_connection_in_len: functions/mqtt/network/connection/mqtt_connection_in_len.json
- mqtt/network/connection/mqtt_connection_peer: functions/mqtt/network/connection/mqtt_connection_peer.json
- mqtt/network/connection/mqtt_connection_read: functions/mqtt/network/connection/mqtt_connection_read.json
- mqtt/network/connection/mqtt_connection_send: functions/mqtt/network/connection/mqtt_connection_send.json
- mqtt/network/connection/mqtt_connection_set_peer: functions/mqtt/network/connection/mqtt_connection_set_peer.json
- mqtt/network/connection/mqtt_connection_want_write: functions/mqtt/network/connection/mqtt_connection_want_write.json
- mqtt/network/tcp_server/accept_loop: functions/mqtt/network/tcp_server/accept_loop.json
- mqtt/network/tcp_server/close_connection: functions/mqtt/network/tcp_server/close_connection.json
- mqtt/network/tcp_server/find_conn: functions/mqtt/network/tcp_server/find_conn.json
- mqtt/network/tcp_server/mqtt_tcp_server_create: functions/mqtt/network/tcp_server/mqtt_tcp_server_create.json
- mqtt/network/tcp_server/mqtt_tcp_server_destroy: functions/mqtt/network/tcp_server/mqtt_tcp_server_destroy.json
- mqtt/network/tcp_server/mqtt_tcp_server_run: functions/mqtt/network/tcp_server/mqtt_tcp_server_run.json
- mqtt/network/tcp_server/mqtt_tcp_server_start: functions/mqtt/network/tcp_server/mqtt_tcp_server_start.json
- mqtt/network/tcp_server/mqtt_tcp_server_stop: functions/mqtt/network/tcp_server/mqtt_tcp_server_stop.json
- mqtt/network/tcp_server/remove_conn: functions/mqtt/network/tcp_server/remove_conn.json
- mqtt/network/tcp_server/set_nonblocking: functions/mqtt/network/tcp_server/set_nonblocking.json
- mqtt/network/tcp_server/setup_epoll: functions/mqtt/network/tcp_server/setup_epoll.json
- mqtt/network/tcp_server/setup_listen_socket: functions/mqtt/network/tcp_server/setup_listen_socket.json
- mqtt/network/tcp_server/sockaddr_to_string: functions/mqtt/network/tcp_server/sockaddr_to_string.json
- mqtt/network/tcp_server/update_interest: functions/mqtt/network/tcp_server/update_interest.json
- mqtt/protocol/mqtt_decoder/decode_one: functions/mqtt/protocol/mqtt_decoder/decode_one.json
- mqtt/protocol/mqtt_decoder/mqtt_decoder_feed: functions/mqtt/protocol/mqtt_decoder/mqtt_decoder_feed.json
- mqtt/protocol/mqtt_decoder/read_string: functions/mqtt/protocol/mqtt_decoder/read_string.json
- mqtt/protocol/mqtt_decoder/read_u16: functions/mqtt/protocol/mqtt_decoder/read_u16.json
- mqtt/protocol/mqtt_decoder/try_parse_remaining_length: functions/mqtt/protocol/mqtt_decoder/try_parse_remaining_length.json
- mqtt/protocol/mqtt_encoder/make_bytes: functions/mqtt/protocol/mqtt_encoder/make_bytes.json
- mqtt/protocol/mqtt_encoder/mqtt_bytes_free: functions/mqtt/protocol/mqtt_encoder/mqtt_bytes_free.json
- mqtt/protocol/mqtt_encoder/mqtt_encode_connack: functions/mqtt/protocol/mqtt_encoder/mqtt_encode_connack.json
- mqtt/protocol/mqtt_encoder/mqtt_encode_pingresp: functions/mqtt/protocol/mqtt_encoder/mqtt_encode_pingresp.json
- mqtt/protocol/mqtt_encoder/mqtt_encode_publish_qos0: functions/mqtt/protocol/mqtt_encoder/mqtt_encode_publish_qos0.json
- mqtt/protocol/mqtt_encoder/mqtt_encode_suback: functions/mqtt/protocol/mqtt_encoder/mqtt_encode_suback.json
- mqtt/protocol/mqtt_encoder/put_remaining_length: functions/mqtt/protocol/mqtt_encoder/put_remaining_length.json
- mqtt/protocol/mqtt_encoder/put_u16: functions/mqtt/protocol/mqtt_encoder/put_u16.json
- mqtt/protocol/mqtt_encoder/remaining_length_bytes: functions/mqtt/protocol/mqtt_encoder/remaining_length_bytes.json
- mqtt/protocol/mqtt_packet/mqtt_packet_free: functions/mqtt/protocol/mqtt_packet/mqtt_packet_free.json
- mqtt/router/message_router/mqtt_message_router_create: functions/mqtt/router/message_router/mqtt_message_router_create.json
- mqtt/router/message_router/mqtt_message_router_destroy: functions/mqtt/router/message_router/mqtt_message_router_destroy.json
- mqtt/router/message_router/mqtt_message_router_publish: functions/mqtt/router/message_router/mqtt_message_router_publish.json
- mqtt/router/message_router/mqtt_message_router_remove_session: functions/mqtt/router/message_router/mqtt_message_router_remove_session.json
- mqtt/router/message_router/mqtt_message_router_subscribe: functions/mqtt/router/message_router/mqtt_message_router_subscribe.json
- mqtt/router/message_router/mqtt_message_router_unsubscribe: functions/mqtt/router/message_router/mqtt_message_router_unsubscribe.json
- mqtt/topic/topic_tree/delete_entry: functions/mqtt/topic/topic_tree/delete_entry.json
- mqtt/topic/topic_tree/ensure_entry_cap: functions/mqtt/topic/topic_tree/ensure_entry_cap.json
- mqtt/topic/topic_tree/ensure_sid_cap: functions/mqtt/topic/topic_tree/ensure_sid_cap.json
- mqtt/topic/topic_tree/entry_has_sid: functions/mqtt/topic/topic_tree/entry_has_sid.json
- mqtt/topic/topic_tree/entry_remove_sid: functions/mqtt/topic/topic_tree/entry_remove_sid.json
- mqtt/topic/topic_tree/find_entry: functions/mqtt/topic/topic_tree/find_entry.json
- mqtt/topic/topic_tree/mqtt_topic_match: functions/mqtt/topic/topic_tree/mqtt_topic_match.json
- mqtt/topic/topic_tree/mqtt_topic_tree_create: functions/mqtt/topic/topic_tree/mqtt_topic_tree_create.json
- mqtt/topic/topic_tree/mqtt_topic_tree_destroy: functions/mqtt/topic/topic_tree/mqtt_topic_tree_destroy.json
- mqtt/topic/topic_tree/mqtt_topic_tree_match_subscribers: functions/mqtt/topic/topic_tree/mqtt_topic_tree_match_subscribers.json
- mqtt/topic/topic_tree/mqtt_topic_tree_remove_session: functions/mqtt/topic/topic_tree/mqtt_topic_tree_remove_session.json
- mqtt/topic/topic_tree/mqtt_topic_tree_subscribe: functions/mqtt/topic/topic_tree/mqtt_topic_tree_subscribe.json
- mqtt/topic/topic_tree/mqtt_topic_tree_unsubscribe: functions/mqtt/topic/topic_tree/mqtt_topic_tree_unsubscribe.json
- mqtt/topic/topic_tree/next_level: functions/mqtt/topic/topic_tree/next_level.json

## Processing chain

