# B1.1 verification

B1.1 checks only:
- fresh repository created;
- exact Python pin file = 3.12.13;
- approved direct dependencies only;
- uv.lock generated and checked by pinned uv;
- dependency environment synced from lock;
- compose skeleton present;
- PostgreSQL 18.6 source identity frozen in Dockerfile;
- Alembic migration skeleton generated from locked Alembic;
- no B1.2+ implementation;
- no external finance/model/broker calls;
- no public push/deploy.
