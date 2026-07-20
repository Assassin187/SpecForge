# FTP Server Example

This standalone FTP server in C supports fixed-credential authentication, a PASV data channel, and common directory, transfer, and file-management commands. The default credentials are `ftpuser` / `ftppass`.

```bash
cd protocol-example/ftp
make
./ftp_server 2121 /tmp/ftp-root
```

The default port is `2121`, and the default root is the current directory. Test with:

```bash
lftp -u ftpuser,ftppass -p 2121 127.0.0.1
curl --ftp-pasv --user ftpuser:ftppass ftp://127.0.0.1:2121/
```

Run `make clean` to remove build artifacts. PORT mode, FTPS/TLS, and complete FTP coverage are out of scope.
