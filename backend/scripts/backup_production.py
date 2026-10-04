"""Export all public application tables at one consistent read-only snapshot."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, UTC

import psycopg
from psycopg import sql

out = Path('backup-output')
out.mkdir(exist_ok=True)
url = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://', 1)
env = dict(os.environ, PGDATABASE=url)
with psycopg.connect(url) as conn:
    conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    snapshot = conn.execute('SELECT pg_export_snapshot()').fetchone()[0]
    tables = [r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
    counts = {table: conn.execute(sql.SQL('SELECT count(*) FROM public.{}').format(sql.Identifier(table))).fetchone()[0] for table in tables}
    with open(out / 'public.dump', 'wb') as target:
        subprocess.run(['docker', 'run', '--rm', '-e', 'PGDATABASE', 'postgres:18',
                        'pg_dump', '--format=custom', '--schema=public', '--no-owner',
                        '--no-privileges', '--snapshot=' + snapshot], env=env, stdout=target, check=True)
    manifest = {'created_at': datetime.now(UTC).isoformat(), 'tables': counts,
                'scope': 'all public application tables; excludes auth and vault schemas',
                'server_version': conn.info.server_version,
                'sha256': hashlib.file_digest(open(out / 'public.dump', 'rb'), 'sha256').hexdigest()}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2))
subprocess.run(['tar', '-cf', str(out / 'archive.tar'), '-C', str(out), 'public.dump', 'manifest.json'], check=True)
subprocess.run(['openssl', 'cms', '-encrypt', '-aes256', '-binary', '-stream', '-outform', 'DER',
                '-in', str(out / 'archive.tar'), '-out', str(out / 'busy-cafe-backup.cms'),
                'scripts/backup-cert.pem'], check=True)
for filename in ('public.dump', 'archive.tar', 'manifest.json'):
    (out / filename).unlink()
print('Encrypted application backup complete; plaintext removed from runner.')
