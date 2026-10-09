[PROMPT]
Terminate the broker: destroy every session still in the registry (releasing descriptors, buffers, identifier copies and subscriptions), then release the scratch buffer, the slot array and the registry object.

[RELY]
STRUCT:
  - NAME:
struct broker
    ROLE:
registry being destroyed with all of its owned sessions
  - NAME:
struct session
    ROLE:
session released through session_destroy
  - NAME:
struct byte_buf
    ROLE:
broker-owned scratch buffer released through buffer_free
FUNC:
  - NAME:
session_destroy
    KIND:
CALL
    ROLE:
release one session, tolerating a NULL slot
  - NAME:
buffer_free
    KIND:
CALL
    ROLE:
release the scratch storage and reset it to empty
  - NAME:
free
    KIND:
CALL
    ROLE:
release the slot array and the registry object
VAR:
  - NAME:
GRANTED_QOS
    ROLE:
unused here; kept only as documentation that no subscription state outlives the broker

[GUARANTEE]
RAW:
void broker_destroy(struct broker *b)
NAME:
broker_destroy
RETURN:
void
PARAMS:
  - TYPE:
struct broker *
    NAME:
b
    NULLABLE:
true
    OWNERSHIP:
TRANSFER

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
b: the registry to destroy, whose ownership passes to this function; NULL is allowed and ignored (the ordinary path when broker_create failed).
  ACTION:
1. If b is NULL return. 2. For i = 0 .. b->count - 1 call session_destroy(b->sessions[i]): this closes each still-open descriptor exactly once (unflushed queued output is dropped, which is the documented shutdown behaviour) and frees every filter copy, so no subscription survives the broker. Slots already NULL (a session removed by remove_session_at) are ignored. 3. buffer_free(&b->scratch) releases the retained encode storage. 4. free(b->sessions) releases the slot array. 5. free(b) releases the registry object. The listener descriptor is not owned by the broker and is closed by the caller.
  OUTPUT:
void. Every descriptor owned by a session is closed, every heap block reachable from b is released and the pointer b must not be used again. Other resources (the listener) remain untouched and are the caller's responsibility.
  INVARIANTS_USED:
    - the broker owns each session pointer exactly once, so destroying the registry frees each session once
    - shutdown drops unflushed queued output rather than writing it
    - the listener is not owned by the broker and is closed by main
    - termination on SIGINT/SIGTERM runs this function through the ordinary (non-signal) path so no allocation is performed inside a handler
  PRECONDITION:
b is NULL or a registry previously returned by broker_create whose slots each hold either NULL or a live owned session.
  POSTCONDITION:
no session, no scratch storage and no registry object from this broker remains allocated; no session may be referenced afterwards, including by borrowed pointers returned from broker_session_at.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Called after the reactor loop has stopped; no other thread is running.
