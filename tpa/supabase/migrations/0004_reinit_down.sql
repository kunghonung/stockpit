-- ============================================================================
-- Migration 0004 DOWN — återställer funktionen/vyerna till läget EFTER 0003
-- (net_delta utan reinit-klass, ingen n_reinit, enriched utan prior_date).
-- Kör i SQL-editorn med service-rollen.
-- ============================================================================

drop view if exists tp_acceleration_current;
drop function if exists get_tp_acceleration(text, integer, integer, integer);
drop view if exists tp_revisions_enriched;

-- enriched tillbaka till 0001 (utan prior_date)
create view tp_revisions_enriched as
  select
    r.*,
    coalesce(r.fmp_prior_target, lag(r.new_target) over w) as old_target,
    case
      when r.fmp_prior_target is not null then 'kalla'
      when lag(r.new_target) over w is not null then 'harledd'
      else null
    end as old_target_kalla,
    (r.new_target - coalesce(r.fmp_prior_target, lag(r.new_target) over w)) as delta_abs,
    case when coalesce(r.fmp_prior_target, lag(r.new_target) over w) > 0
      then round((r.new_target / coalesce(r.fmp_prior_target, lag(r.new_target) over w) - 1) * 100, 2)
    end as delta_pct,
    (coalesce(r.fmp_prior_target, lag(r.new_target) over w) is not null
      and r.new_target = coalesce(r.fmp_prior_target, lag(r.new_target) over w)) as is_reiteration
  from tp_revisions r
  window w as (partition by r.ticker, r.analyst_house order by r.revision_date, r.published_at, r.id);

-- funktionen tillbaka till 0003 (3-arg, net_delta_pct, ingen reinit)
create function get_tp_acceleration(
  p_ticker     text,
  p_days       integer default 30,
  p_stale_days integer default 180
)
returns table (
  acceleration                     numeric,
  current_consensus                numeric,
  n_revisions                      integer,
  n_houses                         integer,
  n_houses_live                    integer,
  largest_single_contribution_pct  numeric,
  net_delta_pct                    numeric,
  points                           integer
)
language sql stable
security definer
set search_path = public, pg_temp
as $$
  with days as (
    select generate_series(current_date - p_days, current_date, interval '1 day')::date as d
  ),
  perday as (
    select days.d,
      (select avg(lt.tgt) from (
         select distinct on (r.analyst_house) r.new_target as tgt
         from tp_revisions r
         where r.ticker = p_ticker
           and r.revision_date <= days.d
           and r.revision_date > days.d - p_stale_days
         order by r.analyst_house, r.revision_date desc, r.published_at desc, r.id desc
      ) lt) as consensus,
      (select count(distinct r.analyst_house) from tp_revisions r
         where r.ticker = p_ticker and r.revision_date <= days.d
           and r.revision_date > days.d - p_stale_days) as houses_live
    from days
  ),
  pts as (select d, consensus from perday where consensus is not null order by d),
  d1 as (
    select d, (d - lag(d) over (order by d))::numeric as dt,
      (consensus - lag(consensus) over (order by d))
        / nullif((d - lag(d) over (order by d))::numeric, 0) as v
    from pts
  ),
  d2 as (
    select d, (v - lag(v) over (order by d))
      / nullif((dt + lag(dt) over (order by d)) / 2.0, 0) as a
    from d1 where v is not null
  ),
  viktat as (
    select a, greatest(p_days - (current_date - d), 1)::numeric as vikt
    from d2 where a is not null
  ),
  rev as (
    select e.analyst_house, e.delta_abs, e.delta_pct from tp_revisions_enriched e
    where e.ticker = p_ticker and e.revision_date > current_date - p_days
      and e.delta_abs is not null and e.delta_abs <> 0
  ),
  meta as (
    select count(*)::int as nrev, count(distinct analyst_house)::int as nhouses,
      case when sum(abs(delta_abs)) > 0
        then round(max(abs(delta_abs)) / sum(abs(delta_abs)) * 100, 1) end as largest,
      round(sum(delta_pct), 2) as net_delta     -- null när 0 revisioner (sum över tomt = null)
    from rev
  )
  select
    -- NOLLDATA-SPÄRR: ingen revision i fönstret → null (aldrig 0,0)
    case when coalesce((select nrev from meta), 0) >= 1
           and (select count(*) from viktat) >= 1
           and (select sum(vikt) from viktat) > 0
      then round((select sum(a * vikt) from viktat) / (select sum(vikt) from viktat), 6)
    end as acceleration,
    (select consensus from perday where d = current_date) as current_consensus,
    coalesce((select nrev from meta), 0) as n_revisions,
    coalesce((select nhouses from meta), 0) as n_houses,
    coalesce((select houses_live from perday where d = current_date), 0) as n_houses_live,
    (select largest from meta) as largest_single_contribution_pct,
    (select net_delta from meta) as net_delta_pct,
    (select count(*) from pts)::int as points;
$$;

-- vyn tillbaka till 0003 (net_delta_pct + source_stale_days, ingen n_reinit)
create view tp_acceleration_current as
  select
    t.ticker,
    a.acceleration,
    a.current_consensus,
    a.n_revisions,
    a.n_houses,
    a.n_houses_live,
    a.largest_single_contribution_pct,
    a.net_delta_pct,
    a.points,
    ac.analyst_count,
    ac.price_at_snapshot,
    case when ac.price_at_snapshot > 0 and a.current_consensus is not null
      then round((a.current_consensus / ac.price_at_snapshot - 1) * 100, 1) end as upside_pct,
    -- KÄLLFÄRSKHET: dagar sedan tickerns senaste revision (skiljer tyst från släpande källa)
    (current_date - (select max(r2.revision_date) from tp_revisions r2 where r2.ticker = t.ticker)) as source_stale_days,
    ac.as_of_date
  from (select distinct ticker from tp_revisions) t
  cross join lateral get_tp_acceleration(t.ticker) a
  left join lateral (
    select analyst_count, price_at_snapshot, as_of_date
    from consensus_snapshots c where c.ticker = t.ticker
    order by as_of_date desc limit 1
  ) ac on true;

grant execute on function get_tp_acceleration(text, integer, integer) to anon, authenticated, service_role;
grant select on tp_acceleration_current to anon, authenticated, service_role;

notify pgrst, 'reload schema';
