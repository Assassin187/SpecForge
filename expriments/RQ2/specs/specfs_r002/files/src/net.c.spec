[PROMPT]
LANG:
C99
ROLE:
POSIX TCP helper layer: strict <port> parsing, nonblocking close-on-exec listener creation, accept() plus explicit fcntl mode setup for every accepted descriptormode for every peer, EINTR-safe partial read/write helpers and an idempotent close.

[RELY]
DEPENDENCY:
  - src/net.h
SYSTEM_DEPENDENCY:
  - sys/types.h
  - sys/socket.h
  - netinet/in.h
  - arpa/inet.h
  - unistd.h
  - fcntl.h
  - errno.h
  - stddef.h
  - stdint.h
  - string.h

[GUARANTEE]
PATH:
src/net.h
DEPENDENCY:

SYSTEM_DEPENDENCY:
  - stddef.h
  - stdint.h
DATA:
  - NAME:
enum net_io_result
    KIND:
TYPE
    VISIBILITY:
PUBLIC
    ROLE:
Outcome of one nonblocking read/write attempt
    TYPE_SPEC:
      TYPE_KIND:
ENUM
      ENUM_VALUES:
        - NAME:
NET_IO_PROGRESS
          VALUE:
1
          ROLE:
Bytes were transferred
        - NAME:
NET_IO_WOULD_BLOCK
          VALUE:
0
          ROLE:
No progress possible right now
        - NAME:
NET_IO_CLOSED
          VALUE:
-1
          ROLE:
Read only: orderly peer close
        - NAME:
NET_IO_ERROR
          VALUE:
-2
          ROLE:
Fatal error on this descriptor
INTERFACE:
  - SIGNATURE:
int net_parse_port(const char *text, uint16_t *out_port)
    NAME:
net_parse_port
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Parse the <port> argv argument
    VISIBILITY:
public
  - SIGNATURE:
int net_listen(uint16_t port, int backlog)
    NAME:
net_listen
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Create a nonblocking close-on-exec TCP listener
    VISIBILITY:
public
  - SIGNATURE:
int net_accept(int listener_fd, int *out_fd)
    NAME:
net_accept
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Accept a nonblocking close-on-exec peer descriptor
    VISIBILITY:
public
  - SIGNATURE:
enum net_io_result net_read_some(int fd, uint8_t *buf, size_t cap, size_t *out_n)
    NAME:
net_read_some
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
One bounded nonblocking read
    VISIBILITY:
public
  - SIGNATURE:
enum net_io_result net_write_some(int fd, const uint8_t *buf, size_t len, size_t *out_n)
    NAME:
net_write_some
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
One bounded nonblocking write without SIGPIPE
    VISIBILITY:
public
  - SIGNATURE:
void net_close_fd(int *fd)
    NAME:
net_close_fd
    KIND:
FUNC
    FUNCTION_TYPE:
ALGORITHM
    ROLE:
Idempotent descriptor close
    VISIBILITY:
public

[SPECIFICATION]
SOURCE:
  PATH:
src/net.c
  DEPENDENCY:
    - src/net.h
  SYSTEM_DEPENDENCY:
    - sys/types.h
    - sys/socket.h
    - netinet/in.h
    - arpa/inet.h
    - unistd.h
    - fcntl.h
    - errno.h
    - stddef.h
    - stdint.h
    - string.h
  DATA:

  INTERFACE:
    - SIGNATURE:
int net_parse_port(const char *text, uint16_t *out_port)
      NAME:
net_parse_port
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
Decimal-only, whitespace-free, overflow-checked parse into 1..65535
      VISIBILITY:
public
    - SIGNATURE:
int net_listen(uint16_t port, int backlog)
      NAME:
net_listen
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
socket with SOCK_NONBLOCK|SOCK_CLOEXEC, SO_REUSEADDR, bind INADDR_ANY, listen
      VISIBILITY:
public
    - SIGNATURE:
int net_accept(int listener_fd, int *out_fd)
      NAME:
net_accept
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
accept() then explicit fcntl O_NONBLOCK|FD_CLOEXEC with explicit would-block mapping
      VISIBILITY:
public
    - SIGNATURE:
enum net_io_result net_read_some(int fd, uint8_t *buf, size_t cap, size_t *out_n)
      NAME:
net_read_some
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
recv loop honouring EINTR, mapping 0 bytes to NET_IO_CLOSED
      VISIBILITY:
public
    - SIGNATURE:
enum net_io_result net_write_some(int fd, const uint8_t *buf, size_t len, size_t *out_n)
      NAME:
net_write_some
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
send with MSG_NOSIGNAL honouring EINTR and partial writes
      VISIBILITY:
public
    - SIGNATURE:
void net_close_fd(int *fd)
      NAME:
net_close_fd
      KIND:
FUNC
      FUNCTION_TYPE:
ALGORITHM
      ROLE:
close(*fd) when *fd >= 0 then set *fd = -1
      VISIBILITY:
public
PUBLIC_SYMBOLS:
  - NAME:
enum net_io_result
    KIND:
TYPE
    ROLE:
Read/write outcome
  - NAME:
net_parse_port
    KIND:
FUNC
    SIGNATURE:
int net_parse_port(const char *text, uint16_t *out_port)
    ROLE:
Parse port
  - NAME:
net_listen
    KIND:
FUNC
    SIGNATURE:
int net_listen(uint16_t port, int backlog)
    ROLE:
Create listener
  - NAME:
net_accept
    KIND:
FUNC
    SIGNATURE:
int net_accept(int listener_fd, int *out_fd)
    ROLE:
Accept peer
  - NAME:
net_read_some
    KIND:
FUNC
    SIGNATURE:
enum net_io_result net_read_some(int fd, uint8_t *buf, size_t cap, size_t *out_n)
    ROLE:
Partial read
  - NAME:
net_write_some
    KIND:
FUNC
    SIGNATURE:
enum net_io_result net_write_some(int fd, const uint8_t *buf, size_t len, size_t *out_n)
    ROLE:
Partial write
  - NAME:
net_close_fd
    KIND:
FUNC
    SIGNATURE:
void net_close_fd(int *fd)
    ROLE:
Close descriptor
