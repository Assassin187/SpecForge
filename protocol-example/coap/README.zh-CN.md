# CoAP Server Example

这是一个基于 UDP 的 minimum CoAP server C implementation，支持常用 request methods、options、response codes，以及 `/hello`、`/echo`、`/kv/<key>` 和 `/.well-known/core` resources。

```bash
cd protocol-example/coap
make
./coap_server 5683
```

默认端口为 `5683`。安装 `libcoap3-bin` 后可测试：

```bash
coap-client-notls -m get coap://127.0.0.1:5683/hello
coap-client-notls -m post -e hello coap://127.0.0.1:5683/echo
coap-client-notls -m put -e value coap://127.0.0.1:5683/kv/demo
```

使用 `make clean` 清理构建产物。DTLS、Observe、Block1/Block2、retransmission state 和完整 RFC coverage 不在当前范围内。
