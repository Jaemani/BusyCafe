"""One-off recovery after local backup restoration and row-count verification."""
import argparse
from datetime import UTC, datetime, timedelta
import os
import psycopg

parser = argparse.ArgumentParser()
parser.add_argument('--expected-snapshots', type=int, required=True)
parser.add_argument('--backup-sha256', required=True)
parser.add_argument('--apply', action='store_true')
args = parser.parse_args()
if len(args.backup_sha256) != 64 or any(c not in '0123456789abcdef' for c in args.backup_sha256):
    raise SystemExit('A verified local backup checksum is required')
cutoff = datetime.now(UTC) - timedelta(days=2)
with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as conn:
    conn.execute("SET statement_timeout = '10min'")
    conn.execute("SET lock_timeout = '10s'")
    count = conn.execute('SELECT count(*) FROM public.hotspot_snapshots').fetchone()[0]
    print(f'current_snapshots={count} expected_snapshots={args.expected_snapshots}', flush=True)
    if count != args.expected_snapshots:
        raise SystemExit('Snapshot count changed since backup; stop and make a new backup')
    latest = [r[0] for r in conn.execute('''
        SELECT DISTINCT ON (hotspot_id) id FROM public.hotspot_snapshots
        ORDER BY hotspot_id, observed_at DESC
    ''')]
    keep = conn.execute('''SELECT count(*) FROM public.hotspot_snapshots
        WHERE observed_at >= %s OR id = ANY(%s)''', (cutoff, latest)).fetchone()[0]
    print(f'backup_sha256={args.backup_sha256} total={count} keep={keep} delete={count-keep}', flush=True)
    if not args.apply:
        raise SystemExit(0)
    # Supabase documents a session-local write override specifically for reducing disk usage.
    conn.execute("SET default_transaction_read_only = off")
    # Preserve rows and reclaim storage atomically instead of generating a million
    # DELETE records and requiring additional disk space for VACUUM FULL.
    with conn.transaction():
        conn.execute('LOCK TABLE public.hotspot_snapshots IN ACCESS EXCLUSIVE MODE')
        locked_count = conn.execute('SELECT count(*) FROM public.hotspot_snapshots').fetchone()[0]
        if locked_count != count:
            raise RuntimeError('Snapshot count changed; aborting cleanup')
        references = conn.execute("""SELECT count(*) FROM pg_constraint
            WHERE contype='f' AND confrelid='public.hotspot_snapshots'::regclass""").fetchone()[0]
        if references:
            raise RuntimeError('Snapshot table has dependent foreign keys; aborting cleanup')
        conn.execute('''CREATE TEMP TABLE busycafe_keep_snapshots ON COMMIT DROP AS
            SELECT * FROM public.hotspot_snapshots
            WHERE observed_at >= %s OR id=ANY(%s)''', (cutoff, latest))
        conn.execute('TRUNCATE public.hotspot_snapshots')
        conn.execute('INSERT INTO public.hotspot_snapshots SELECT * FROM busycafe_keep_snapshots')
        remaining = conn.execute('SELECT count(*) FROM public.hotspot_snapshots').fetchone()[0]
        protected = conn.execute('SELECT count(*) FROM public.hotspot_snapshots WHERE id=ANY(%s)', (latest,)).fetchone()[0]
        if remaining != keep or protected != len(latest):
            raise RuntimeError('Retention verification failed; transaction rolls back')
    print(f'deleted={count-remaining}', flush=True)
    print(f'verified remaining={remaining} latest_hotspots={protected}', flush=True)
    conn.execute('ANALYZE public.hotspot_snapshots')
    print('database_size=' + conn.execute('SELECT pg_size_pretty(pg_database_size(current_database()))').fetchone()[0], flush=True)
    print('snapshot_size=' + conn.execute("SELECT pg_size_pretty(pg_total_relation_size('public.hotspot_snapshots'))").fetchone()[0], flush=True)
