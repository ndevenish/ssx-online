FROM alpine/git:latest

# RUN apt-get update && apt-get install -y git
RUN git clone https://github.com/DiamondLightSource/ispyb-database.git /opt/ispyb-database
WORKDIR /opt/ispyb-database/schemas/ispyb
RUN mv tables.sql   0_tables.sql && \
    mv lookups.sql  1_lookups.sql && \
    mv data.sql     2_data.sql && \
    mv routines.sql 3_routines.sql
COPY custom.sql 99_custom.sql

FROM mariadb:latest
COPY --from=0 /opt/ispyb-database/schemas/ispyb/*.sql /docker-entrypoint-initdb.d/
ENV MARIADB_ALLOW_EMPTY_ROOT_PASSWORD=y
ENV MARIADB_DATABASE=ispyb

