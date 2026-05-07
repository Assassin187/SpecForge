CC ?= gcc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -pedantic -D_POSIX_C_SOURCE=200809L -I.
LDFLAGS ?=

SRCS = \
	main.c \
	network/udp_server.c \
	protocol/coap_message.c \
	protocol/coap_codec.c \
	resource/resource_router.c \
	server/coap_server.c

all: coap_server

coap_server: $(SRCS)
	$(CC) $(CFLAGS) -o $@ $(SRCS) $(LDFLAGS)

clean:
	rm -f coap_server

.PHONY: all clean
