# SMTP Server Example

This standalone SMTP receiving server in C supports common mail transactions and AUTH LOGIN/PLAIN, and stores received messages as `.eml` files. The default port is `2525`, with credentials `smtpuser` / `smtppass`.

```bash
cd protocol-example/smtp
make
mkdir -p /tmp/specforge-mail
./smtp_server 2525 /tmp/specforge-mail
```

Test with `swaks`:

```bash
swaks --server 127.0.0.1:2525 \
  --auth LOGIN --auth-user smtpuser --auth-password smtppass \
  --from alice@example.com --to bob@example.com \
  --body 'Hello from SpecForge'
```

Run `make clean` to remove build artifacts. TLS, external delivery, and complete SMTP extension coverage are out of scope.
