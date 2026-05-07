#ifndef FTP_SESSION_H
#define FTP_SESSION_H

#include <stdbool.h>
#include <stddef.h>

#include "../network/connection.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct ftp_session {
    int control_fd;
    ftp_connection_t* conn;
    bool authenticated;
    char pending_user[128];
    char cwd[1024];
    char rename_from[1024];
    bool has_rename_from;
    int pasv_listen_fd;
} ftp_session_t;

ftp_session_t* ftp_session_create(int control_fd);
void ftp_session_destroy(ftp_session_t* session);
void ftp_session_reset_pasv(ftp_session_t* session);

#ifdef __cplusplus
}
#endif

#endif
