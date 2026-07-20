# HTTP/1.1 Server Example

This standalone HTTP/1.1 server in C uses TCP and epoll. It supports `GET`, `HEAD`, `POST`, static files, directory listings, MIME types, and a document-root path sandbox.

```bash
cd protocol-example/http
make
mkdir -p /tmp/specforge-www
printf 'hello\n' > /tmp/specforge-www/hello.txt
./http_server 8080 /tmp/specforge-www
```

The default port is `8080`, and the default document root is the current directory. Test with:

```bash
curl http://127.0.0.1:8080/hello.txt
curl -I http://127.0.0.1:8080/hello.txt
curl -X POST -d 'new content' http://127.0.0.1:8080/new.txt
```

Run `make clean` to remove build artifacts. Keep-alive, chunked transfer, range and conditional requests, TLS, and complete HTTP/1.1 coverage are out of scope.
