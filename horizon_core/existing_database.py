"""An isolated agent database inside an already hosted PostgreSQL engine.

Only the trusted state controller receives the admin DSN. Its deterministic
credential derives from the existing server secret and never enters model input.
No client tables/databases are read or changed. No new Railway service is needed.
"""
import hashlib
import hmac
from urllib.parse import urlsplit, urlunsplit, unquote, quote

DATABASE = 'horizon_agents_v1'
ROLE = 'horizon_agents_v1'
MARKER = 'horizon-agent-core:database-v1'


def agent_dsn(admin_dsn):
    parts = urlsplit(admin_dsn)
    password = unquote(parts.password or '')
    if parts.scheme not in ('postgres', 'postgresql') or not parts.hostname or not password:
        raise ValueError('A server-resolved PostgreSQL credential is required')
    derived = hmac.new(password.encode(), b'horizon-agent-database-v1', hashlib.sha256).hexdigest()
    host = '[' + parts.hostname + ']' if ':' in parts.hostname else parts.hostname
    authority = ROLE + ':' + derived + '@' + host + (':' + str(parts.port) if parts.port else '')
    return urlunsplit((parts.scheme, authority, '/' + DATABASE, parts.query, ''))


def provision(admin_dsn):
    import psycopg
    from psycopg import sql
    password = unquote(urlsplit(agent_dsn(admin_dsn)).password)
    with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=15) as admin:
        # Session lock makes a reconnect/retry reconcile the same owned resources.
        admin.execute('SELECT pg_advisory_lock(hashtextextended(%s,0))', (MARKER,))
        try:
            role = admin.execute("SELECT oid,shobj_description(oid,'pg_authid') FROM pg_roles WHERE rolname=%s", (ROLE,)).fetchone()
            if role and role[1] != MARKER:
                raise ValueError('Agent role name already belongs to another application')
            if not role:
                with admin.transaction():
                    admin.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT').format(sql.Identifier(ROLE), sql.Literal(password)))
                    admin.execute(sql.SQL('COMMENT ON ROLE {} IS {}').format(sql.Identifier(ROLE), sql.Literal(MARKER)))
            db = admin.execute("SELECT pg_get_userbyid(datdba),shobj_description(oid,'pg_database') FROM pg_database WHERE datname=%s", (DATABASE,)).fetchone()
            if db and (db[0] != ROLE or db[1] not in (None, MARKER)):
                raise ValueError('Agent database name already belongs to another application')
            if not db:
                admin.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(DATABASE), sql.Identifier(ROLE)))
            admin.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(DATABASE), sql.Literal(MARKER)))
            # Reconcile a server-password rotation only on our marked role.
            admin.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(sql.Identifier(ROLE), sql.Literal(password)))
        finally:
            admin.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', (MARKER,))
    with psycopg.connect(agent_dsn(admin_dsn), autocommit=True, connect_timeout=15) as connection:
        identity = connection.execute('SELECT current_database(),current_user,pg_database_size(current_database())').fetchone()
        privileges = connection.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone()
        if identity[:2] != (DATABASE, ROLE) or any(privileges):
            raise RuntimeError('Agent database isolation check failed')
        return {'connected': True, 'database': identity[0], 'role': identity[1],
                'database_bytes': identity[2], 'superuser': False, 'new_service_created': False}
