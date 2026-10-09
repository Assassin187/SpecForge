[PROMPT]
Adopt an accepted descriptor as a new session in the AWAITING_CONNECT state, transferring ownership of the descriptor to the broker only when the session is actually registered.

[RELY]
STRUCT:
  - NAME:
struct broker
    ROLE:
registry that receives the new session
  - NAME:
struct session
    ROLE:
new connection owning the descriptor after success
FUNC:
  - NAME:
add_session
    KIND:
CALL
    ROLE:
same-file: create and register the session
VAR:
  - NAME:
SESSION_AWAITING_CONNECT
    ROLE:
state of the new session; no protocol packet is processed until a CONNECT arrives

[GUARANTEE]
RAW:
int broker_accept(struct broker *b, int fd)
NAME:
broker_accept
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
int
    NAME:
fd
    NULLABLE:
false
    OWNERSHIP:
OWNED_BY_CALLER

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
b: broker registry (non-NULL in the ordinary path). fd: a descriptor already accepted as nonblocking and close-on-exec by net_accept; it is owned by the caller until this call succeeds.
  ACTION:
1. If b is NULL or fd < 0 return -1 without touching the descriptor. 2. rc = add_session(b, fd, NULL): it creates the session (which adopts fd), appends it to the registry and grows the slot array when needed. 3. Return rc unchanged.
  OUTPUT:
0 when the session is registered: the broker owns fd from now on, the session is in SESSION_AWAITING_CONNECT with an empty receive buffer, an empty output queue, no identifier and no subscriptions, and its interest mask is established by the execution layer (EPOLLIN only, no EPOLLOUT because nothing is queued). -1 when the session could not be created or registered: the registry is unchanged and the caller still owns fd, except in the case where the session had already adopted it, in which case add_session's rollback closed it.
  INVARIANTS_USED:
    - a newly accepted connection starts in AWAITING_CONNECT and can only become READY through a successful CONNECT
    - each accepted descriptor is owned by exactly one session
    - an accept failure never updates the listener: the execution layer keeps listening and closes the descriptor it still owns
  PRECONDITION:
b is a registry from broker_create; fd is a valid descriptor returned by net_accept and is not owned by any existing session.
  POSTCONDITION:
either the registry has one more session owning fd, or the registry is unchanged and the descriptor's ownership is unchanged from the caller's point of view.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Single-threaded reactor; called only from the listener dispatch path.
