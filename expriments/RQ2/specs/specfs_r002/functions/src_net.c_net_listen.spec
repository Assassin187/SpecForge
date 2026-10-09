[PROMPT]
Create the listening TCP socket: SOCK_NONBLOCK | SOCK_CLOEXEC, SO_REUSEADDR, bind to INADDR_ANY on the requested port and listen, closing the descriptor on every failure path.

[RELY]
STRUCT:
  - NAME:
struct sockaddr_in
    ROLE:
IPv4 bind address with the requested port
FUNC:
  - NAME:
socket
    KIND:
TYPE_REF
    ROLE:
create the listening descriptor with SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC
  - NAME:
setsockopt
    KIND:
TYPE_REF
    ROLE:
enable SO_REUSEADDR so a restarting broker can rebind quickly
  - NAME:
bind
    KIND:
TYPE_REF
    ROLE:
reserve the local port on all interfaces
  - NAME:
listen
    KIND:
TYPE_REF
    ROLE:
mark the socket as accepting connections
  - NAME:
close
    KIND:
TYPE_REF
    ROLE:
release the descriptor on a failed setup
  - NAME:
htons
    KIND:
TYPE_REF
    ROLE:
convert the port to network byte order
  - NAME:
htonl
    KIND:
TYPE_REF
    ROLE:
convert INADDR_ANY (all local addresses) to network byte order
VAR:
  - NAME:
AF_INET
    ROLE:
IPv4 address family
  - NAME:
INADDR_ANY
    ROLE:
bind on every local address
  - NAME:
SO_REUSEADDR
    ROLE:
socket option enabled with value 1
  - NAME:
SOL_SOCKET
    ROLE:
option level for SO_REUSEADDR
  - NAME:
SOCK_STREAM
    ROLE:
TCP stream socket type, combined with the mode flags

[GUARANTEE]
RAW:
int net_listen(uint16_t port, int backlog)
NAME:
net_listen
RETURN:
int
PARAMS:
  - TYPE:
uint16_t
    NAME:
port
    NULLABLE:
false
    OWNERSHIP:
BORROWED
  - TYPE:
int
    NAME:
backlog
    NULLABLE:
false
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
port: TCP port in host byte order (the CLI passes 1..65535; 0 is allowed so tests can ask the kernel for an ephemeral port). backlog: listen backlog, normally 128.
  ACTION:
1) If backlog < 1 return -1 with errno = EINVAL (a listen backlog must be positive). 2) fd = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0); if fd < 0 return -1 with errno unchanged from socket. The mode flags are requested at creation time, so the listener is nonblocking and close-on-exec without any fcntl fallback. 3) int one = 1; if setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one) != 0 { saved = errno; close(fd); errno = saved; return -1; }. 4) memset(&addr, 0, sizeof addr); addr.sin_family = AF_INET; addr.sin_port = htons(port); addr.sin_addr.s_addr = htonl(INADDR_ANY); if bind(fd, (const struct sockaddr *)&addr, sizeof addr) != 0 { saved = errno; close(fd); errno = saved; return -1; }. 5) if listen(fd, backlog) != 0 { saved = errno; close(fd); errno = saved; return -1; }. 6) Return fd. The descriptor is returned open, nonblocking and close-on-exec; ownership transfers to the caller, which must close it with net_close_fd.
  OUTPUT:
A listening descriptor >= 0 on success; -1 with errno set (EINVAL for a nonpositive backlog, otherwise the errno of the failing call) on failure. No descriptor is leaked on any failure path: every error after a successful socket() closes the descriptor first while preserving errno. The listener's readiness mask is EPOLLIN only; it is registered by loop_add_listener.
  INVARIANTS_USED:
    - descriptor modes are established at creation time (SOCK_NONBLOCK | SOCK_CLOEXEC) and never depend on process umask or on a listener
    - the broker binds INADDR_ANY so any local address is reachable
    - SO_REUSEADDR avoids a TIME_WAIT bind failure on restart
    - the caller owns the returned descriptor and closes it exactly once
  PRECONDITION:
No other process holds the requested port (or SO_REUSEADDR makes it rebindable).
  POSTCONDITION:
On success one owned listening descriptor exists and no other resource; on failure no descriptor from this call remains open.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Safe to call from one thread; the returned descriptor is independent of other sockets.
