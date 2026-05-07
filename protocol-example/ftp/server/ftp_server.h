#ifndef FTP_SERVER_H
#define FTP_SERVER_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct ftp_server ftp_server_t;

ftp_server_t* ftp_server_create(uint16_t port, const char* root_dir);
int ftp_server_start(ftp_server_t* server);
int ftp_server_run(ftp_server_t* server);
void ftp_server_stop(ftp_server_t* server);
void ftp_server_destroy(ftp_server_t* server);

#ifdef __cplusplus
}
#endif

#endif
