[PROMPT]
LANG:
C99
ROLE:
epoll level-triggered reactor: accepts peers, dispatches bounded read and write pumps under explicit readiness and state preconditions, re-synchronizes interest masks so queued output reaches idle recipients, reaps finished sessions and exits cleanly on a stop flag.

[RELY]
DEPENDENCY:
  - src/loop.h
  - src/broker.h
  - src/session.h
  - src/net.h
  - src/buffer.h
SYSTEM_DEPENDENCY:
  - sys/epoll.h
  - sys/socket.h
  - unistd.h
  - errno.h
  - stdio.h
  - stdint.h
  - stddef.h
  - signal.h
  - time.h
  - stdlib.h

[GUARANTEE]
PATH:
src/loop.h
DEPENDENCY:
  - src/broker.h
SYSTEM_DEPENDENCY:
  - signal.h
  - stddef.h
DATA:
  - NAME:
struct loop
    KIND:
TYPE
    VISIBILITY:
PUBLIC
    ROLE:
Opaque reactor instance; definition is private to src/loop.c
    TYPE_SPEC:
      TYPE_KIND:
OPAQUE
INTERFACE:
  - SIGNATURE:
int loop_create(struct broker *broker, struct loop **out_loop)
    NAME:
loop_create
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Create an epoll reactor bound to a broker
    VISIBILITY:
public
  - SIGNATURE:
int loop_add_listener(struct loop *l, int listener_fd)
    NAME:
loop_add_listener
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Register the listener for EPOLLIN with data pointer NULL
    VISIBILITY:
public
  - SIGNATURE:
void loop_set_stop_flag(struct loop *l, volatile sig_atomic_t *stop_flag)
    NAME:
loop_set_stop_flag
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Publish the signal-written stop flag
    VISIBILITY:
public
  - SIGNATURE:
int loop_run(struct loop *l)
    NAME:
loop_run
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Run the readiness loop until stopped or fatal
    VISIBILITY:
public
  - SIGNATURE:
void loop_stop(struct loop *l)
    NAME:
loop_stop
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Request loop_run to return
    VISIBILITY:
public
  - SIGNATURE:
void loop_destroy(struct loop *l)
    NAME:
loop_destroy
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Close the epoll descriptor and free the loop
    VISIBILITY:
public

[SPECIFICATION]
SOURCE:
  PATH:
src/loop.c
  DEPENDENCY:
    - src/loop.h
    - src/broker.h
    - src/session.h
    - src/net.h
    - src/buffer.h
  SYSTEM_DEPENDENCY:
    - sys/epoll.h
    - sys/socket.h
    - unistd.h
    - errno.h
    - stdio.h
    - stdint.h
    - stddef.h
    - signal.h
    - time.h
    - stdlib.h
  DATA:
    - NAME:
LOOP_MAX_EVENTS
      KIND:
MACRO
      VISIBILITY:
PRIVATE
      ROLE:
epoll_wait event array size
      VALUE:
64
    - NAME:
LOOP_READ_CHUNK
      KIND:
MACRO
      VISIBILITY:
PRIVATE
      ROLE:
stack staging chunk size for recv
      VALUE:
4096
    - NAME:
LOOP_MAX_READ_CALLS
      KIND:
MACRO
      VISIBILITY:
PRIVATE
      ROLE:
Bounded read calls per descriptor per dispatched event
      VALUE:
64
    - NAME:
LOOP_MAX_WRITE_CALLS
      KIND:
MACRO
      VISIBILITY:
PRIVATE
      ROLE:
Bounded write calls per descriptor per dispatched event
      VALUE:
64
    - NAME:
LOOP_ACCEPT_BURST
      KIND:
MACRO
      VISIBILITY:
PRIVATE
      ROLE:
Bounded accepted peers per listener event
      VALUE:
64
    - NAME:
LOOP_WAIT_MS
      KIND:
MACRO
      VISIBILITY:
PRIVATE
      ROLE:
epoll_wait timeout so the stop flag is observed without a signal
      VALUE:
100
    - NAME:
struct loop
      KIND:
TYPE
      VISIBILITY:
PRIVATE
      ROLE:
Private reactor state: epoll descriptor, borrowed broker, borrowed listener and stop flag
      TYPE_SPEC:
        TYPE_KIND:
STRUCT
        FIELDS:
          - NAME:
epfd
            TYPE:
int
            ROLE:
Owned epoll descriptor, -1 once closed
          - NAME:
broker
            TYPE:
struct broker *
            ROLE:
Borrowed protocol core
          - NAME:
listener_fd
            TYPE:
int
            ROLE:
Borrowed listener descriptor, -1 when unset
          - NAME:
stop_flag
            TYPE:
volatile sig_atomic_t *
            ROLE:
Optional signal-written flag; NULL when unset
          - NAME:
stopped
            TYPE:
int
            ROLE:
Internal stop request set by loop_stop
  INTERFACE:
    - SIGNATURE:
static int is_registered(const struct loop *l, const struct session *s)
      NAME:
is_registered
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Guard against dispatching an event for a session that was already reaped
      VISIBILITY:
private
    - SIGNATURE:
static void accept_ready(struct loop *l)
      NAME:
accept_ready
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Bounded accept loop adopting peers through broker_accept, keeping the listener on EMFILE
      VISIBILITY:
private
    - SIGNATURE:
static void pump_read(struct loop *l, struct session *s)
      NAME:
pump_read
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Bounded nonblocking reads into the session input buffer followed by broker_process_input
      VISIBILITY:
private
    - SIGNATURE:
static void pump_write(struct loop *l, struct session *s)
      NAME:
pump_write
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Bounded nonblocking writes draining the session output buffer
      VISIBILITY:
private
    - SIGNATURE:
static void dispatch_event(struct loop *l, struct session *s, uint32_t events)
      NAME:
dispatch_event
      KIND:
FUNC
      FUNCTION_TYPE:
EVENT_HANDLER
      ROLE:
Apply the readiness preconditions that select read and/or write pumping
      VISIBILITY:
private
    - SIGNATURE:
static void mask_sync(struct loop *l)
      NAME:
mask_sync
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Recompute and apply each session interest mask from its state and queued output
      VISIBILITY:
private
    - SIGNATURE:
int loop_create(struct broker *broker, struct loop **out_loop)
      NAME:
loop_create
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Allocate the loop and create the epoll descriptor
      VISIBILITY:
public
    - SIGNATURE:
int loop_add_listener(struct loop *l, int listener_fd)
      NAME:
loop_add_listener
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
epoll_ctl ADD of the listener for EPOLLIN with data pointer NULL
      VISIBILITY:
public
    - SIGNATURE:
void loop_set_stop_flag(struct loop *l, volatile sig_atomic_t *stop_flag)
      NAME:
loop_set_stop_flag
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Store the borrowed stop flag pointer
      VISIBILITY:
public
    - SIGNATURE:
int loop_run(struct loop *l)
      NAME:
loop_run
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
epoll_wait batch loop: dispatch events, reap, synchronize masks, honour the stop flag
      VISIBILITY:
public
    - SIGNATURE:
void loop_stop(struct loop *l)
      NAME:
loop_stop
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Set the internal stop request
      VISIBILITY:
public
    - SIGNATURE:
void loop_destroy(struct loop *l)
      NAME:
loop_destroy
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Close epfd if open and free the loop object
      VISIBILITY:
public
PUBLIC_SYMBOLS:
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
    SIGNATURE:
int loop_create(struct broker *broker, struct loop **out_loop)
    ROLE:
Create reactor
  - NAME:
loop_add_listener
    KIND:
FUNC
    SIGNATURE:
int loop_add_listener(struct loop *l, int listener_fd)
    ROLE:
Register listener
  - NAME:
loop_set_stop_flag
    KIND:
FUNC
    SIGNATURE:
void loop_set_stop_flag(struct loop *l, volatile sig_atomic_t *stop_flag)
    ROLE:
Set stop flag
  - NAME:
loop_run
    KIND:
FUNC
    SIGNATURE:
int loop_run(struct loop *l)
    ROLE:
Run reactor
  - NAME:
loop_stop
    KIND:
FUNC
    SIGNATURE:
void loop_stop(struct loop *l)
    ROLE:
Stop reactor
  - NAME:
loop_destroy
    KIND:
FUNC
    SIGNATURE:
void loop_destroy(struct loop *l)
    ROLE:
Destroy reactor
