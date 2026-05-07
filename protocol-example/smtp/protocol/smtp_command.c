#include "smtp_command.h"

#include <ctype.h>
#include <stddef.h>
#include <string.h>

static smtp_command_kind_t parse_kind(const char* cmd) {
    if (strcmp(cmd, "HELO") == 0) return SMTP_CMD_HELO;
    if (strcmp(cmd, "EHLO") == 0) return SMTP_CMD_EHLO;
    if (strcmp(cmd, "MAIL") == 0) return SMTP_CMD_MAIL;
    if (strcmp(cmd, "RCPT") == 0) return SMTP_CMD_RCPT;
    if (strcmp(cmd, "DATA") == 0) return SMTP_CMD_DATA;
    if (strcmp(cmd, "RSET") == 0) return SMTP_CMD_RSET;
    if (strcmp(cmd, "NOOP") == 0) return SMTP_CMD_NOOP;
    if (strcmp(cmd, "QUIT") == 0) return SMTP_CMD_QUIT;
    if (strcmp(cmd, "AUTH") == 0) return SMTP_CMD_AUTH;
    if (strcmp(cmd, "VRFY") == 0) return SMTP_CMD_VRFY;
    return SMTP_CMD_UNKNOWN;
}

int smtp_command_parse(const char* line, smtp_command_t* out) {
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

const char* smtp_command_name(smtp_command_kind_t kind) {
    switch (kind) {
        case SMTP_CMD_HELO: return "HELO";
        case SMTP_CMD_EHLO: return "EHLO";
        case SMTP_CMD_MAIL: return "MAIL";
        case SMTP_CMD_RCPT: return "RCPT";
        case SMTP_CMD_DATA: return "DATA";
        case SMTP_CMD_RSET: return "RSET";
        case SMTP_CMD_NOOP: return "NOOP";
        case SMTP_CMD_QUIT: return "QUIT";
        case SMTP_CMD_AUTH: return "AUTH";
        case SMTP_CMD_VRFY: return "VRFY";
        default: return "UNKNOWN";
    }
}
