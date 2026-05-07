#include "ftp_command.h"

#include <ctype.h>
#include <stddef.h>
#include <string.h>

static ftp_command_kind_t parse_kind(const char* cmd) {
    if (strcmp(cmd, "USER") == 0) return FTP_CMD_USER;
    if (strcmp(cmd, "PASS") == 0) return FTP_CMD_PASS;
    if (strcmp(cmd, "QUIT") == 0) return FTP_CMD_QUIT;
    if (strcmp(cmd, "SYST") == 0) return FTP_CMD_SYST;
    if (strcmp(cmd, "NOOP") == 0) return FTP_CMD_NOOP;
    if (strcmp(cmd, "PWD") == 0) return FTP_CMD_PWD;
    if (strcmp(cmd, "CWD") == 0) return FTP_CMD_CWD;
    if (strcmp(cmd, "TYPE") == 0) return FTP_CMD_TYPE;
    if (strcmp(cmd, "PASV") == 0) return FTP_CMD_PASV;
    if (strcmp(cmd, "LIST") == 0) return FTP_CMD_LIST;
    if (strcmp(cmd, "NLST") == 0) return FTP_CMD_NLST;
    if (strcmp(cmd, "RETR") == 0) return FTP_CMD_RETR;
    if (strcmp(cmd, "STOR") == 0) return FTP_CMD_STOR;
    if (strcmp(cmd, "DELE") == 0) return FTP_CMD_DELE;
    if (strcmp(cmd, "MKD") == 0) return FTP_CMD_MKD;
    if (strcmp(cmd, "RMD") == 0) return FTP_CMD_RMD;
    if (strcmp(cmd, "RNFR") == 0) return FTP_CMD_RNFR;
    if (strcmp(cmd, "RNTO") == 0) return FTP_CMD_RNTO;
    return FTP_CMD_UNKNOWN;
}

int ftp_command_parse(const char* line, ftp_command_t* out) {
    if (line == NULL || out == NULL) {
        return -1;
    }

    memset(out, 0, sizeof(*out));

    while (*line == ' ' || *line == '\t') {
        ++line;
    }
    if (*line == '\0') {
        return -1;
    }

    char cmd[8];
    size_t i = 0;
    while (line[i] != '\0' && !isspace((unsigned char)line[i]) && i < sizeof(cmd) - 1) {
        cmd[i] = (char)toupper((unsigned char)line[i]);
        ++i;
    }
    cmd[i] = '\0';

    out->kind = parse_kind(cmd);

    const char* p = line + i;
    while (*p == ' ' || *p == '\t') {
        ++p;
    }
    if (*p != '\0') {
        out->has_arg = true;
        strncpy(out->arg, p, sizeof(out->arg) - 1);
        out->arg[sizeof(out->arg) - 1] = '\0';

        size_t len = strlen(out->arg);
        while (len > 0 && (out->arg[len - 1] == ' ' || out->arg[len - 1] == '\t')) {
            out->arg[len - 1] = '\0';
            --len;
        }
    }

    return 0;
}

const char* ftp_command_name(ftp_command_kind_t kind) {
    switch (kind) {
        case FTP_CMD_USER: return "USER";
        case FTP_CMD_PASS: return "PASS";
        case FTP_CMD_QUIT: return "QUIT";
        case FTP_CMD_SYST: return "SYST";
        case FTP_CMD_NOOP: return "NOOP";
        case FTP_CMD_PWD: return "PWD";
        case FTP_CMD_CWD: return "CWD";
        case FTP_CMD_TYPE: return "TYPE";
        case FTP_CMD_PASV: return "PASV";
        case FTP_CMD_LIST: return "LIST";
        case FTP_CMD_NLST: return "NLST";
        case FTP_CMD_RETR: return "RETR";
        case FTP_CMD_STOR: return "STOR";
        case FTP_CMD_DELE: return "DELE";
        case FTP_CMD_MKD: return "MKD";
        case FTP_CMD_RMD: return "RMD";
        case FTP_CMD_RNFR: return "RNFR";
        case FTP_CMD_RNTO: return "RNTO";
        default: return "UNKNOWN";
    }
}
