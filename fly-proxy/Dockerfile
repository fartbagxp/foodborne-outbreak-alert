FROM alpine:3.20

RUN apk add --no-cache tinyproxy gettext

COPY tinyproxy.conf.template /etc/tinyproxy/tinyproxy.conf.template
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8888

ENTRYPOINT ["/entrypoint.sh"]
