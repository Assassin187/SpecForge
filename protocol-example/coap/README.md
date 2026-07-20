# CoAP Server Example

This is a minimum CoAP server implementation in C over UDP. It supports common request methods, options, response codes, and the `/hello`, `/echo`, `/kv/<key>`, and `/.well-known/core` resources.

```bash
cd protocol-example/coap
make
./coap_server 5683
```

The default port is `5683`. Install `libcoap3-bin` to run these checks:

```bash
coap-client-notls -m get coap://127.0.0.1:5683/hello
coap-client-notls -m post -e hello coap://127.0.0.1:5683/echo
coap-client-notls -m put -e value coap://127.0.0.1:5683/kv/demo
```

Run `make clean` to remove build artifacts. DTLS, Observe, Block1/Block2, retransmission state, and complete RFC coverage are out of scope.
