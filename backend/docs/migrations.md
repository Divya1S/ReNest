# Zero-Downtime Migration Runbook

Django's default `migrate` command acquires table-level locks that can block reads
and writes for seconds to minutes on large tables.  This runbook describes a
three-phase pattern that eliminates that window.

---

## When this applies

Use the three-phase pattern whenever a migration touches a table estimated to have
**more than 10 000 rows** and does any of the following:

- Adds a NOT NULL column without a database-level default
- Adds an index (`AddIndex` or `unique=True`)
- Alters a column type
- Adds a foreign-key constraint

Routine migrations (new table, drop unused table, add nullable column, rename via
`db_column`) can run synchronously without special handling.

---

## The three-phase pattern

### Phase 1 — Add the column or index as safe

Deploy a migration that is safe to run while the old code is live:

```python
# Safe nullable column — no lock, old app ignores the field
class Migration(migrations.Migration):
    operations = [
        migrations.AddField(
            model_name="listing",
            name="campus",
            field=models.ForeignKey(
                "accounts.Campus",
                null=True, blank=True,
                on_delete=models.SET_NULL,
                db_constraint=False,   # skip FK check at DB level for now
            ),
        ),
    ]
```

For an **index**, use `CONCURRENTLY` to avoid a full table lock:

```python
from django.db import migrations

class Migration(migrations.Migration):
    atomic = False  # CONCURRENTLY cannot run inside a transaction

    operations = [
        migrations.RunSQL(
            sql="CREATE INDEX CONCURRENTLY IF NOT EXISTS listings_listing_campus_id ON listings_listing (campus_id)",
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS listings_listing_campus_id",
        ),
    ]
```

Deploy and verify. The old application code keeps running; it simply ignores the
new nullable column or the new index is used transparently by the query planner.

---

### Phase 2 — Backfill existing rows via Celery

Write a one-shot Celery task (not a migration) to populate the new column in
batches so the work is spread over time and never locks the table:

```python
# listings/tasks.py
@shared_task(name="listings.backfill_campus")
def backfill_campus_task() -> int:
    from accounts.models import Campus
    from listings.models import Listing

    filled = 0
    qs = Listing.objects.filter(campus__isnull=True).select_related("owner__campus")
    for listing in qs.iterator(chunk_size=500):
        if listing.owner.campus_id:
            Listing.objects.filter(pk=listing.pk).update(campus=listing.owner.campus)
            filled += 1
    return filled
```

Trigger it from a management command or from the Django shell on staging first,
then production.  The task is idempotent — safe to re-run.

---

### Phase 3 — Add the constraint in a separate release

Once all rows are populated, deploy the migration that enforces the constraint:

```python
class Migration(migrations.Migration):
    operations = [
        migrations.AlterField(
            model_name="listing",
            name="campus",
            field=models.ForeignKey(
                "accounts.Campus",
                null=False,            # now required
                on_delete=models.SET_NULL,
            ),
        ),
    ]
```

This `ALTER TABLE … SET NOT NULL` takes a brief lock (milliseconds) to validate
the constraint because all rows already satisfy it.

---

## CI check for large-table migrations

The following check is run in CI (`.github/workflows/ci.yml`) and fails if a
migration touches a known large table without `atomic = False`:

```yaml
- name: Check migration safety
  run: |
    python manage.py migrate --check --run-syncdb
    python -c "
    import ast, sys, pathlib
    LARGE_TABLES = {'listings_listing', 'accounts_user', 'listings_reservation', 'listings_listingviewevent'}
    errors = []
    for p in pathlib.Path('backend').rglob('migrations/*.py'):
        src = p.read_text()
        if any(t in src for t in LARGE_TABLES) and 'atomic = False' not in src and 'CONCURRENTLY' not in src:
            if 'AddIndex' in src or 'AlterField' in src or 'NOT NULL' in src:
                errors.append(str(p))
    if errors:
        print('Potentially unsafe migrations on large tables:', errors)
        sys.exit(1)
    "
```

---

## Deployment command order

```bash
# 1. Run Phase 1 migration BEFORE deploying new code
python manage.py migrate

# 2. Deploy new application code (zero downtime — old schema still valid)
# ... rolling restart ...

# 3. Trigger backfill task
python manage.py shell -c "from listings.tasks import backfill_campus_task; backfill_campus_task.delay()"

# 4. Verify backfill completed (check Celery logs / flower)

# 5. Run Phase 3 migration
python manage.py migrate
```

---

## Starting Celery

```bash
# Worker
celery -A dormcycle worker -l info --concurrency=4

# Beat scheduler (run exactly one instance)
celery -A dormcycle beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler

# Or with the default file-based scheduler (no extra package needed):
celery -A dormcycle beat -l info
```
