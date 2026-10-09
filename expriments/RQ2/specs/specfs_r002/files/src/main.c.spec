[PROMPT]
LANG:
C99
ROLE:
Program entry point: argument and <port> validation, signal-handler installation that only writes a volatile sig_atomic_t flag, creation of broker/listener/reactor, run, then ordered teardown that closes the listener and releases the broker and the loop.

[RELY]
DEPENDENCY:
  - src/broker.h
  - src/loop.h
  - src/net.h
  - src/session.h
SYSTEM_DEPENDENCY:
  - signal.h
  - stdio.h
  - stdlib.h
  - stddef.h
  - stdint.h

[GUARANTEE]


[SPECIFICATION]
SOURCE:
  PATH:
src/main.c
  DEPENDENCY:
    - src/broker.h
    - src/loop.h
    - src/net.h
    - src/session.h
  SYSTEM_DEPENDENCY:
    - signal.h
    - stdio.h
    - stdlib.h
    - stddef.h
    - stdint.h
  DATA:
    - NAME:
MAIN_BACKLOG
      KIND:
MACRO
      VISIBILITY:
PRIVATE
      ROLE:
listen() backlog passed to net_listen
      VALUE:
64
    - NAME:
g_stop
      KIND:
VAR
      VISIBILITY:
PRIVATE
      ROLE:
volatile sig_atomic_t flag written only by the signal handler and read by loop_run
      VALUE:
0
    - NAME:
g_stop_flag
      KIND:
VAR
      VISIBILITY:
PRIVATE
      ROLE:
Address published to loop_set_stop_flag; identical to g_stop
      VALUE:
&g_stop
  INTERFACE:
    - SIGNATURE:
static void on_signal(int signum)
      NAME:
on_signal
      KIND:
FUNC
      FUNCTION_TYPE:
EVENT_HANDLER
      ROLE:
Signal handler: stores 1 into the sig_atomic_t stop flag and does nothing else
      VISIBILITY:
private
    - SIGNATURE:
int main(int argc, char **argv)
      NAME:
main
      KIND:
FUNC
      FUNCTION_TYPE:
ENTRYPOINT
      ROLE:
Validate argv, ignore SIGPIPE, install handlers, run the broker loop, tear down in order
      VISIBILITY:
public
PUBLIC_SYMBOLS:
  - NAME:
main
    KIND:
FUNC
    SIGNATURE:
int main(int argc, char **argv)
    ROLE:
Program entry point
