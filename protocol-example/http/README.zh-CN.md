# HTTP/1.1 Server Example

这是一个独立的 HTTP/1.1 server C implementation，基于 TCP 和 epoll，支持 `GET`、`HEAD`、`POST`、static files、directory listings、MIME types 和 document-root path sandbox。

```bash
cd protocol-example/http
make
mkdir -p /tmp/specforge-www
printf 'hello\n' > /tmp/specforge-www/hello.txt
./http_server 8080 /tmp/specforge-www
```

默认端口为 `8080`，默认 document root 为当前目录。可测试：

```bash
curl http://127.0.0.1:8080/hello.txt
curl -I http://127.0.0.1:8080/hello.txt
curl -X POST -d 'new content' http://127.0.0.1:8080/new.txt
```

使用 `make clean` 清理构建产物。keep-alive、chunked transfer、range/conditional requests、TLS 和完整 HTTP/1.1 coverage 不在当前范围内。
