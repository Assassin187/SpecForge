#include "auth.h"

#include <string.h>

static const char* k_user = "ftpuser";
static const char* k_pass = "ftppass";

const char* ftp_auth_default_user(void) {
    return k_user;
}

int ftp_auth_validate(const char* user, const char* pass) {
    if (user == NULL || pass == NULL) {
        return 0;
    }
    return strcmp(user, k_user) == 0 && strcmp(pass, k_pass) == 0;
}
