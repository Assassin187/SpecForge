[PROMPT]
Allocate an empty broker registry, its initial session slot array and its reusable encode scratch buffer, reporting success only when every owned allocation exists.

[RELY]
STRUCT:
  - NAME:
struct broker
    ROLE:
registry object being created: sessions array, count, capacity and scratch buffer
  - NAME:
struct byte_buf
    ROLE:
scratch buffer initialised empty so route_publish can reuse it
FUNC:
  - NAME:
calloc
    KIND:
CALL
    ROLE:
allocate the zeroed registry object
  - NAME:
free
    KIND:
CALL
    ROLE:
release partial allocations on the failure paths
  - NAME:
buffer_init
    KIND:
CALL
    ROLE:
initialise the scratch buffer to empty with no storage
VAR:
  - NAME:
BROKER_INITIAL_SESSION_CAP
    ROLE:
number of session slots allocated up front
  - NAME:
struct broker
    ROLE:
out_broker receives the only owning pointer to the new registry

[GUARANTEE]
RAW:
int broker_create(struct broker **out_broker)
NAME:
broker_create
RETURN:
int
PARAMS:
  - TYPE:
struct broker **
    NAME:
out_broker
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
out_broker: caller-provided output slot for the single owning registry pointer.
  ACTION:
1. If out_broker is NULL return -1. 2. *out_broker = NULL so a caller ignoring the result cannot use an indeterminate pointer. 3. b = calloc(1, sizeof *b); if b is NULL return -1. 4. b->sessions = malloc(BROKER_INITIAL_SESSION_CAP * sizeof *b->sessions); if it is NULL, free(b) and return -1 (no partial object escapes). 5. b->count = 0; b->cap = BROKER_INITIAL_SESSION_CAP. 6. buffer_init(&b->scratch) so the scratch buffer starts empty and its storage is allocated lazily by the first route_publish. 7. *out_broker = b; return 0.
  OUTPUT:
0 with *out_broker pointing to an empty registry (count 0, capacity BROKER_INITIAL_SESSION_CAP, empty scratch, no I/O descriptors). -1 with *out_broker == NULL when out_broker is NULL or an allocation failed; all memory allocated by the failing attempt has been freed, so the caller has nothing to clean up.
  INVARIANTS_USED:
    - the registry starts with no sessions and no descriptors of its own; the execution layer supplies the listener
    - every allocation made here is reachable through out_broker on success and freed on failure
    - the scratch buffer is broker-owned and reusable, so fan-out does not allocate a buffer per publish
  PRECONDITION:
out_broker points to writable storage of type struct broker *.
  POSTCONDITION:
either the caller owns a fully initialised empty registry, or no registry exists and no allocation was leaked.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Called once before the reactor starts; no concurrent access.
