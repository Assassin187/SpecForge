[PROMPT]
LANG:
C99
ROLE:
MQTT protocol core: connection registry, input framing loop, per-packet dispatch, CONNECT/CONNACK policy with client-id eviction, SUBSCRIBE/SUBACK policy with in-place filter replacement, QoS 0 publish fan-out with per-recipient enqueue isolation, EOF draining and session reaping.

[RELY]
DEPENDENCY:
  - src/broker.h
  - src/session.h
  - src/wire.h
  - src/topic.h
  - src/buffer.h
SYSTEM_DEPENDENCY:
  - stdlib.h
  - string.h
  - stddef.h
  - stdint.h

[GUARANTEE]
PATH:
src/broker.h
DEPENDENCY:
  - src/session.h
SYSTEM_DEPENDENCY:
  - stddef.h
DATA:
  - NAME:
enum broker_result
    KIND:
TYPE
    VISIBILITY:
PUBLIC
    ROLE:
Continue or close outcome of handling one packet
    TYPE_SPEC:
      TYPE_KIND:
ENUM
      ENUM_VALUES:
        - NAME:
BROKER_CONTINUE
          VALUE:
0
          ROLE:
Session stays usable
        - NAME:
BROKER_CLOSE
          VALUE:
1
          ROLE:
Session is or must be closed
  - NAME:
struct broker
    KIND:
TYPE
    VISIBILITY:
PUBLIC
    ROLE:
Opaque registry of active connections; definition is private to src/broker.c
    TYPE_SPEC:
      TYPE_KIND:
OPAQUE
INTERFACE:
  - SIGNATURE:
int broker_create(struct broker **out_broker)
    NAME:
broker_create
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Create an empty registry
    VISIBILITY:
public
  - SIGNATURE:
void broker_destroy(struct broker *b)
    NAME:
broker_destroy
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Destroy all sessions and the registry
    VISIBILITY:
public
  - SIGNATURE:
int broker_accept(struct broker *b, int fd)
    NAME:
broker_accept
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Adopt an accepted descriptor as a new session
    VISIBILITY:
public
  - SIGNATURE:
int broker_process_input(struct broker *b, struct session *s)
    NAME:
broker_process_input
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Consume all complete frames from the session input buffer
    VISIBILITY:
public
  - SIGNATURE:
void broker_session_eof(struct broker *b, struct session *s)
    NAME:
broker_session_eof
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Drain complete frames then close after flush
    VISIBILITY:
public
  - SIGNATURE:
void broker_reap(struct broker *b)
    NAME:
broker_reap
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Promote drained CLOSING sessions and destroy CLOSED ones
    VISIBILITY:
public
  - SIGNATURE:
size_t broker_session_count(const struct broker *b)
    NAME:
broker_session_count
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Registry iteration bound
    VISIBILITY:
public
  - SIGNATURE:
struct session *broker_session_at(const struct broker *b, size_t index)
    NAME:
broker_session_at
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Registry iteration accessor
    VISIBILITY:
public

[SPECIFICATION]
SOURCE:
  PATH:
src/broker.c
  DEPENDENCY:
    - src/broker.h
    - src/session.h
    - src/wire.h
    - src/topic.h
    - src/buffer.h
  SYSTEM_DEPENDENCY:
    - stdlib.h
    - string.h
    - stddef.h
    - stdint.h
  DATA:
    - NAME:
BROKER_INITIAL_SESSION_CAP
      KIND:
MACRO
      VISIBILITY:
PRIVATE
      ROLE:
Initial registry capacity in session slots
      VALUE:
8
    - NAME:
BROKER_INITIAL_SUB_CAP
      KIND:
MACRO
      VISIBILITY:
PRIVATE
      ROLE:
Initial subscription slots requested per session
      VALUE:
4
    - NAME:
CONNACK_UNACCEPTABLE_PROTOCOL_LEVEL
      KIND:
MACRO
      VISIBILITY:
PRIVATE
      ROLE:
CONNACK return code 0x01 sent when the protocol level is not 4
      VALUE:
0x01
    - NAME:
CONNACK_IDENTIFIER_REJECTED
      KIND:
MACRO
      VISIBILITY:
PRIVATE
      ROLE:
CONNACK return code 0x02 sent for an empty or non-ASCII client identifier
      VALUE:
0x02
    - NAME:
GRANTED_QOS
      KIND:
MACRO
      VISIBILITY:
PRIVATE
      ROLE:
Granted QoS reported in every SUBACK result code (this broker grants QoS 0)
      VALUE:
0
    - NAME:
struct broker
      KIND:
TYPE
      VISIBILITY:
PRIVATE
      ROLE:
Private registry definition: session pointer array plus a reusable encode scratch buffer
      TYPE_SPEC:
        TYPE_KIND:
STRUCT
        FIELDS:
          - NAME:
sessions
            TYPE:
struct session **
            ROLE:
Owned array of owned session pointers
          - NAME:
count
            TYPE:
size_t
            ROLE:
Live sessions
          - NAME:
cap
            TYPE:
size_t
            ROLE:
Allocated slots
          - NAME:
scratch
            TYPE:
struct byte_buf
            ROLE:
Reusable buffer holding one encoded outbound PUBLISH; cleared with buffer_consume after fan-out
  INTERFACE:
    - SIGNATURE:
static void remove_session_at(struct broker *b, size_t index)
      NAME:
remove_session_at
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Destroy the session at index and shift the tail down
      VISIBILITY:
private
    - SIGNATURE:
static int add_session(struct broker *b, int fd, struct session **out_session)
      NAME:
add_session
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Create a session and append it to the registry, growing the array if needed
      VISIBILITY:
private
    - SIGNATURE:
static enum broker_result handle_connect(struct broker *b, struct session *s, const struct wire_connect *c)
      NAME:
handle_connect
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Apply CONNECT policy, store the client id, send CONNACK, evict a same-id READY session
      VISIBILITY:
private
    - SIGNATURE:
static enum broker_result handle_subscribe(struct broker *b, struct session *s, const struct wire_subscribe *sub)
      NAME:
handle_subscribe
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Store each filter QoS 0 and queue the SUBACK with one granted code per filter in order
      VISIBILITY:
private
    - SIGNATURE:
static enum broker_result handle_publish(struct broker *b, struct session *s, const struct wire_publish *pub)
      NAME:
handle_publish
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Reject QoS 1/2 and DUP-with-QoS-0, otherwise fan out one QoS 0 copy
      VISIBILITY:
private
    - SIGNATURE:
static enum broker_result handle_pingreq(struct broker *b, struct session *s)
      NAME:
handle_pingreq
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Queue PINGRESP
      VISIBILITY:
private
    - SIGNATURE:
static enum broker_result handle_disconnect(struct broker *b, struct session *s)
      NAME:
handle_disconnect
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Close the connection immediately without flushing
      VISIBILITY:
private
    - SIGNATURE:
static void route_publish(struct broker *b, const struct wire_publish *pub)
      NAME:
route_publish
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Encode one QoS 0 PUBLISH into scratch and enqueue it to every READY matching session, including the publisher, closing only a failing recipient
      VISIBILITY:
private
    - SIGNATURE:
int broker_create(struct broker **out_broker)
      NAME:
broker_create
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Allocate the registry and its scratch buffer
      VISIBILITY:
public
    - SIGNATURE:
void broker_destroy(struct broker *b)
      NAME:
broker_destroy
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Destroy every session, free the arrays and the scratch buffer
      VISIBILITY:
public
    - SIGNATURE:
int broker_accept(struct broker *b, int fd)
      NAME:
broker_accept
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Take ownership of fd through add_session
      VISIBILITY:
public
    - SIGNATURE:
int broker_process_input(struct broker *b, struct session *s)
      NAME:
broker_process_input
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Frame, dispatch, consume; stop on INCOMPLETE, close on INVALID or non-CONNECT before CONNECT
      VISIBILITY:
public
    - SIGNATURE:
void broker_session_eof(struct broker *b, struct session *s)
      NAME:
broker_session_eof
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Process buffered complete frames, then enter CLOSING with the queue preserved
      VISIBILITY:
public
    - SIGNATURE:
void broker_reap(struct broker *b)
      NAME:
broker_reap
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Close drained CLOSING sessions and destroy CLOSED sessions
      VISIBILITY:
public
    - SIGNATURE:
size_t broker_session_count(const struct broker *b)
      NAME:
broker_session_count
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Return count (0 for NULL)
      VISIBILITY:
public
    - SIGNATURE:
struct session *broker_session_at(const struct broker *b, size_t index)
      NAME:
broker_session_at
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Borrowed session pointer or NULL when out of range
      VISIBILITY:
public
PUBLIC_SYMBOLS:
  - NAME:
enum broker_result
    KIND:
TYPE
    ROLE:
Packet handling outcome
  - NAME:
struct broker
    KIND:
TYPE
    ROLE:
Opaque registry
  - NAME:
broker_create
    KIND:
FUNC
    SIGNATURE:
int broker_create(struct broker **out_broker)
    ROLE:
Create registry
  - NAME:
broker_destroy
    KIND:
FUNC
    SIGNATURE:
void broker_destroy(struct broker *b)
    ROLE:
Destroy registry
  - NAME:
broker_accept
    KIND:
FUNC
    SIGNATURE:
int broker_accept(struct broker *b, int fd)
    ROLE:
Adopt descriptor
  - NAME:
broker_process_input
    KIND:
FUNC
    SIGNATURE:
int broker_process_input(struct broker *b, struct session *s)
    ROLE:
Framing pump
  - NAME:
broker_session_eof
    KIND:
FUNC
    SIGNATURE:
void broker_session_eof(struct broker *b, struct session *s)
    ROLE:
EOF handling
  - NAME:
broker_reap
    KIND:
FUNC
    SIGNATURE:
void broker_reap(struct broker *b)
    ROLE:
Reap finished sessions
  - NAME:
broker_session_count
    KIND:
FUNC
    SIGNATURE:
size_t broker_session_count(const struct broker *b)
    ROLE:
Session count
  - NAME:
broker_session_at
    KIND:
FUNC
    SIGNATURE:
struct session *broker_session_at(const struct broker *b, size_t index)
    ROLE:
Session accessor
