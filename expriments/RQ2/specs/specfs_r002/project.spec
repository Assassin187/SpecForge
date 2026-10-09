[PROMPT]
Project design.

[RELY]
- NAME:
buffer
  ROLE:
Systems layer: growable byte buffer primitive with explicit append/consume ownership semantics (no implicit NUL termination, no string semantics).
  DEPENDENCIES:

  ARTIFACTS:
    - NAME:
struct byte_buf
      KIND:
TYPE
      ROLE:
Public buffer representation: data/len/cap
    - NAME:
buffer_init
      KIND:
FUNC
      ROLE:
Zero-initialize a buffer
    - NAME:
buffer_free
      KIND:
FUNC
      ROLE:
Release owned storage
    - NAME:
buffer_reserve
      KIND:
FUNC
      ROLE:
Ensure capacity for extra bytes
    - NAME:
buffer_tail
      KIND:
FUNC
      ROLE:
Borrowed write slot
    - NAME:
buffer_commit
      KIND:
FUNC
      ROLE:
Advance length after external write
    - NAME:
buffer_append
      KIND:
FUNC
      ROLE:
Append bytes with unchanged-on-failure guarantee
    - NAME:
buffer_consume
      KIND:
FUNC
      ROLE:
Drop leading bytes without releasing storage
  FILES:
    - src/buffer.h
    - src/buffer.c
- NAME:
topic
  ROLE:
Protocol policy layer: MQTT Topic Name and Topic Filter validation plus case-sensitive level-wise matching with '+'/'#' wildcards, empty levels and the '$' rule.
  DEPENDENCIES:

  ARTIFACTS:
    - NAME:
topic_name_validate
      KIND:
FUNC
      ROLE:
Validate a PUBLISH topic name
    - NAME:
topic_filter_validate
      KIND:
FUNC
      ROLE:
Validate a SUBSCRIBE topic filter
    - NAME:
topic_filter_matches
      KIND:
FUNC
      ROLE:
Match filter against topic name
  FILES:
    - src/topic.h
    - src/topic.c
- NAME:
wire
  ROLE:
Codec layer: fixed-header/Remaining Length framing, per-packet decoders returning borrowed slices with explicit consumed counts, and packet encoders appending to a caller-owned buffer.
  DEPENDENCIES:
    - buffer
    - topic
  ARTIFACTS:
    - NAME:
enum wire_status
      KIND:
TYPE
      ROLE:
Incomplete/complete/invalid decode outcome
    - NAME:
enum wire_packet_type
      KIND:
TYPE
      ROLE:
Control packet type constants
    - NAME:
struct wire_span
      KIND:
TYPE
      ROLE:
Borrowed byte slice
    - NAME:
struct wire_header
      KIND:
TYPE
      ROLE:
Decoded fixed header
    - NAME:
struct wire_connect
      KIND:
TYPE
      ROLE:
Decoded CONNECT fields as borrowed slices
    - NAME:
struct wire_subscribe
      KIND:
TYPE
      ROLE:
Decoded SUBSCRIBE header fields plus filter list payload
    - NAME:
struct wire_publish
      KIND:
TYPE
      ROLE:
Decoded PUBLISH topic/payload slices and flags
    - NAME:
WIRE_MAX_REMAINING_LENGTH
      KIND:
MACRO
      ROLE:
Protocol maximum Remaining Length
    - NAME:
wire_decode_header
      KIND:
FUNC
      ROLE:
Frame a packet from a stream buffer
    - NAME:
wire_decode_connect
      KIND:
FUNC
      ROLE:
Validate and decode CONNECT
    - NAME:
wire_decode_subscribe
      KIND:
FUNC
      ROLE:
Validate and decode SUBSCRIBE
    - NAME:
wire_subscribe_next
      KIND:
FUNC
      ROLE:
Iterate SUBSCRIBE filter/QoS pairs in order
    - NAME:
wire_decode_publish
      KIND:
FUNC
      ROLE:
Validate and decode PUBLISH
    - NAME:
wire_decode_empty
      KIND:
FUNC
      ROLE:
Validate zero-length PINGREQ/DISCONNECT
    - NAME:
wire_encode_connack
      KIND:
FUNC
      ROLE:
Encode CONNACK
    - NAME:
wire_encode_suback
      KIND:
FUNC
      ROLE:
Encode SUBACK with ordered return codes
    - NAME:
wire_encode_publish
      KIND:
FUNC
      ROLE:
Encode outbound QoS 0 PUBLISH
    - NAME:
wire_encode_pingresp
      KIND:
FUNC
      ROLE:
Encode PINGRESP
  FILES:
    - src/wire.h
    - src/wire.c
- NAME:
net
  ROLE:
Systems layer: POSIX socket listener, accept4 with per-descriptor nonblocking mode, EINTR-safe partial read/write helpers and fd closing.
  DEPENDENCIES:

  ARTIFACTS:
    - NAME:
net_parse_port
      KIND:
FUNC
      ROLE:
Parse <port> argv argument
    - NAME:
net_listen
      KIND:
FUNC
      ROLE:
Create nonblocking TCP listener
    - NAME:
net_accept
      KIND:
FUNC
      ROLE:
Accept a nonblocking peer descriptor
    - NAME:
net_read_some
      KIND:
FUNC
      ROLE:
Partial nonblocking read
    - NAME:
net_write_some
      KIND:
FUNC
      ROLE:
Partial nonblocking write without SIGPIPE
    - NAME:
net_close_fd
      KIND:
FUNC
      ROLE:
Idempotent descriptor close
  FILES:
    - src/net.h
    - src/net.c
- NAME:
session
  ROLE:
State layer: one connection object owning its descriptor, input buffer, queued output buffer, copied client id and copied subscription list, with explicit READY/CLOSING/CLOSED transitions.
  DEPENDENCIES:
    - buffer
    - net
  ARTIFACTS:
    - NAME:
enum session_state
      KIND:
TYPE
      ROLE:
Connection lifecycle state
    - NAME:
struct session_sub
      KIND:
TYPE
      ROLE:
Owned subscription entry exposed read-only
    - NAME:
struct session
      KIND:
TYPE
      ROLE:
Opaque per-connection object
    - NAME:
session_create
      KIND:
FUNC
      ROLE:
Create connection state taking fd ownership
    - NAME:
session_destroy
      KIND:
FUNC
      ROLE:
Release descriptor, buffers and subscriptions
    - NAME:
session_fd
      KIND:
FUNC
      ROLE:
Descriptor getter
    - NAME:
session_state
      KIND:
FUNC
      ROLE:
State getter
    - NAME:
session_mark_ready
      KIND:
FUNC
      ROLE:
AWAITING_CONNECT to READY
    - NAME:
session_close_immediately
      KIND:
FUNC
      ROLE:
Drop output, close descriptor, CLOSED
    - NAME:
session_close_after_flush
      KIND:
FUNC
      ROLE:
CLOSING until queued output drains
    - NAME:
session_input
      KIND:
FUNC
      ROLE:
Borrowed receive buffer
    - NAME:
session_output
      KIND:
FUNC
      ROLE:
Borrowed queued output buffer
    - NAME:
session_enqueue
      KIND:
FUNC
      ROLE:
Append a complete response to queued output
    - NAME:
session_set_client_id
      KIND:
FUNC
      ROLE:
Copy and own the client id
    - NAME:
session_client_id
      KIND:
FUNC
      ROLE:
Read owned client id
    - NAME:
session_add_subscription
      KIND:
FUNC
      ROLE:
Copy filter, replacing identical filter
    - NAME:
session_subscription_count
      KIND:
FUNC
      ROLE:
Subscription count getter
    - NAME:
session_subscription_at
      KIND:
FUNC
      ROLE:
Subscription accessor
    - NAME:
session_matches_topic
      KIND:
FUNC
      ROLE:
Any stored filter matches a topic name
  FILES:
    - src/session.h
    - src/session.c
- NAME:
broker
  ROLE:
Protocol core: connection registry, framing pump over a session's input buffer, per-packet dispatch, CONNECT/CONNACK policy, SUBSCRIBE/SUBACK policy, QoS 0 fan-out with enqueue-failure isolation and session reaping.
  DEPENDENCIES:
    - session
    - wire
    - topic
  ARTIFACTS:
    - NAME:
enum broker_result
      KIND:
TYPE
      ROLE:
Continue or close outcome of packet handling
    - NAME:
struct broker
      KIND:
TYPE
      ROLE:
Opaque broker registry
    - NAME:
broker_create
      KIND:
FUNC
      ROLE:
Create empty registry
    - NAME:
broker_destroy
      KIND:
FUNC
      ROLE:
Destroy every session and the registry
    - NAME:
broker_accept
      KIND:
FUNC
      ROLE:
Adopt an accepted descriptor as a session
    - NAME:
broker_process_input
      KIND:
FUNC
      ROLE:
Consume all complete frames from session input
    - NAME:
broker_session_eof
      KIND:
FUNC
      ROLE:
Drain complete frames then close after flush
    - NAME:
broker_reap
      KIND:
FUNC
      ROLE:
Promote and destroy finished sessions
    - NAME:
broker_session_count
      KIND:
FUNC
      ROLE:
Registry iteration bound
    - NAME:
broker_session_at
      KIND:
FUNC
      ROLE:
Registry iteration accessor
  FILES:
    - src/broker.h
    - src/broker.c
- NAME:
loop
  ROLE:
Execution layer: epoll readiness reactor that accepts peers, dispatches read/write pumps under explicit readiness preconditions, synchronizes interest masks and reaps finished sessions without letting one peer stall another.
  DEPENDENCIES:
    - broker
    - session
    - net
    - buffer
  ARTIFACTS:
    - NAME:
struct loop
      KIND:
TYPE
      ROLE:
Opaque reactor
    - NAME:
loop_create
      KIND:
FUNC
      ROLE:
Create epoll reactor bound to a broker
    - NAME:
loop_add_listener
      KIND:
FUNC
      ROLE:
Register listener for EPOLLIN
    - NAME:
loop_set_stop_flag
      KIND:
FUNC
      ROLE:
Attach signal flag observed between batches
    - NAME:
loop_run
      KIND:
FUNC
      ROLE:
Run readiness loop until stopped
    - NAME:
loop_stop
      KIND:
FUNC
      ROLE:
Request loop termination
    - NAME:
loop_destroy
      KIND:
FUNC
      ROLE:
Release epoll descriptor
  FILES:
    - src/loop.h
    - src/loop.c
- NAME:
app
  ROLE:
Entrypoint layer: ./mqtt_broker <port> argv contract, SIGINT/SIGTERM flag-only handlers, object wiring and ordered teardown; the Makefile and README are build/documentation artifacts of this module.
  DEPENDENCIES:
    - loop
    - broker
    - net
  ARTIFACTS:
    - NAME:
main
      KIND:
FUNC
      ROLE:
Program entrypoint
  FILES:
    - src/main.c

[GUARANTEE]
NAME:
MQTT
SPEC_VERSION:
3.1.1
DEFAULT_PORT:
1883
ROLES:
  - Server (broker)
SCOPE:
  - Concurrent TCP clients accepted by a Linux C99 broker invoked as ./mqtt_broker <port>
  - CONNECT (anonymous, nonempty ASCII client id, Clean Session=1, no Will) with exact CONNACK and CONNECT-first enforcement
  - SUBSCRIBE with one or more filters, requested QoS 0 (0/1/2 requests accepted and granted QoS 0), ordered QoS 0 SUBACK results echoing the Packet Identifier
  - QoS 0 PUBLISH routing to every connected connection with a matching subscription, preserving topic bytes and arbitrary binary payload bytes
  - Topic matching: case-sensitive exact levels, '+' and '#' wildcards, empty levels, '/' boundaries, '$'-prefix rule
  - PINGREQ/PINGRESP, DISCONNECT and TCP-EOF cleanup with per-connection error isolation
  - Stream framing over partial and coalesced frames with incomplete/complete/invalid outcomes and explicit bytes consumed
  - Deterministic ownership of input slices, copied subscriptions, output buffers and connection objects, released on SIGINT/SIGTERM

[SPECIFICATION]
GENERATION_ORDER:
  - buffer
  - topic
  - wire
  - net
  - session
  - broker
  - loop
  - app
PUBLIC_SYMBOLS:
  - NAME:
struct byte_buf
    KIND:
TYPE
    ROLE:
Growable byte buffer
  - NAME:
buffer_init
    KIND:
FUNC
    ROLE:
Initialize buffer
  - NAME:
buffer_free
    KIND:
FUNC
    ROLE:
Free buffer storage
  - NAME:
buffer_reserve
    KIND:
FUNC
    ROLE:
Ensure capacity
  - NAME:
buffer_tail
    KIND:
FUNC
    ROLE:
Borrow write slot
  - NAME:
buffer_commit
    KIND:
FUNC
    ROLE:
Advance length
  - NAME:
buffer_append
    KIND:
FUNC
    ROLE:
Append bytes
  - NAME:
buffer_consume
    KIND:
FUNC
    ROLE:
Drop leading bytes
  - NAME:
topic_name_validate
    KIND:
FUNC
    ROLE:
Topic name validation
  - NAME:
topic_filter_validate
    KIND:
FUNC
    ROLE:
Topic filter validation
  - NAME:
topic_filter_matches
    KIND:
FUNC
    ROLE:
Topic matching
  - NAME:
enum wire_status
    KIND:
TYPE
    ROLE:
Decode outcome
  - NAME:
enum wire_packet_type
    KIND:
TYPE
    ROLE:
Packet type constants
  - NAME:
struct wire_span
    KIND:
TYPE
    ROLE:
Borrowed slice
  - NAME:
struct wire_header
    KIND:
TYPE
    ROLE:
Decoded fixed header
  - NAME:
struct wire_connect
    KIND:
TYPE
    ROLE:
Decoded CONNECT
  - NAME:
struct wire_subscribe
    KIND:
TYPE
    ROLE:
Decoded SUBSCRIBE
  - NAME:
struct wire_publish
    KIND:
TYPE
    ROLE:
Decoded PUBLISH
  - NAME:
WIRE_MAX_REMAINING_LENGTH
    KIND:
MACRO
    ROLE:
Protocol Remaining Length maximum
  - NAME:
wire_decode_header
    KIND:
FUNC
    ROLE:
Frame packet
  - NAME:
wire_decode_connect
    KIND:
FUNC
    ROLE:
Decode CONNECT
  - NAME:
wire_decode_subscribe
    KIND:
FUNC
    ROLE:
Decode SUBSCRIBE
  - NAME:
wire_subscribe_next
    KIND:
FUNC
    ROLE:
Iterate SUBSCRIBE filters
  - NAME:
wire_decode_publish
    KIND:
FUNC
    ROLE:
Decode PUBLISH
  - NAME:
wire_decode_empty
    KIND:
FUNC
    ROLE:
Decode PINGREQ/DISCONNECT
  - NAME:
wire_encode_connack
    KIND:
FUNC
    ROLE:
Encode CONNACK
  - NAME:
wire_encode_suback
    KIND:
FUNC
    ROLE:
Encode SUBACK
  - NAME:
wire_encode_publish
    KIND:
FUNC
    ROLE:
Encode PUBLISH
  - NAME:
wire_encode_pingresp
    KIND:
FUNC
    ROLE:
Encode PINGRESP
  - NAME:
net_parse_port
    KIND:
FUNC
    ROLE:
Parse port argument
  - NAME:
net_listen
    KIND:
FUNC
    ROLE:
Create listener
  - NAME:
net_accept
    KIND:
FUNC
    ROLE:
Accept peer
  - NAME:
net_read_some
    KIND:
FUNC
    ROLE:
Partial read
  - NAME:
net_write_some
    KIND:
FUNC
    ROLE:
Partial write
  - NAME:
net_close_fd
    KIND:
FUNC
    ROLE:
Close descriptor
  - NAME:
enum session_state
    KIND:
TYPE
    ROLE:
Session lifecycle state
  - NAME:
struct session_sub
    KIND:
TYPE
    ROLE:
Owned subscription entry
  - NAME:
struct session
    KIND:
TYPE
    ROLE:
Connection object
  - NAME:
session_create
    KIND:
FUNC
    ROLE:
Create session
  - NAME:
session_destroy
    KIND:
FUNC
    ROLE:
Destroy session
  - NAME:
session_fd
    KIND:
FUNC
    ROLE:
Session fd
  - NAME:
session_state
    KIND:
FUNC
    ROLE:
Session state
  - NAME:
session_mark_ready
    KIND:
FUNC
    ROLE:
Mark ready
  - NAME:
session_close_immediately
    KIND:
FUNC
    ROLE:
Close now
  - NAME:
session_close_after_flush
    KIND:
FUNC
    ROLE:
Close after flush
  - NAME:
session_input
    KIND:
FUNC
    ROLE:
Receive buffer
  - NAME:
session_output
    KIND:
FUNC
    ROLE:
Queued output
  - NAME:
session_enqueue
    KIND:
FUNC
    ROLE:
Queue response
  - NAME:
session_set_client_id
    KIND:
FUNC
    ROLE:
Own client id
  - NAME:
session_client_id
    KIND:
FUNC
    ROLE:
Read client id
  - NAME:
session_add_subscription
    KIND:
FUNC
    ROLE:
Add/replace subscription
  - NAME:
session_subscription_count
    KIND:
FUNC
    ROLE:
Subscription count
  - NAME:
session_subscription_at
    KIND:
FUNC
    ROLE:
Subscription accessor
  - NAME:
session_matches_topic
    KIND:
FUNC
    ROLE:
Matches stored filter
  - NAME:
enum broker_result
    KIND:
TYPE
    ROLE:
Handler outcome
  - NAME:
struct broker
    KIND:
TYPE
    ROLE:
Broker registry
  - NAME:
broker_create
    KIND:
FUNC
    ROLE:
Create broker
  - NAME:
broker_destroy
    KIND:
FUNC
    ROLE:
Destroy broker
  - NAME:
broker_accept
    KIND:
FUNC
    ROLE:
Adopt descriptor
  - NAME:
broker_process_input
    KIND:
FUNC
    ROLE:
Framing pump
  - NAME:
broker_session_eof
    KIND:
FUNC
    ROLE:
EOF handling
  - NAME:
broker_reap
    KIND:
FUNC
    ROLE:
Reap finished sessions
  - NAME:
broker_session_count
    KIND:
FUNC
    ROLE:
Session count
  - NAME:
broker_session_at
    KIND:
FUNC
    ROLE:
Session accessor
  - NAME:
struct loop
    KIND:
TYPE
    ROLE:
epoll reactor
  - NAME:
loop_create
    KIND:
FUNC
    ROLE:
Create reactor
  - NAME:
loop_add_listener
    KIND:
FUNC
    ROLE:
Register listener
  - NAME:
loop_set_stop_flag
    KIND:
FUNC
    ROLE:
Attach stop flag
  - NAME:
loop_run
    KIND:
FUNC
    ROLE:
Readiness loop
  - NAME:
loop_stop
    KIND:
FUNC
    ROLE:
Stop reactor
  - NAME:
loop_destroy
    KIND:
FUNC
    ROLE:
Destroy reactor
  - NAME:
main
    KIND:
FUNC
    ROLE:
Entrypoint
FORBIDDEN_SYMBOLS:
  - NAME:
strlen
    KIND:
FUNC
    REASON:
Topic names, filters and payloads are binary byte slices with explicit lengths; payloads may contain NUL (scope R06).
  - NAME:
strcpy
    KIND:
FUNC
    REASON:
All copies are bounded by explicit lengths; use memcpy with a recorded length.
  - NAME:
strcat
    KIND:
FUNC
    REASON:
Buffer growth goes through buffer_reserve/buffer_append.
  - NAME:
sprintf
    KIND:
FUNC
    REASON:
Wire encoding is explicit byte construction, never formatted text.
  - NAME:
strcmp
    KIND:
FUNC
    REASON:
Topic and filter comparison must be byte-exact and length-aware (memcmp with equal lengths).
  - NAME:
gets
    KIND:
FUNC
    REASON:
Unbounded input.
CONSISTENCY_RULES:
  - ID:
CR-LENGTH
    RULE:
Three distinct length quantities are never interchanged: header_len is the encoded fixed-header size (1 + encoded Remaining Length bytes), remaining_length is the variable header plus payload byte count, and consumed is header_len + remaining_length, the total packet bytes the caller must drop from the receive buffer.
  - ID:
CR-FRAME
    RULE:
A decoder returns WIRE_INCOMPLETE without consuming, WIRE_INVALID for malformed input or a non-matching packet type, and WIRE_COMPLETE only after re-validating the fixed header and the full body for exactly the supplied packet length.
  - ID:
CR-BINARY
    RULE:
Every topic, filter, client id and payload value is handled as bytes plus length; string functions are forbidden and NUL bytes are only ever legal inside PUBLISH payloads.
  - ID:
CR-CLOSE
    RULE:
A malformed in-scope packet, an unsupported in-scope packet needing an unimplemented acknowledgement, and a transient allocation failure close only the offending connection; the listener and all other connections keep running.
  - ID:
CR-OWN
    RULE:
Sessions own their descriptor, input buffer, output buffer, client id copy and subscription filter copies; broker owns every session; loop owns only the epoll descriptor and never the listener fd passed to it.
  - ID:
CR-CLOSE-FLUSH
    RULE:
CONNACK-with-nonzero-return-code and rejection paths that must be observed by the peer use CLOSING (flush queued bytes, then close); protocol-violation and write-error paths close immediately.
  - ID:
CR-READY
    RULE:
Readiness gates: a listener descriptor only accepts, a session descriptor reads only when the dispatch sees a read-side event (EPOLLIN/EPOLLRDHUP/EPOLLHUP/EPOLLERR) and the session is AWAITING_CONNECT or READY, and a write pump runs only when EPOLLOUT was reported and queued output exists; EPOLLOUT alone never permits a read.
  - ID:
CR-MODE
    RULE:
The listener is created nonblocking with socket() plus an explicit fcntl(O_NONBLOCK) (and SOCK_CLOEXEC is applied to the listener and to every accepted descriptor); each accepted descriptor is taken with accept() and then explicitly put in nonblocking mode with fcntl before it is registered, so its mode is established by this code and never assumed to be inherited from the listener.
  - ID:
CR-MASK
    RULE:
After every event batch the loop re-synchronizes each live session's epoll mask from its state and queued-output length, so a session that never sends data again still gets EPOLLOUT for output queued by a peer.
  - ID:
CR-BUILD
    RULE:
The project builds with GNU make and GCC as a multi-file C99 program producing ./mqtt_broker invoked as ./mqtt_broker <port>; a README documents build, run and supported MQTT subset.
