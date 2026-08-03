-- ============================================================================
-- Migration 0003 UP — lägger d¹ bredvid d² i panelens läskälla.
--
-- Paritetskörningen visade att d² (acc) ensam vilseleder efter stegrörelser:
-- ett brant fall följt av platt läses som POSITIV böjning. Läsaren behöver
-- riktning (d¹) och böjning (d²) TILLSAMMANS. Två nya fält:
--
--   net_delta_pct     — summan av delta_pct för revisionerna i SAMMA 30d-fönster
--                       som acc (reiterationer delta=0 påverkar ej). null vid 0
--                       revisioner. Beräknas i get_tp_acceleration (samma rev-CTE
--                       som n_revisions) så fönstret ALDRIG kan driva isär.
--   source_stale_days — dagar sedan tickerns senaste revision (vilken som helst).
--                       Skiljer "analytikerna är tysta" (0 rev, färsk källa) från
--                       "källan täcker inte tickern" (0 rev, gammal källa). Sätts
--                       i vyn (källmetadata, inte fönsterberoende).
--
-- Att lägga en kolumn i RETURNS TABLE kräver drop+recreate av funktionen, och
-- vyn beror på den → drop vy först. SECURITY DEFINER + search_path (0002) och
-- grants (0001/0002) återställs sist eftersom drop tar bort dem.
-- Acc-FORMELN är oförändrad. Kör i SQL-editorn med service-rollen.
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
  n_revisions                      integer,   -- delta<>0 i fönstret (reiterationer räknas ej)
  n_houses                         integer,   -- unika hus bakom dessa revisioner
  n_houses_live                    integer,   -- hus med levande mål idag (konsensusens bredd)
  largest_single_contribution_pct  numeric,   -- största enskilda |delta| / total |delta|
  net_delta_pct                    numeric,   -- SUMMAN av delta_pct i fönstret (d¹, riktning)
  points                           integer    -- konsensuspunkter i serien
)
language sql stable
security definer                    -- från 0002: vyn är rättighetsgräns, läser råtabell som ägare
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

comment on function get_tp_acceleration(text, integer, integer) is
  'Tidsviktad d²TP/dt² (acc) + nettodelta% (d¹) ur carry-forward-konsensus, samma 30d-fönster. acc/net_delta null vid 0 revisioner. Enhet acc: TP/dag².';

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

comment on view tp_acceleration_current is
  'Panelens läskälla: acc (d²) + nettodelta (d¹) + n_houses + kluster-metrik + uppsida + källfärskhet per ticker, allt ur rådata.';

-- ---------- Återställ grants (0001) + service_role-läsning (0002) ----------
grant execute on function get_tp_acceleration(text, integer, integer) to anon, authenticated, service_role;
grant select on tp_acceleration_current to anon, authenticated, service_role;

notify pgrst, 'reload schema';
