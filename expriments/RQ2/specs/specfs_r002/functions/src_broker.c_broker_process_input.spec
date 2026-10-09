[PROMPT]
Frame every complete packet currently buffered in a session's receive buffer, dispatch it to the matching protocol handler, consume exactly its bytes and stop at the first incomplete frame, the first malformed frame or a packet that closes the connection.

[RELY]
STRUCT:
  - NAME:
struct broker
    ROLE:
registry passed to the handlers; the private registry definition allows direct access to the slot array
  - NAME:
struct session
    ROLE:
connection whose receive buffer holds the stream bytes and whose state selects the permitted packets
  - NAME:
struct byte_buf
    ROLE:
session receive buffer; bytes of each handled packet are consumed from it
  - NAME:
struct wire_header
    ROLE:
frame description: type, flags, remaining_length, header_len and total_len
  - NAME:
struct wire_connect
    ROLE:
decoded CONNECT passed to handle_connect
  - NAME:
struct wire_subscribe
    ROLE:
decoded SUBSCRIBE passed to handle_subscribe
  - NAME:
struct wire_publish
    ROLE:
decoded PUBLISH passed to handle_publish
FUNC:
  - NAME:
session_input
    KIND:
CALL
    ROLE:
borrow the receive buffer to frame from
  - NAME:
session_state
    KIND:
CALL
    ROLE:
select the packet set permitted by the connection state
  - NAME:
session_close_immediately
    KIND:
CALL
    ROLE:
close on an invalid frame, a second CONNECT or a packet before CONNECT
  - NAME:
wire_decode_header
    KIND:
CALL
    ROLE:
frame one packet and report WIRE_INCOMPLETE / WIRE_COMPLETE / WIRE_INVALID
  - NAME:
wire_decode_connect
    KIND:
CALL
    ROLE:
decode the CONNECT body
  - NAME:
wire_decode_subscribe
    KIND:
CALL
    ROLE:
decode and validate the SUBSCRIBE body
  - NAME:
wire_decode_publish
    KIND:
CALL
    ROLE:
decode the PUBLISH topic, payload and flags
  - NAME:
wire_decode_empty
    KIND:
CALL
    ROLE:
validate the PINGREQ/DISCONNECT fixed header and zero Remaining Length
  - NAME:
buffer_consume
    KIND:
CALL
    ROLE:
drop the handled frame's bytes from the receive buffer
  - NAME:
handle_connect
    KIND:
CALL
    ROLE:
same-file: apply the CONNECT policy
  - NAME:
handle_subscribe
    KIND:
CALL
    ROLE:
same-file: store filters and queue SUBACK
  - NAME:
handle_publish
    KIND:
CALL
    ROLE:
same-file: fan out a QoS 0 PUBLISH
  - NAME:
handle_pingreq
    KIND:
CALL
    ROLE:
same-file: queue PINGRESP
  - NAME:
handle_disconnect
    KIND:
CALL
    ROLE:
same-file: close without flushing
VAR:
  - NAME:
WIRE_INCOMPLETE
    ROLE:
framing outcome that ends the loop with the partial bytes kept
  - NAME:
WIRE_COMPLETE
    ROLE:
framing/decoding outcome that permits dispatch
  - NAME:
WIRE_INVALID
    ROLE:
framing or decoding outcome that closes the connection
  - NAME:
WIRE_PACKET_CONNECT
    ROLE:
type 1, the only packet allowed before CONNECT
  - NAME:
WIRE_PACKET_SUBSCRIBE
    ROLE:
type 8
  - NAME:
WIRE_PACKET_PUBLISH
    ROLE:
type 3
  - NAME:
WIRE_PACKET_PINGREQ
    ROLE:
type 12
  - NAME:
WIRE_PACKET_DISCONNECT
    ROLE:
type 14
  - NAME:
SESSION_AWAITING_CONNECT
    ROLE:
state in which only CONNECT is accepted
  - NAME:
SESSION_READY
    ROLE:
state in which SUBSCRIBE/PUBLISH/PINGREQ/DISCONNECT are accepted
  - NAME:
SESSION_CLOSED
    ROLE:
state reported to the caller as -1
  - NAME:
BROKER_CLOSE
    ROLE:
handler outcome that stops processing and reports -1

[GUARANTEE]
RAW:
int broker_process_input(struct broker *b, struct session *s)
NAME:
broker_process_input
RETURN:
int
PARAMS:
  - TYPE:
struct broker *
    NAME:
b
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
struct session *
    NAME:
s
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
b: broker registry handed to the handlers. s: the session whose receive buffer was filled by the execution layer; its state is SESSION_AWAITING_CONNECT before its first CONNECT and SESSION_READY afterwards.
  ACTION:
in = session_input(s); if in is NULL or b is NULL, close s and return -1. Then repeat: (a) st = wire_decode_header(in->data, in->len, &h). If st == WIRE_INCOMPLETE return 0: fewer bytes than the whole packet are buffered (or the Remaining Length field itself is unfinished), the partial frame stays buffered for the next read, and no handler is invoked. If st == WIRE_INVALID (reserved type 0/15 or a Remaining Length field longer than 4 bytes) close the session and return -1. (b) The frame occupies exactly h.total_len bytes = h.header_len + h.remaining_length, which is <= in->len because the header decode reported WIRE_COMPLETE. (c) Dispatch by state: if session_state(s) == SESSION_AWAITING_CONNECT, only h.type == WIRE_PACKET_CONNECT is legal; for any other type (including SUBSCRIBE, PUBLISH, PINGREQ, DISCONNECT and every server-only type) close the session immediately without a response and return -1, because CONNECT must be the first packet. For CONNECT decode with wire_decode_connect(in->data, h.total_len, &connect); a non-COMPLETE result closes the session and returns -1 (malformed CONNECT, no CONNACK); otherwise r = handle_connect(b, s, &connect). If session_state(s) == SESSION_READY, dispatch on h.type: WIRE_PACKET_SUBSCRIBE -> wire_decode_subscribe(in->data, h.total_len, &sub), r = handle_subscribe; WIRE_PACKET_PUBLISH -> wire_decode_publish(in->data, h.total_len, &pub), r = handle_publish; WIRE_PACKET_PINGREQ -> wire_decode_empty(in->data, h.total_len, WIRE_PACKET_PINGREQ, &consumed), r = handle_pingreq; WIRE_PACKET_DISCONNECT -> wire_decode_empty(in->data, h.total_len, WIRE_PACKET_DISCONNECT, &consumed), r = handle_disconnect; WIRE_PACKET_CONNECT (a second CONNECT on the same connection) closes the session immediately and returns -1; every other type (CONNACK, PUBACK/PUBREC/PUBREL/PUBCOMP, SUBACK, UNSUBSCRIBE/UNSUBACK, PINGRESP) is not accepted from a client by this broker and closes the session immediately without a response. A decoder result other than WIRE_COMPLETE closes the session and returns -1 before any handler runs, so no partially valid packet is ever acted upon. If session_state(s) is neither AWAITING_CONNECT nor READY (CLOSING or CLOSED) return -1 without consuming bytes: a session whose queue is draining must not process further input. (d) buffer_consume(in, h.total_len) drops exactly the handled frame, so the immediately following coalesced frame becomes the new head; the borrowed slices of the handled packet (client id, filters, topic, payload) are copied into session-owned storage by the handlers before this point, so no slice is used after the memmove. (e) If r == BROKER_CLOSE return -1 (the handler closed the session). Otherwise continue at (a) with the next frame. The loop is bounded by the buffered bytes, since every iteration either consumes a whole frame or returns.
  OUTPUT:
0 when every complete frame was handled and either the buffer is empty or it holds only an incomplete trailing frame (those bytes stay buffered); -1 when the session was closed as a result of its input - an invalid frame, a second CONNECT, a non-CONNECT packet before CONNECT, an unsupported server-only packet type, a malformed packet body, or a handler that closed the session (CONNECT rejection, unsupported QoS, DISCONNECT). The function never reaps the registry itself, so the borrowed session pointer stays valid for the caller and the execution layer decides when to call broker_reap.
  INVARIANTS_USED:
    - only wire_decode_header reports WIRE_INCOMPLETE; every other decoder sees one complete frame
    - the bytes consumed for a handled packet are exactly header_len + remaining_length, so coalesced frames are handled in order and never overlap
    - CONNECT is the first packet of a connection and is accepted exactly once per connection
    - malformed input closes only the offending connection; the broker continues serving other sessions
    - handler-visible slices are borrowed from the receive buffer and are copied before the frame is consumed
    - a CLOSING session processes no further input, so a rejected client cannot keep sending
    - no session state is inherited by a later connection
  PRECONDITION:
b and s are non-NULL and s is owned by b; the receive buffer holds stream bytes appended by the execution layer (possibly a partial frame).
  POSTCONDITION:
either s may continue (0, all complete frames handled, incomplete tail preserved and in->len < the size of one full frame) or s is SESSION_CLOSED and the return value is -1; in both cases b's registry count is unchanged because reaping is deferred to the caller.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Single-threaded reactor; the session and registry are not accessed concurrently.
