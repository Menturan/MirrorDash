FROM debian:trixie
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq && apt-get install -y -qq systemd systemd-sysv curl ca-certificates nftables iproute2 sudo python3 git >/dev/null \
    && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
ENV UV_PYTHON_INSTALL_DIR=/opt/uvpython
RUN uv python install 3.14 && chmod -R a+rX /opt/uvpython
RUN groupadd -g 988 i2c && groupadd -g 989 spi && groupadd -g 960 mirrordash-mod \
    && useradd --system -u 960 -g 960 -G i2c,spi -d /nonexistent -s /usr/sbin/nologin mirrordash-mod \
    && useradd -m -u 1000 -G mirrordash-mod,i2c,spi pi
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
