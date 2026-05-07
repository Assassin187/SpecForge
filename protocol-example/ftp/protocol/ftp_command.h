#ifndef FTP_COMMAND_H
#define FTP_COMMAND_H

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    FTP_CMD_USER,
    FTP_CMD_PASS,
    FTP_CMD_QUIT,
    FTP_CMD_SYST,
    FTP_CMD_NOOP,
    FTP_CMD_PWD,
    FTP_CMD_CWD,
    FTP_CMD_TYPE,
    FTP_CMD_PASV,
    FTP_CMD_LIST,
    FTP_CMD_NLST,
    FTP_CMD_RETR,
    FTP_CMD_STOR,
    FTP_CMD_DELE,
    FTP_CMD_MKD,
    FTP_CMD_RMD,
    FTP_CMD_RNFR,
    FTP_CMD_RNTO,
    FTP_CMD_UNKNOWN,
} ftp_command_kind_t;

typedef struct {
    ftp_command_kind_t kind;
    char arg[1024];
    bool has_arg;
} ftp_command_t;

int ftp_command_parse(const char* line, ftp_command_t* out);
const char* ftp_command_name(ftp_command_kind_t kind);

#ifdef __cplusplus
}
#endif

#endif
