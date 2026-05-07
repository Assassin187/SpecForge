#ifndef FTP_CONNECTION_H
#define FTP_CONNECTION_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct ftp_connection ftp_connection_t;

ftp_connection_t* ftp_connection_create(int fd);
void ftp_connection_destroy(ftp_connection_t* conn);

int ftp_connection_fd(const ftp_connection_t* conn);
int ftp_connection_read(ftp_connection_t* conn);
char* ftp_connection_pop_line(ftp_connection_t* conn);

int ftp_connection_queue(ftp_connection_t* conn, const void* data, size_t len);
int ftp_connection_queue_str(ftp_connection_t* conn, const char* text);
int ftp_connection_flush(ftp_connection_t* conn);
bool ftp_connection_has_pending(const ftp_connection_t* conn);

#ifdef __cplusplus
}
#endif

#endif
