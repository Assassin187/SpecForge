[PROMPT]
Program entry point: validate the single <port> argument, ignore SIGPIPE, install the SIGINT/SIGTERM handler that only sets the stop flag, create the registry, the listening socket and the reactor, run the reactor, then tear down in the documented order and return the process exit status.

[RELY]
STRUCT:
  - NAME:
struct broker
    ROLE:
registry created first and destroyed last
  - NAME:
struct loop
    ROLE:
reactor created after the registry and destroyed first
  - NAME:
struct sigaction
    ROLE:
handler installation for SIGINT and SIGTERM
  - NAME:
struct session
    ROLE:
not used directly; sessions are created by broker_accept through the reactor
FUNC:
  - NAME:
net_parse_port
    KIND:
CALL
    ROLE:
turn argv[1] into a port in 1..65535
  - NAME:
signal
    KIND:
CALL
    ROLE:
ignore SIGPIPE so a write to a closed peer returns EPIPE instead of killing the process
  - NAME:
sigaction
    KIND:
CALL
    ROLE:
install on_signal for SIGINT and SIGTERM without SA_RESTART
  - NAME:
broker_create
    KIND:
CALL
    ROLE:
create the session registry
  - NAME:
net_listen
    KIND:
CALL
    ROLE:
create the nonblocking listening socket bound to INADDR_ANY:<port>
  - NAME:
loop_create
    KIND:
CALL
    ROLE:
create the reactor bound to the registry
  - NAME:
loop_add_listener
    KIND:
CALL
    ROLE:
register the listener for EPOLLIN with the NULL tag
  - NAME:
loop_set_stop_flag
    KIND:
CALL
    ROLE:
publish &g_stop, the object on_signal writes
  - NAME:
loop_run
    KIND:
CALL
    ROLE:
serve clients until signalled or fatally interrupted
  - NAME:
loop_destroy
    KIND:
CALL
    ROLE:
close the epoll descriptor and free the reactor
  - NAME:
net_close_fd
    KIND:
CALL
    ROLE:
release the listener exactly once
  - NAME:
broker_destroy
    KIND:
CALL
    ROLE:
destroy every remaining session and free the registry
VAR:
  - NAME:
MAIN_BACKLOG
    ROLE:
listen() backlog value, 64
  - NAME:
g_stop
    ROLE:
volatile sig_atomic_t flag written by on_signal and observed by loop_run
  - NAME:
SIGPIPE
    ROLE:
signal ignored at startup
  - NAME:
SIGINT
    ROLE:
shutdown signal handled by on_signal
  - NAME:
SIGTERM
    ROLE:
shutdown signal handled by on_signal
  - NAME:
EXIT_SUCCESS
    ROLE:
status returned after a clean stop

[GUARANTEE]
RAW:
int main(int argc, char **argv)
NAME:
main
RETURN:
int
PARAMS:
  - TYPE:
int
    NAME:
argc
    NULLABLE:
false
    OWNERSHIP:
BORROWED
  - TYPE:
char **
    NAME:
argv
    NULLABLE:
false
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ENTRYPOINT
LOGIC:
  INPUT:
argc/argv from the shell: exactly one positional argument, the decimal TCP port to listen on.
  ACTION:
1. If argc != 2: write a usage line naming the program and the <port> argument to stderr and return 1 (no socket is created). 2. If net_parse_port(argv[1], &port) != 0: write the same usage line to stderr and return 1. 3. signal(SIGPIPE, SIG_IGN) so that net_write_some() to a peer that has gone away yields EPIPE instead of terminating the process. 4. Install the shutdown handler: struct sigaction sa; memset zero; sa.sa_handler = on_signal; sigemptyset(&sa.sa_mask); sa.sa_flags = 0 (deliberately WITHOUT SA_RESTART, so a blocking epoll_wait/recv is interrupted with EINTR and loop_run observes the flag); sigaction(SIGINT, &sa, NULL) and sigaction(SIGTERM, &sa, NULL); a failure of sigaction is fatal (return 1) because the broker could then be killed without releasing the port. 5. g_stop = 0. 6. broker_create(&b); if it fails return 1 (nothing else is owned yet). 7. listener = net_listen(port, MAIN_BACKLOG); if listener < 0 { broker_destroy(b); return 1; }. 8. loop_create(b, &l); if it fails { net_close_fd(&listener); broker_destroy(b); return 1; }. 9. loop_add_listener(l, listener); if it fails { loop_destroy(l); net_close_fd(&listener); broker_destroy(b); return 1; }. 10. loop_set_stop_flag(l, &g_stop). 11. rc = loop_run(l): this is the whole service lifetime; sessions are accepted, decoded, routed, flushed and reaped inside it. 12. Teardown in the documented order: loop_destroy(l) (epoll descriptor closed; no further dispatch possible), net_close_fd(&listener) (port released; further connects are refused), broker_destroy(b) (every remaining session descriptor closed exactly once, buffers, identifiers and filter copies freed, registry freed). 13. Return rc == 0 ? 0 : 1.
  OUTPUT:
Exit status 0 after a clean shutdown triggered by SIGINT/SIGTERM (loop_run returned 0) with the listening port released. Exit status 1 for a usage or port error, for a failed sigaction, and for any fatal resource failure during startup or a fatal reactor error; in every failure path the resources acquired so far are released before returning.
  INVARIANTS_USED:
    - the program takes exactly one argument, the port; no other configuration exists
    - signal handlers only store into the sig_atomic_t flag and ordinary code performs all cleanup
    - teardown order is reactor, then listener, then registry so nothing can dispatch into freed state
    - every descriptor has exactly one owner: the listener by main, session descriptors by their session, the epoll descriptor by the loop
    - a client's failure never affects the process: broken peers are closed by broker_reap through the reactor
  PRECONDITION:
The process starts with no broker state and one optional command-line argument.
  POSTCONDITION:
The process has exited with no open listening socket, no owned memory and no live session; all cleanup was performed by ordinary code, never by a handler.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Single-threaded; only a signal handler writes the stop flag asynchronously.
