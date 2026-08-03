-- ============================================================================
-- Migration 0004 UP — REINIT-klassificering (rättar stale-prior-föroreningen).
--
-- BUGG: net_delta summerade delta_pct där old_target härleds via lag UTAN
-- färskhetskrav. En juli-revision mot ett hus vars förra post är från 2021/2022
-- ger ett enormt "delta" som är ÅTERINITIERING av täckning, inte en riktnings-
-- ändring (META Susquehanna $140→$650 = +364 % vände hela summan från −72 % till
-- +366 %). Fönstret filtrerade revision_date men aldrig priorns ålder.
--
-- FIX: en revision vars härledda prior är äldre än p_reinit_days (default 365)
-- är REINIT — exkluderas ur net_delta/n_revisions/n_houses/kluster, räknas i
-- n_reinit så täckningsexpansion syns. p_reinit_days är FRIKOPPLAD från carry-
-- forwardens p_stale_days (180): halvårskadens (t.ex. META:s sänkningskluster,
-- prior 182–304 d) är en FÄRSK revision, inte reinit — bara fleråriga fossiler
-- exkluderas. Verifierat mot riktig data (META −72,1 %, ASML +68,5 %).
--
-- acc och dess carry-forward är OFÖRÄNDRADE — stale-fönstret (180 d) skyddar dem
-- redan (det inaktuella målet bärs aldrig i konsensusen). Acc-guarden räknar
-- fortfarande ALLA fönsterrevisioner, precis som förr.
--
-- Kräver drop+recreate av funktion (ny returkolumn + ny parameter) och vy.
-- SECURITY DEFINER + search_path + grants återställs. Kör i SQL-editorn (service).
-- ============================================================================

-- ---------- 1. enriched: exponera härledda priorns datum (additivt) ----------
create or replace view tp_revisions_enriched as
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
      and r.new_target = coalesce(r.fmp_prior_target, lag(r.new_target) over w)) as is_reiteration,
    lag(r.revision_date) over w as prior_date       -- NY: härledda priorns datum (reinit-klass)
  from tp_revisions r
  window w as (partition by r.ticker, r.analyst_house order by r.revision_date, r.published_at, r.id);

-- ---------- 2. funktionen: reinit-klass + n_reinit + p_reinit_days ----------
drop view if exists tp_acceleration_current;
drop function if exists get_tp_acceleration(text, integer, integer);

create function get_tp_acceleration(
  p_ticker      text,
  p_days        integer default 30,
  p_stale_days  integer default 180,
  p_reinit_days integer default 365      -- reinit-tröskel, FRIKOPPLAD från p_stale_days
)
returns table (
  acceleration                     numeric,
  current_consensus                numeric,
  n_revisions                      integer,   -- FÄRSKA delta<>0 i fönstret (reinit + reiteration räknas ej)
  n_houses                         integer,   -- unika hus bakom de färska revisionerna
  n_houses_live                    integer,   -- hus med levande mål idag (konsensusens bredd)
  largest_single_contribution_pct  numeric,   -- största enskilda |delta| / total |delta| (färska)
  net_delta_pct                    numeric,   -- SUMMAN av delta_pct för färska revisioner (d¹)
  n_reinit                         integer,   -- revisioner exkluderade som täckningsåterinitiering
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
  klass as (   -- alla delta<>0-revisioner i fönstret, klassade som reinit eller färsk
    select e.analyst_house, e.delta_abs, e.delta_pct,
      (e.old_target_kalla = 'harledd' and e.prior_date is not null
        and (e.revision_date - e.prior_date) > p_reinit_days) as is_reinit
    from tp_revisions_enriched e
    where e.ticker = p_ticker and e.revision_date > current_date - p_days
      and e.delta_abs is not null and e.delta_abs <> 0
  ),
  rev as (select analyst_house, delta_abs, delta_pct from klass where not is_reinit),
  meta as (
    select count(*)::int as nrev, count(distinct analyst_house)::int as nhouses,
      case when sum(abs(delta_abs)) > 0
        then round(max(abs(delta_abs)) / sum(abs(delta_abs)) * 100, 1) end as largest,
      round(sum(delta_pct), 2) as net_delta     -- null när 0 färska revisioner (sum över tomt = null)
    from rev
  ),
  reinit as (select count(*)::int as nreinit from klass where is_reinit)
  select
    -- NOLLDATA-SPÄRR: acc-guarden räknar ALLA fönsterrevisioner (klass) precis som förr —
    -- acc-formeln oförändrad; ingen revision i fönstret → null (aldrig 0,0)
    case when (select count(*) from klass) >= 1
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
    coalesce((select nreinit from reinit), 0) as n_reinit,
    (select count(*) from pts)::int as points;
$$;

comment on function get_tp_acceleration(text, integer, integer, integer) is
  'Tidsviktad d²TP/dt² (acc) + nettodelta% (d¹, färska revisioner) ur carry-forward. Reinit (härledd prior > p_reinit_days, default 365) exkluderas och räknas i n_reinit. acc/net_delta null vid 0 revisioner.';

-- ---------- 3. panelvy: + n_reinit ----------
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
    a.n_reinit,
    a.points,
    ac.analyst_count,
    ac.price_at_snapshot,
    case when ac.price_at_snapshot > 0 and a.current_consensus is not null
      then round((a.current_consensus / ac.price_at_snapshot - 1) * 100, 1) end as upside_pct,
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
  'Panelens läskälla: acc (d²) + nettodelta (d¹, färska) + n_houses + kluster + n_reinit + uppsida + källfärskhet per ticker.';

-- ---------- Återställ grants (0001/0002) för den nya signaturen ----------
grant execute on function get_tp_acceleration(text, integer, integer, integer) to anon, authenticated, service_role;
grant select on tp_acceleration_current to anon, authenticated, service_role;

notify pgrst, 'reload schema';
