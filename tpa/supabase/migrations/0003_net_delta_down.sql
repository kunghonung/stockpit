-- ============================================================================
-- Migration 0003 DOWN — återställer funktionen/vyn till läget EFTER 0002
-- (utan net_delta_pct/source_stale_days, med SECURITY DEFINER + search_path kvar).
-- Kör i SQL-editorn med service-rollen.
-- ============================================================================

drop view if exists tp_acceleration_current;
drop function if exists get_tp_acceleration(text, integer, integer);

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
    select e.analyst_house, e.delta_abs from tp_revisions_enriched e
    where e.ticker = p_ticker and e.revision_date > current_date - p_days
      and e.delta_abs is not null and e.delta_abs <> 0
  ),
  meta as (
    select count(*)::int as nrev, count(distinct analyst_house)::int as nhouses,
      case when sum(abs(delta_abs)) > 0
        then round(max(abs(delta_abs)) / sum(abs(delta_abs)) * 100, 1) end as largest
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
    (select count(*) from pts)::int as points;
$$;

create view tp_acceleration_current as
  select
    t.ticker, a.acceleration, a.current_consensus, a.n_revisions, a.n_houses,
    a.n_houses_live, a.largest_single_contribution_pct, a.points,
    ac.analyst_count, ac.price_at_snapshot,
    case when ac.price_at_snapshot > 0 and a.current_consensus is not null
      then round((a.current_consensus / ac.price_at_snapshot - 1) * 100, 1) end as upside_pct,
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
