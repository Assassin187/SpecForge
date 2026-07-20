# FTP Server Example

这是一个独立的 FTP server C implementation，支持固定 credential authentication、PASV data channel，以及常用 directory、transfer 和 file-management commands。默认 credentials 为 `ftpuser` / `ftppass`。

```bash
cd protocol-example/ftp
make
./ftp_server 2121 /tmp/ftp-root
```

默认端口为 `2121`，默认 root 为当前目录。可使用以下命令测试：

```bash
lftp -u ftpuser,ftppass -p 2121 127.0.0.1
curl --ftp-pasv --user ftpuser:ftppass ftp://127.0.0.1:2121/
```

使用 `make clean` 清理构建产物。PORT mode、FTPS/TLS 和完整 FTP coverage 不在当前范围内。
