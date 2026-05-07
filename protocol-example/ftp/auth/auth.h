#ifndef FTP_AUTH_H
#define FTP_AUTH_H

#ifdef __cplusplus
extern "C" {
#endif

const char* ftp_auth_default_user(void);
int ftp_auth_validate(const char* user, const char* pass);

#ifdef __cplusplus
}
#endif

#endif
