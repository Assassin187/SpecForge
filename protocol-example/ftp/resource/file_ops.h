#ifndef FTP_FILE_OPS_H
#define FTP_FILE_OPS_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

int ftp_fileops_list_dir(int data_fd, const char* abs_dir);
int ftp_fileops_send_file(int data_fd, const char* abs_file);
int ftp_fileops_recv_file(int data_fd, const char* abs_file);
int ftp_fileops_delete(const char* abs_path);
int ftp_fileops_mkdir(const char* abs_path);
int ftp_fileops_rmdir(const char* abs_path);
int ftp_fileops_rename(const char* old_abs_path, const char* new_abs_path);

#ifdef __cplusplus
}
#endif

#endif
