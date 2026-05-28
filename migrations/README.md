# Migrations

Forward-only numbered SQL files. Applied by `python -m meridian.cli migrate`.

## Naming

```
NNNN_short_description.sql
```

- `NNNN` is a zero-padded sequence number. Lexicographic sort = apply order.
- Use snake_case for the description.
- One concept per migration. Don't bundle unrelated changes.

## Writing a migration

- Wrap the migration in a `BEGIN; ... COMMIT;` block so partial failures roll back.
- Never edit a migration after it has been applied anywhere. The runner
  checksums applied migrations and refuses to proceed if the file has changed.
- Add a corresponding test under `tests/` if the migration introduces a
  non-trivial constraint or data transformation.

## Running

```sh
make migrate   # applies all pending migrations
```

The runner is idempotent: re-running it after no new files have been added
is a no-op.

## Resetting (dev only)

```sh
make reset && make up && make migrate
```

`make reset` deletes the Postgres data volume, so the next `make up` boots
a fresh database; the init script re-enables `timescaledb` and the runner
re-applies every migration from scratch.
