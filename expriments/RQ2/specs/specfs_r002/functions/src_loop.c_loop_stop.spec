[PROMPT]
Request that a running loop_run() return after the current event batch, without touching sessions, descriptors or queued output.

[RELY]
STRUCT:
  - NAME:
struct loop
    ROLE:
reactor whose internal stopped request is set
FUNC:

VAR:
  - NAME:
struct loop
    ROLE:
only the stopped field is written; the reactor, its broker and all sessions keep their ownership

[GUARANTEE]
RAW:
void loop_stop(struct loop *l)
NAME:
loop_stop
RETURN:
void
PARAMS:
  - TYPE:
struct loop *
    NAME:
l
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
l: reactor from loop_create() (NULL tolerated).
  ACTION:
1. If l == NULL return. 2. l->stopped = 1. No descriptor, buffer, session or registry is touched, so a stop request never truncates already queued output: loop_run() finishes dispatching the batch in progress, runs broker_reap() and mask_sync(), and only then observes the flag at the top of the next iteration and returns 0.
  OUTPUT:
void. A subsequent (or in-flight) loop_run() returns 0 after completing at most one more dispatch/reap/mask batch; sessions that still hold queued output stay registered for EPOLLOUT so the caller may choose to flush before tearing down.
  INVARIANTS_USED:
    - stopping is cooperative: the loop returns, and all cleanup (closing sessions, freeing buffers) is performed afterwards by ordinary code
    - a stop request must not discard queued output silently; it only ends the wait for more readiness
    - the loop object stays owned by the caller, which calls loop_destroy() afterwards
  PRECONDITION:
l is a live reactor or NULL.
  POSTCONDITION:
l->stopped == 1 (or nothing happened for a NULL argument); no other state changed.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Intended to be called from ordinary code in the same thread as loop_run(); setting an int flag is the only operation, so calling it from a signal handler would also be harmless but the design uses the sig_atomic_t flag for that.
