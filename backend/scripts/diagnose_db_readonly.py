"""Read-only incident diagnostics; never changes database settings."""
import json
import os

import psycopg


def main():
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("BEGIN READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '15s'")
        queries = {
            "database_state": """
                SELECT pg_size_pretty(pg_database_size(current_database())) AS size,
                       pg_database_size(current_database()) AS bytes,
                       current_setting('default_transaction_read_only') AS default_read_only,
                       pg_is_in_recovery() AS in_recovery
            """,
            "readonly_settings": """
                SELECT setting, source FROM pg_settings
                WHERE name = 'default_transaction_read_only'
            """,
            "stored_readonly_settings": """
                SELECT CASE WHEN setdatabase = 0 THEN 'all' ELSE 'database' END AS scope,
                       CASE WHEN setrole = 0 THEN 'all' ELSE 'role' END AS role_scope,
                       value
                FROM pg_db_role_setting CROSS JOIN LATERAL unnest(setconfig) AS value
                WHERE value LIKE '%transaction_read_only=%'
            """,
            "largest_tables": """
                SELECT schemaname, relname, n_live_tup, n_dead_tup,
                       pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
                       pg_total_relation_size(relid) AS total_bytes,
                       last_autovacuum
                FROM pg_stat_user_tables
                ORDER BY pg_total_relation_size(relid) DESC LIMIT 12
            """,
            "cron_recent_status": """
                SELECT jobid, status, count(*), min(start_time), max(start_time)
                FROM (SELECT jobid, status, start_time FROM cron.job_run_details
                      ORDER BY start_time DESC LIMIT 100) recent
                GROUP BY jobid, status ORDER BY jobid, status
            """,
            "dispatch_recent_status": """
                SELECT status_code, timed_out, (error_msg IS NOT NULL) AS has_error,
                       count(*), min(created), max(created)
                FROM net._http_response GROUP BY status_code, timed_out,
                     (error_msg IS NOT NULL)
            """,
            "ingest_recent_status": """
                SELECT started_at, completed_at, targets, saved, failed, status
                FROM ingest_cycles ORDER BY started_at DESC LIMIT 5
            """,
        }
        for name, sql in queries.items():
            conn.execute("SAVEPOINT diagnostic")
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    columns = [c.name for c in cur.description]
                    print(name + ': ' + json.dumps(
                        [dict(zip(columns, row)) for row in cur.fetchall()], default=str
                    ))
            except psycopg.Error as exc:
                conn.execute("ROLLBACK TO SAVEPOINT diagnostic")
                print(name + ': ' + type(exc).__name__)
            conn.execute("RELEASE SAVEPOINT diagnostic")
        conn.execute("ROLLBACK")


if __name__ == "__main__":
    main()
