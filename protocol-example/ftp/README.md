# FTP Server (Standalone C Implementation)

This project implements a standalone FTP server in C with no code dependency on sibling protocol projects.

## Features

- Control connection over TCP (default port: 2121)
- Fixed username/password authentication
- Commands: USER, PASS, QUIT, SYST, NOOP, TYPE, PWD, CWD
- Passive mode data channel: PASV
- Directory and transfer: LIST, RETR, STOR
- File management: DELE, MKD, RMD, RNFR, RNTO
- Root-directory sandbox for path traversal protection

## Credentials

- Username: `ftpuser`
- Password: `ftppass`

## Build

```bash
cd ~/SpecForge/protocol-example/ftp
make
```

## Run

```bash
# Run on port 2121 with current directory as root
./ftp_server

# Run on custom port/root
./ftp_server 2121 /tmp
```

## Quick Test

```bash
# Interactive test
lftp -u ftpuser,ftppass -p 2121 127.0.0.1

# curl upload/download/list
curl -v --ftp-pasv --user ftpuser:ftppass ftp://127.0.0.1:2121/
curl -v --ftp-pasv --user ftpuser:ftppass -T local.bin ftp://127.0.0.1:2121/local.bin
curl -v --ftp-pasv --user ftpuser:ftppass -o out.bin ftp://127.0.0.1:2121/local.bin
```

## Notes

- Only PASV mode is supported in this version.
- FTPS/TLS and PORT mode are intentionally out of scope.
