[PROMPT]
Perform one nonblocking send with MSG_NOSIGNAL, retrying EINTR internally and reporting progress, would-block or a fatal error with the exact number of bytes accepted.

[RELY]
STRUCT:

FUNC:
  - NAME:
send
    KIND:
TYPE_REF
    ROLE:
one nonblocking write of at most len bytes from the caller's queue
VAR:
  - NAME:
MSG_NOSIGNAL
    ROLE:
suppress SIGPIPE on a peer that closed, so a dead peer cannot kill the broker
  - NAME:
EINTR
    ROLE:
retry the send after a signal
  - NAME:
EAGAIN
    ROLE:
socket buffer full
  - NAME:
EWOULDBLOCK
    ROLE:
same meaning as EAGAIN

[GUARANTEE]
RAW:
enum net_io_result net_write_some(int fd, const uint8_t *buf, size_t len, size_t *out_n)
NAME:
net_write_some
RETURN:
enum net_io_result
PARAMS:
  - TYPE:
int
    NAME:
fd
    NULLABLE:
false
    OWNERSHIP:
BORROWED
  - TYPE:
const uint8_t *
    NAME:
buf
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
size_t
    NAME:
len
    NULLABLE:
false
    OWNERSHIP:
BORROWED
  - TYPE:
size_t *
    NAME:
out_n
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
fd: a nonblocking peer descriptor (mode set by net_accept). buf/len: the bytes to write, normally the leading unflushed bytes of the session output buffer, so the caller consumes exactly the reported count afterwards. out_n: bytes accepted by the kernel.
  ACTION:
1) If out_n is NULL return NET_IO_ERROR; *out_n = 0. 2) If fd < 0 or buf is NULL return NET_IO_ERROR. 3) If len == 0 { *out_n = 0; return NET_IO_WOULD_BLOCK; } - a zero-length write is a documented no-op reported as no progress so that a drain loop that only continues on NET_IO_PROGRESS cannot spin; the caller never passes an empty buffer because it checks the queue first. 4) Loop: n = send(fd, buf, len, MSG_NOSIGNAL); if n > 0 { *out_n = (size_t)n; return NET_IO_PROGRESS; } if n == 0 return NET_IO_ERROR (a zero-length acceptance for a nonzero request is not progress and must not be reported as such); if n < 0: if errno == EINTR continue; if errno == EAGAIN || errno == EWOULDBLOCK return NET_IO_WOULD_BLOCK; else return NET_IO_ERROR. 5) Because the descriptor is nonblocking and only one send is attempted per call, a peer with a full socket buffer never blocks the loop; the caller stops and waits for EPOLLOUT. MSG_NOSIGNAL means a peer that vanished produces EPIPE/ECONNRESET instead of a fatal SIGPIPE (SIGPIPE is also ignored in main as a second line of defence). The remaining bytes stay in the caller's buffer, so a partial write is always resumable: the caller consumes exactly *out_n and keeps the rest.
  OUTPUT:
NET_IO_PROGRESS with *out_n in 1..len when some bytes were accepted: the caller must consume exactly *out_n bytes from the front of its queue and keep sending later. NET_IO_WOULD_BLOCK with *out_n == 0 when the socket buffer is full or len was 0. NET_IO_ERROR with *out_n == 0 for NULL/invalid arguments, a zero-length acceptance or a fatal write error.
  INVARIANTS_USED:
    - the descriptor is nonblocking so a single send is bounded and cannot stall unrelated peers
    - partial writes are legal and the unaccepted bytes must stay queued
    - MSG_NOSIGNAL prevents a per-peer write failure from terminating the process
    - the caller consumes exactly the bytes the kernel accepted, so no byte is written twice
  PRECONDITION:
fd is NULL or a valid nonblocking descriptor; buf points to len readable bytes when len > 0.
  POSTCONDITION:
On progress the first *out_n bytes of buf were accepted by the kernel and the buffer is otherwise untouched; on any other result no byte was accepted.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Safe per descriptor; concurrent writes on the same descriptor could reorder bytes.
