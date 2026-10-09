[PROMPT]
Asynchronous SIGINT/SIGTERM handler: records the shutdown request in a volatile sig_atomic_t flag and performs no other work, so all cleanup stays in ordinary code.

[RELY]
STRUCT:

FUNC:

VAR:
  - NAME:
g_stop
    ROLE:
the volatile sig_atomic_t object written to 1; its address is published to the reactor with loop_set_stop_flag
  - NAME:
SIGINT
    ROLE:
one of the two signals whose handler is this function
  - NAME:
SIGTERM
    ROLE:
the other of the two signals whose handler is this function

[GUARANTEE]
RAW:
static void on_signal(int signum)
NAME:
on_signal
RETURN:
void
PARAMS:
  - TYPE:
int
    NAME:
signum
    NULLABLE:
false
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
EVENT
EVENT:
  TRIGGER:
Delivery of SIGINT or SIGTERM, installed with sigaction() by main() before loop_run() begins.
  PRECONDITION:
The process is running the reactor loop; signal handlers for SIGINT and SIGTERM have been installed with on_signal as sa_handler and with the signal not blocked in the running thread. The handler does not depend on any argument value.
  INPUT:
signum: the delivered signal number (SIGINT or SIGTERM); it is not needed to decide anything because both request the same shutdown.
  ACTION:
1. Store the value 1 into the process-global volatile sig_atomic_t object g_stop (a single async-signal-safe assignment; the object is also published to the reactor via loop_set_stop_flag(&g_stop)). 2. Return immediately. No other statement is executed: no malloc/free/close/send/recv/printf, no errno inspection, no session, broker, loop or buffer access, no iteration over the registry, and no call to loop_stop(). (signum is unused; the parameter exists only to match the sa_handler signature.)
  STATE_CHANGE:
g_stop changes from 0 to 1 exactly once; no other state changes. The reactor observes the flag at the top of its next batch, loop_run() returns 0, and ordinary code in main() then performs the ordered teardown (loop_destroy, net_close_fd(&listener), broker_destroy).
  RESPONSE:
Interrupted blocking calls return with errno == EINTR: epoll_wait() reports EINTR and loop_run() continues its loop, re-checks the flag and returns 0; recv()/send() wrappers similarly retry, so at most the current batch of work is unfinished. Queued output is not discarded by the handler: a session whose output buffer still has bytes stays registered for EPOLLOUT, and main() tears down with broker_destroy() which closes every session descriptor (the process exit releases any unflushed bytes).
  EVENT_TYPE:
ASYNCHRONOUS_SIGNAL
  INVARIANTS_USED:
    - a signal handler may only store into a volatile sig_atomic_t object: all cleanup is performed by ordinary execution after loop_run() returns
    - the flag object outlives the handler because it is a static object in main()
    - no descriptor, buffer or registry object is touched asynchronously, so no data race or double-free can be introduced by a signal
  IDEMPOTENT:
true
  THREAD_SAFETY:
Async-signal-safe by construction: the single assignment is atomic with respect to the interrupted ordinary code for the volatile sig_atomic_t type, and no other object is read or written.
  POSTCONDITION:
g_stop == 1 and no other observable state has changed; the shutdown is completed by ordinary code, giving exit status 0.
