ARG BUILD_FROM

# Stage 1: compile velserv statically with Alpine + musl
FROM alpine:3.19 AS builder
RUN apk add --no-cache gcc musl-dev wget
WORKDIR /build
RUN wget -q https://raw.githubusercontent.com/jeroends/velserv/master/velserv.c
RUN gcc -static -o velserv velserv.c -lpthread

# Stage 2: minimal runtime image
FROM $BUILD_FROM
COPY --from=builder /build/velserv /usr/local/bin/velserv
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
