CC ?= gcc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -pedantic -D_POSIX_C_SOURCE=200809L -I.
LDFLAGS ?=

SRCS = \
	main.c \
	network/tcp_server.c \
	network/connection.c \
	protocol/ftp_command.c \
	protocol/ftp_response.c \
	server/ftp_session.c \
	server/ftp_server.c \
	auth/auth.c \
	resource/file_ops.c

OBJS = $(SRCS:.c=.o)
TARGET = ftp_server

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $(OBJS) $(LDFLAGS)

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f $(TARGET) $(OBJS)

.PHONY: all clean
