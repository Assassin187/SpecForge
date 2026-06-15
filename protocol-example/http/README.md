# HTTP/1.1 Server (Standalone C Implementation)

This project implements a standalone HTTP/1.1 server in C with no code dependency on sibling protocol projects.

## Features

- TCP serving using epoll-based event loop (default port: 8080)
- Methods: GET, HEAD, POST
- Static file serving with MIME-type detection
- Directory listing (auto-generated HTML) when index.html is absent
- `index.html` served automatically for directory requests
- POST writes request body to file at the requested URI path
- Root-directory sandbox for path-traversal protection
- Status codes: 200, 201, 400, 403, 404, 405, 413, 500, 501
- `Connection: close` per request (keep-alive not supported in v1)

## Scope and Limitations

This is a **minimum viable** HTTP/1.1 implementation focused on the core
request/response model. The following are intentionally out of scope:

- Persistent connections (keep-alive / pipelining)
- Chunked Transfer-Encoding
- Range requests
- Conditional requests (If-Modified-Since, ETag, etc.)
- CGI / server-side scripting
- TLS/HTTPS
- Virtual hosts
- Authentication

## Build

```bash
cd ~/SpecForge/protocol-example/http
make
```

## Run

```bash
# Run on port 8080 with current directory as root
./http_server

# Run on custom port and root
./http_server 8080 /var/www
```

## Quick Test

```bash
# Create a test directory
mkdir -p /tmp/test-www
echo '<h1>Hello HTTP</h1>' > /tmp/test-www/index.html
echo 'hello world' > /tmp/test-www/data.txt

# Start server in background
./http_server 8080 /tmp/test-www &
sleep 1

# GET a page
curl -v http://127.0.0.1:8080/

# GET a specific file
curl -v http://127.0.0.1:8080/data.txt

# HEAD request (no body)
curl -I http://127.0.0.1:8080/data.txt

# POST a new file
curl -v -X POST -d 'new content' http://127.0.0.1:8080/newfile.txt

# Verify POST created the file
curl http://127.0.0.1:8080/newfile.txt

# Stop
kill %1
```

## Verification Checklist

The following 8 tests cover the core functionality. Run them after starting the
server with `./http_server 8080 /tmp/test-www &`.

| # | Test Item | Command | Expected |
|---|-----------|---------|----------|
| 1 | **GET root (index.html)** | `curl -s http://127.0.0.1:8080/` | 200, returns HTML content |
| 2 | **GET text file** | `curl -s http://127.0.0.1:8080/data.txt` | 200, returns `hello world` |
| 3 | **HEAD (no body)** | `curl -s -I http://127.0.0.1:8080/data.txt` | 200, headers only, correct `Content-Length` |
| 4 | **404 Not Found** | `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/nonexistent` | `404` |
| 5 | **POST create file** | `curl -s -o /dev/null -w "%{http_code}" -X POST -d 'new content' http://127.0.0.1:8080/posted.txt` | `201` |
| 6 | **Verify POST content** | `curl -s http://127.0.0.1:8080/posted.txt` | 200, returns `new content` |
| 7 | **Directory listing** | `curl -s http://127.0.0.1:8080/subdir/` | 200, HTML `<pre>` listing |
| 8 | **Path traversal (sandbox)** | `curl -s -o /dev/null -w "%{http_code}" --path-as-is http://127.0.0.1:8080/../etc/passwd` | `403` |

Additional boundary tests:

| # | Test Item | Command | Expected |
|---|-----------|---------|----------|
| 9 | **POST to directory** | `curl -s -o /dev/null -w "%{http_code}" -X POST -d 'x' http://127.0.0.1:8080/subdir/` | `405` |
| 10 | **Unknown method** | `curl -s -o /dev/null -w "%{http_code}" -X DELETE http://127.0.0.1:8080/data.txt` | `501` |

## Directory Structure

```
http/
├── Makefile
├── main.c
├── README.md
├── network/
│   ├── tcp_server.h / .c    — epoll-based TCP acceptor + event loop
│   └── connection.h / .c     — per-connection read/write buffers
├── protocol/
│   ├── http_request.h / .c   — HTTP/1.1 request-line + header + body parser
│   └── http_response.h / .c  — HTTP/1.1 status-line + header + body encoder
├── server/
│   ├── http_server.h / .c    — request dispatch, server lifecycle
│   └── http_session.h / .c   — per-connection session state
└── resource/
    └── file_ops.h / .c       — path sandbox, MIME mapping, file read, dir listing
```

## Notes

- The server sends `Connection: close` on every response and closes the TCP
  connection after the response is fully flushed.
- POST to an existing directory returns 405 Method Not Allowed.
- Maximum request body is 16 MiB.
