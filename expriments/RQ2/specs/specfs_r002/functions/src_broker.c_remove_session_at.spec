[PROMPT]
Destroy the session held in registry slot index and shift the following slots down so the registry stays compact and every remaining owned session pointer is still reachable exactly once.

[RELY]
STRUCT:
  - NAME:
struct broker
    ROLE:
registry whose slot index is removed and whose tail is shifted down
  - NAME:
struct session
    ROLE:
owned session destroyed by this function
FUNC:
  - NAME:
session_destroy
    KIND:
CALL
    ROLE:
close the descriptor if open and free every session-owned resource, including any NULL slot
  - NAME:
memmove
    KIND:
CALL
    ROLE:
shift the higher slots one position down (regions overlap, so memmove is required)
VAR:
  - NAME:
struct broker
    ROLE:
b->count is decremented; b->sessions[index] is NULLed before the shift so a partially completed removal could never free the same session twice

[GUARANTEE]
RAW:
static void remove_session_at(struct broker *b, size_t index)
NAME:
remove_session_at
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
BORROWED
  - TYPE:
size_t
    NAME:
index
    NULLABLE:
false
    OWNERSHIP:
UNKNOWN

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
b: broker registry whose sessions array and count are valid. index: slot to remove; the caller guarantees index < b->count.
  ACTION:
1. s = b->sessions[index]. 2. b->sessions[index] = NULL so the slot cannot be mistaken for a live session even if the shift below is observed reentrantly. 3. session_destroy(s): it releases the descriptor (if still open), the receive and output buffers, the identifier copy and every subscription filter copy, then frees the session object. 4. b->count--. 5. memmove(&b->sessions[index], &b->sessions[index + 1], (b->count - index) * sizeof *b->sessions) moves every later slot down one position; when index == b->count (the removed session was last) the count is already decremented so zero bytes are moved, and b->sessions[old count - 1] is left holding the NULL written in step 2 rather than a duplicate pointer. The array capacity is not changed: the freed slot's storage is retained for the next broker_accept.
  OUTPUT:
void. The destroyed session's resources are fully released, b->count is one smaller, the slots after index hold the sessions that were previously one position higher, and the trailing slot contains NULL.
  INVARIANTS_USED:
    - the broker owns every session pointer in the array; removing one releases exactly its resources
    - the slots after index overlap the destination, so the shift must use memmove semantics
    - removing one session never changes another session's descriptor, queue or subscriptions
    - capacity is retained so a reconnecting client does not force a fresh array allocation
  PRECONDITION:
b is non-NULL and b->sessions is a valid array with b->count >= 1 and index < b->count; no caller holds a borrowed pointer to the removed session afterwards.
  POSTCONDITION:
b->count decreased by one, the removed session is destroyed and no registry slot references it, and the relative order of all other sessions is preserved.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Single-threaded reactor; the array is not iterated by any other frame while this runs.
