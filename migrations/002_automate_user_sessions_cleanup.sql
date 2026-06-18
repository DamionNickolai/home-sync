-- Automate cleanup for user_sessions
-- Policy:
-- 1) Delete expired sessions immediately.
-- 2) Delete inactive sessions older than 30 days.
-- 3) Null refresh_token for inactive sessions older than 7 days (defense in depth).

create or replace function public.cleanup_user_sessions(retention_days integer default 30)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
    -- Remove token material from old inactive rows first.
    update public.user_sessions
    set refresh_token = null
    where is_active = false
      and refresh_token is not null
      and last_accessed_at < now() - interval '7 days';

    -- Expired sessions are no longer valid.
    delete from public.user_sessions
    where expires_at < now();

    -- Inactive sessions older than retention window are purged.
    delete from public.user_sessions
    where is_active = false
      and last_accessed_at < now() - make_interval(days => retention_days);
end;
$$;

comment on function public.cleanup_user_sessions(integer)
is 'Prunes expired/inactive sessions and removes stale refresh tokens.';

-- Limit execution to service role / privileged contexts.
revoke all on function public.cleanup_user_sessions(integer) from public;
grant execute on function public.cleanup_user_sessions(integer) to service_role;

-- Schedule daily cleanup with pg_cron when available.
-- If pg_cron is not installed/enabled, the function still exists and can be run manually.
do $$
begin
    if exists (select 1 from pg_extension where extname = 'pg_cron') then
        -- Remove prior schedule if present.
        perform cron.unschedule(jobid)
        from cron.job
        where jobname = 'cleanup-user-sessions';

        -- Run daily at 03:15 UTC.
        perform cron.schedule(
            'cleanup-user-sessions',
            '15 3 * * *',
            $job$select public.cleanup_user_sessions(30);$job$
        );
    end if;
end;
$$;
