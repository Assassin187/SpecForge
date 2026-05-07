# SMTP Server Example (C)

这是一个可运行的 SMTP 示例实现，位于 `protocol-example/smtp`，支持最常用的邮件接收流程。

## 已实现功能

- SMTP 基础命令：HELO、EHLO、MAIL FROM、RCPT TO、DATA、RSET、NOOP、QUIT
- 认证：AUTH LOGIN、AUTH PLAIN
- 本地投递：将邮件落盘到指定目录（默认 `/mail-test`）
- 基础限制：
  - 最大收件人数量 `32`
  - 单邮件最大大小 `1MB`
  - 控制命令行最大长度约 `2048`

## 默认配置

- 监听端口：`2525`
- 邮件目录：`/mail-test`
- 测试账号：`smtpuser`
- 测试密码：`smtppass`

## 构建

```bash
cd ~/SpecForge/protocol-example/smtp
make
```

## 运行

```bash
./smtp_server
# 或自定义端口与目录
./smtp_server 2525 /mail-test
```

## 快速测试（swaks）

```bash
swaks --server 127.0.0.1:2525 \
  --auth LOGIN --auth-user smtpuser --auth-password smtppass \
  --from alice@example.com --to bob@example.com \
  --header "Subject: SMTP demo" \
  --body "Hello from smtp example"
```

## 快速测试（openssl + AUTH PLAIN）

`AUTH PLAIN` 负载格式为 `\0user\0pass` 的 base64：

```bash
printf '\0smtpuser\0smtppass' | base64
```

然后可用 `nc` 手工测试：

```bash
nc 127.0.0.1 2525
EHLO localhost
AUTH PLAIN AHNtdHB1c2VyAHNtdHBwYXNz
MAIL FROM:<alice@example.com>
RCPT TO:<bob@example.com>
DATA
Subject: test

hello
.
QUIT
```

## 落盘格式

每封邮件会生成一个 `.eml` 文件，包含 envelope 元信息头（HELO、认证用户、MAIL FROM、RCPT TO）和原始 DATA 内容。
