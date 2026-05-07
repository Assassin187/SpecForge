CC ?= gcc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -pedantic -D_POSIX_C_SOURCE=200809L -I.
LDFLAGS ?=

SRCS = \
	main.c \
	network/tcp_server.c \
	network/connection.c \
	protocol/smtp_command.c \
	protocol/smtp_response.c \
	server/smtp_session.c \
	server/smtp_server.c \
	auth/smtp_auth.c \
	storage/mail_store.c

OBJS = $(SRCS:.c=.o)
TARGET = smtp_server

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $(OBJS) $(LDFLAGS)

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f $(TARGET) $(OBJS)

.PHONY: all clean
