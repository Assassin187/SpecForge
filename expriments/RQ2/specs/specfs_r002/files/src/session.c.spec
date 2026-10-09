[PROMPT]
LANG:
C99
ROLE:
Per-connection state layer: owns the accepted descriptor, the receive buffer, the queued output buffer, a copy of the client identifier and copies of all accepted subscription filters, with explicit and idempotent AWAITING_CONNECT/READY/CLOSING/CLOSED transitions.

[RELY]
DEPENDENCY:
  - src/session.h
  - src/buffer.h
  - src/net.h
  - src/topic.h
SYSTEM_DEPENDENCY:
  - stdlib.h
  - string.h
  - stdint.h
  - stddef.h

[GUARANTEE]
PATH:
src/session.h
DEPENDENCY:
  - src/buffer.h
SYSTEM_DEPENDENCY:
  - stddef.h
  - stdint.h
DATA:
  - NAME:
enum session_state
    KIND:
TYPE
    VISIBILITY:
PUBLIC
    ROLE:
Connection lifecycle state
    TYPE_SPEC:
      TYPE_KIND:
ENUM
      ENUM_VALUES:
        - NAME:
SESSION_AWAITING_CONNECT
          VALUE:
0
          ROLE:
Created, no CONNECT processed yet
        - NAME:
SESSION_READY
          VALUE:
1
          ROLE:
CONNECT accepted, responses may be queued
        - NAME:
SESSION_CLOSING
          VALUE:
2
          ROLE:
No more input; queued output must flush
        - NAME:
SESSION_CLOSED
          VALUE:
3
          ROLE:
Terminal, descriptor already closed
  - NAME:
struct session_sub
    KIND:
TYPE
    VISIBILITY:
PUBLIC
    ROLE:
Read-only view of one stored subscription entry
    TYPE_SPEC:
      TYPE_KIND:
STRUCT
      FIELDS:
        - NAME:
filter
          TYPE:
const char *
          ROLE:
Session-owned filter bytes, not NUL terminated
        - NAME:
filter_len
          TYPE:
size_t
          ROLE:
Filter length in bytes
        - NAME:
qos
          TYPE:
uint8_t
          ROLE:
Granted QoS 0..2
  - NAME:
struct session
    KIND:
TYPE
    VISIBILITY:
PUBLIC
    ROLE:
Opaque per-connection object; definition is private to src/session.c
    TYPE_SPEC:
      TYPE_KIND:
OPAQUE
INTERFACE:
  - SIGNATURE:
int session_create(int fd, struct session **out_session)
    NAME:
session_create
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Create a session adopting fd
    VISIBILITY:
public
  - SIGNATURE:
void session_destroy(struct session *s)
    NAME:
session_destroy
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Release descriptor, buffers, identifier and subscriptions
    VISIBILITY:
public
  - SIGNATURE:
int session_fd(const struct session *s)
    NAME:
session_fd
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Descriptor getter
    VISIBILITY:
public
  - SIGNATURE:
enum session_state session_state(const struct session *s)
    NAME:
session_state
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Lifecycle state getter
    VISIBILITY:
public
  - SIGNATURE:
void session_mark_ready(struct session *s)
    NAME:
session_mark_ready
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
AWAITING_CONNECT to READY
    VISIBILITY:
public
  - SIGNATURE:
void session_close_immediately(struct session *s)
    NAME:
session_close_immediately
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Drop output, close descriptor, enter CLOSED
    VISIBILITY:
public
  - SIGNATURE:
void session_close_after_flush(struct session *s)
    NAME:
session_close_after_flush
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Enter CLOSING keeping queued output
    VISIBILITY:
public
  - SIGNATURE:
struct byte_buf *session_input(struct session *s)
    NAME:
session_input
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Borrowed receive buffer
    VISIBILITY:
public
  - SIGNATURE:
struct byte_buf *session_output(struct session *s)
    NAME:
session_output
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Borrowed queued-output buffer
    VISIBILITY:
public
  - SIGNATURE:
int session_enqueue(struct session *s, const void *data, size_t len)
    NAME:
session_enqueue
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Append a complete response to queued output
    VISIBILITY:
public
  - SIGNATURE:
int session_set_client_id(struct session *s, const char *id, size_t id_len)
    NAME:
session_set_client_id
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Replace the owned client identifier copy
    VISIBILITY:
public
  - SIGNATURE:
const char *session_client_id(const struct session *s, size_t *out_len)
    NAME:
session_client_id
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Read the owned client identifier
    VISIBILITY:
public
  - SIGNATURE:
int session_add_subscription(struct session *s, const char *filter, size_t filter_len, uint8_t qos)
    NAME:
session_add_subscription
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Copy a filter, replacing an identical one in place
    VISIBILITY:
public
  - SIGNATURE:
size_t session_subscription_count(const struct session *s)
    NAME:
session_subscription_count
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Subscription count getter
    VISIBILITY:
public
  - SIGNATURE:
const struct session_sub *session_subscription_at(const struct session *s, size_t index)
    NAME:
session_subscription_at
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Subscription accessor
    VISIBILITY:
public
  - SIGNATURE:
int session_matches_topic(const struct session *s, const char *topic, size_t topic_len)
    NAME:
session_matches_topic
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Any stored filter matches the topic name
    VISIBILITY:
public

[SPECIFICATION]
SOURCE:
  PATH:
src/session.c
  DEPENDENCY:
    - src/session.h
    - src/buffer.h
    - src/net.h
    - src/topic.h
  SYSTEM_DEPENDENCY:
    - stdlib.h
    - string.h
    - stdint.h
    - stddef.h
  DATA:
    - NAME:
struct session_sub_entry
      KIND:
TYPE
      VISIBILITY:
PRIVATE
      ROLE:
Private subscription slot: public read-only view plus the owned filter copy it points at
      TYPE_SPEC:
        TYPE_KIND:
STRUCT
        FIELDS:
          - NAME:
view
            TYPE:
struct session_sub
            ROLE:
Read-only projection returned by session_subscription_at; view.filter points at the owned copy
          - NAME:
owned
            TYPE:
char *
            ROLE:
Owned filter copy, not NUL terminated, released by free_subscriptions
    - NAME:
struct session
      KIND:
TYPE
      VISIBILITY:
PRIVATE
      ROLE:
Private definition of the opaque public object
      TYPE_SPEC:
        TYPE_KIND:
STRUCT
        FIELDS:
          - NAME:
fd
            TYPE:
int
            ROLE:
Owned descriptor, -1 once closed
          - NAME:
state
            TYPE:
enum session_state
            ROLE:
Lifecycle state
          - NAME:
input
            TYPE:
struct byte_buf
            ROLE:
Unconsumed receive bytes
          - NAME:
output
            TYPE:
struct byte_buf
            ROLE:
Complete encoded packets awaiting write
          - NAME:
client_id
            TYPE:
char *
            ROLE:
Owned client identifier copy, not NUL terminated
          - NAME:
client_id_len
            TYPE:
size_t
            ROLE:
Client identifier length in bytes
          - NAME:
subs
            TYPE:
struct session_sub_entry *
            ROLE:
Owned subscription array
          - NAME:
sub_count
            TYPE:
size_t
            ROLE:
Stored subscriptions
          - NAME:
sub_cap
            TYPE:
size_t
            ROLE:
Allocated subscription slots
    - NAME:
SESSION_READ_CHUNK
      KIND:
MACRO
      VISIBILITY:
PRIVATE
      ROLE:
Receive chunk size used by the reactor read pump
      VALUE:
4096
    - NAME:
SESSION_INITIAL_SUB_CAP
      KIND:
MACRO
      VISIBILITY:
PRIVATE
      ROLE:
Slot count allocated for the first stored subscription
      VALUE:
4
  INTERFACE:
    - SIGNATURE:
static void free_subscriptions(struct session *s)
      NAME:
free_subscriptions
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Release every owned filter copy and the slot array, leaving count/cap zero
      VISIBILITY:
private
    - SIGNATURE:
static void release_fd(struct session *s)
      NAME:
release_fd
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Close the owned descriptor exactly once via net_close_fd
      VISIBILITY:
private
    - SIGNATURE:
int session_create(int fd, struct session **out_session)
      NAME:
session_create
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Allocate, zero-init buffers, adopt fd, start AWAITING_CONNECT
      VISIBILITY:
public
    - SIGNATURE:
void session_destroy(struct session *s)
      NAME:
session_destroy
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Close fd if still open, free buffers, identifier and subscriptions, then free the object
      VISIBILITY:
public
    - SIGNATURE:
int session_fd(const struct session *s)
      NAME:
session_fd
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Return fd (or -1 for NULL)
      VISIBILITY:
public
    - SIGNATURE:
enum session_state session_state(const struct session *s)
      NAME:
session_state
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Return state (CLOSED for NULL)
      VISIBILITY:
public
    - SIGNATURE:
void session_mark_ready(struct session *s)
      NAME:
session_mark_ready
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Only AWAITING_CONNECT becomes READY
      VISIBILITY:
public
    - SIGNATURE:
void session_close_immediately(struct session *s)
      NAME:
session_close_immediately
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Idempotent teardown: discard queued output, close fd, state CLOSED
      VISIBILITY:
public
    - SIGNATURE:
void session_close_after_flush(struct session *s)
      NAME:
session_close_after_flush
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
READY/AWAITING_CONNECT to CLOSING, output preserved
      VISIBILITY:
public
    - SIGNATURE:
struct byte_buf *session_input(struct session *s)
      NAME:
session_input
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Borrow the receive buffer
      VISIBILITY:
public
    - SIGNATURE:
struct byte_buf *session_output(struct session *s)
      NAME:
session_output
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Borrow the queued-output buffer
      VISIBILITY:
public
    - SIGNATURE:
int session_enqueue(struct session *s, const void *data, size_t len)
      NAME:
session_enqueue
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
buffer_append into output with all-or-nothing failure semantics
      VISIBILITY:
public
    - SIGNATURE:
int session_set_client_id(struct session *s, const char *id, size_t id_len)
      NAME:
session_set_client_id
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Allocate a copy, then release the previous copy and adopt the new one
      VISIBILITY:
public
    - SIGNATURE:
const char *session_client_id(const struct session *s, size_t *out_len)
      NAME:
session_client_id
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Borrow the stored identifier and its length
      VISIBILITY:
public
    - SIGNATURE:
int session_add_subscription(struct session *s, const char *filter, size_t filter_len, uint8_t qos)
      NAME:
session_add_subscription
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Replace identical filter in place or append a new copy, growing the array as needed
      VISIBILITY:
public
    - SIGNATURE:
size_t session_subscription_count(const struct session *s)
      NAME:
session_subscription_count
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Return sub_count (0 for NULL)
      VISIBILITY:
public
    - SIGNATURE:
const struct session_sub *session_subscription_at(const struct session *s, size_t index)
      NAME:
session_subscription_at
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Project the private entry into the public read-only view; NULL when out of range
      VISIBILITY:
public
    - SIGNATURE:
int session_matches_topic(const struct session *s, const char *topic, size_t topic_len)
      NAME:
session_matches_topic
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Linear scan calling topic_filter_matches, returns 1 on first match
      VISIBILITY:
public
PUBLIC_SYMBOLS:
  - NAME:
enum session_state
    KIND:
TYPE
    ROLE:
Lifecycle state
  - NAME:
struct session_sub
    KIND:
TYPE
    ROLE:
Read-only subscription view
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
    SIGNATURE:
int session_create(int fd, struct session **out_session)
    ROLE:
Create session
  - NAME:
session_destroy
    KIND:
FUNC
    SIGNATURE:
void session_destroy(struct session *s)
    ROLE:
Destroy session
  - NAME:
session_fd
    KIND:
FUNC
    SIGNATURE:
int session_fd(const struct session *s)
    ROLE:
Descriptor getter
  - NAME:
session_state
    KIND:
FUNC
    SIGNATURE:
enum session_state session_state(const struct session *s)
    ROLE:
State getter
  - NAME:
session_mark_ready
    KIND:
FUNC
    SIGNATURE:
void session_mark_ready(struct session *s)
    ROLE:
Become READY
  - NAME:
session_close_immediately
    KIND:
FUNC
    SIGNATURE:
void session_close_immediately(struct session *s)
    ROLE:
Immediate close
  - NAME:
session_close_after_flush
    KIND:
FUNC
    SIGNATURE:
void session_close_after_flush(struct session *s)
    ROLE:
Deferred close
  - NAME:
session_input
    KIND:
FUNC
    SIGNATURE:
struct byte_buf *session_input(struct session *s)
    ROLE:
Receive buffer
  - NAME:
session_output
    KIND:
FUNC
    SIGNATURE:
struct byte_buf *session_output(struct session *s)
    ROLE:
Output buffer
  - NAME:
session_enqueue
    KIND:
FUNC
    SIGNATURE:
int session_enqueue(struct session *s, const void *data, size_t len)
    ROLE:
Queue bytes
  - NAME:
session_set_client_id
    KIND:
FUNC
    SIGNATURE:
int session_set_client_id(struct session *s, const char *id, size_t id_len)
    ROLE:
Store client id
  - NAME:
session_client_id
    KIND:
FUNC
    SIGNATURE:
const char *session_client_id(const struct session *s, size_t *out_len)
    ROLE:
Read client id
  - NAME:
session_add_subscription
    KIND:
FUNC
    SIGNATURE:
int session_add_subscription(struct session *s, const char *filter, size_t filter_len, uint8_t qos)
    ROLE:
Add/replace subscription
  - NAME:
session_subscription_count
    KIND:
FUNC
    SIGNATURE:
size_t session_subscription_count(const struct session *s)
    ROLE:
Subscription count
  - NAME:
session_subscription_at
    KIND:
FUNC
    SIGNATURE:
const struct session_sub *session_subscription_at(const struct session *s, size_t index)
    ROLE:
Subscription accessor
  - NAME:
session_matches_topic
    KIND:
FUNC
    SIGNATURE:
int session_matches_topic(const struct session *s, const char *topic, size_t topic_len)
    ROLE:
Subscription match
