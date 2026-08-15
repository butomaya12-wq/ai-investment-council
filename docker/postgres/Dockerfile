FROM debian:bookworm-slim
ARG PG_VERSION=18.6
ARG PG_SHA256=983ee554ec53dbeb9b70797bef9fcf4e67e117e7e48ca1463cc80b3ff8e8ff3f
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl build-essential libreadline-dev zlib1g-dev libicu-dev pkg-config bison flex perl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /tmp
RUN curl -fsSLO "https://ftp.postgresql.org/pub/source/v${PG_VERSION}/postgresql-${PG_VERSION}.tar.gz" \
 && curl -fsSLO "https://ftp.postgresql.org/pub/source/v${PG_VERSION}/postgresql-${PG_VERSION}.tar.gz.sha256" \
 && sha256sum -c "postgresql-${PG_VERSION}.tar.gz.sha256" \
 && printf '%s  %s\n' "${PG_SHA256}" "postgresql-${PG_VERSION}.tar.gz" | sha256sum -c -
RUN tar -xzf "postgresql-${PG_VERSION}.tar.gz" \
 && cd "postgresql-${PG_VERSION}" \
 && ./configure --prefix=/opt/postgresql --with-icu \
 && make -j"$(nproc)" \
 && make install
RUN useradd --system --create-home --home-dir /var/lib/postgresql --shell /bin/sh postgres \
 && mkdir -p /var/lib/postgresql/data \
 && chown -R postgres:postgres /var/lib/postgresql
COPY docker/postgres/entrypoint.sh /usr/local/bin/aic-postgres-entrypoint
RUN chmod 755 /usr/local/bin/aic-postgres-entrypoint
ENV PATH="/opt/postgresql/bin:${PATH}"
ENV PGDATA="/var/lib/postgresql/data"
USER postgres
EXPOSE 5432
ENTRYPOINT ["/usr/local/bin/aic-postgres-entrypoint"]
