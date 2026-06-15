CC ?= gcc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -pedantic -D_POSIX_C_SOURCE=200809L -D_DEFAULT_SOURCE -I.
LDFLAGS ?=

SRCS = \
	main.c \
	network/tcp_server.c \
	network/connection.c \
	protocol/http_request.c \
	protocol/http_response.c \
	server/http_session.c \
	server/http_server.c \
	resource/file_ops.c

OBJS = $(SRCS:.c=.o)
TARGET = http_server

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $(OBJS) $(LDFLAGS)

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f $(TARGET) $(OBJS)

.PHONY: all clean
