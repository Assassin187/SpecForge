# SMTP Server Example

这是一个独立的 SMTP receiving server C implementation，支持常见 mail transactions 和 AUTH LOGIN/PLAIN，并把接收的邮件保存为 `.eml`。默认端口为 `2525`，credentials 为 `smtpuser` / `smtppass`。

```bash
cd protocol-example/smtp
make
mkdir -p /tmp/specforge-mail
./smtp_server 2525 /tmp/specforge-mail
```

使用 `swaks` 测试：

```bash
swaks --server 127.0.0.1:2525 \
  --auth LOGIN --auth-user smtpuser --auth-password smtppass \
  --from alice@example.com --to bob@example.com \
  --body 'Hello from SpecForge'
```

使用 `make clean` 清理构建产物。TLS、external delivery 和完整 SMTP extension coverage 不在当前范围内。
