-- ============================================================================
-- Migration 0001 UP — rådata per hus + carry-forward-acceleration
--
-- Inför lagring av VARJE riktkursrevision som egen rad (tp_revisions) i stället
-- för bara det dagliga aggregatet (consensus_snapshots). Aggregatet BERÄKNAS
-- härefter ur rådatat via carry-forward, inte lagras separat.
--
-- Idempotent: hela filen kan köras om. Reversibel via 0001_tp_revisions_down.sql.
-- Kör i Supabase SQL-editorn med service-rollen (DDL går inte via PostgREST).
-- ============================================================================

-- ---------- 1. Husnormalisering: alias-tabell + konservativ funktion ----------
-- alias_key = rånamnet strippat till gemener + alfanumeriskt, så "M.S.", "MS"
-- och "Morgan Stanley " alla slår mot samma rad. canonical = visningsnamnet.
-- ENDAST explicit uppräknade sammanslagningar mergas — okända namn behålls som
-- de är (aldrig gissa att två hus är samma).
create table if not exists analyst_house_alias (
  alias_key  text primary key,
  canonical  text not null,
  noterat    text
);

comment on table analyst_house_alias is
  'Kända husnamnsvarianter → kanoniskt namn. Väx tabellen när nya varianter dyker upp; strippa aldrig aggressivt i kod.';

insert into analyst_house_alias (alias_key, canonical, noterat) values
  ('morganstanley',            'Morgan Stanley',   'MS/Morgan Stanley & Co'),
  ('ms',                       'Morgan Stanley',   null),
  ('jpmorgan',                 'J.P. Morgan',      'JPMorgan/J.P. Morgan/JPM'),
  ('jpmorganchase',            'J.P. Morgan',      null),
  ('jpm',                      'J.P. Morgan',      null),
  ('bofasecurities',           'BofA Securities',  'Bank of America/BofA/Merrill'),
  ('bankofamericasecurities',  'BofA Securities',  null),
  ('bankofamerica',            'BofA Securities',  null),
  ('merrilllynch',             'BofA Securities',  null),
  ('tdcowen',                  'TD Cowen',         'Cowen & Co/Cowen'),
  ('cowen',                    'TD Cowen',         null),
  ('cowenco',                  'TD Cowen',         null),
  ('goldmansachs',             'Goldman Sachs',    'GS'),
  ('gs',                       'Goldman Sachs',    null),
  ('rbccapitalmarkets',        'RBC Capital',      'RBC'),
  ('rbc',                      'RBC Capital',      null),
  ('rbccapital',               'RBC Capital',      null),
  ('ubs',                      'UBS',              null),
  ('ubsgroup',                 'UBS',              null),
  ('wellsfargo',               'Wells Fargo',      null),
  ('wellsfargosecurities',     'Wells Fargo',      null),
  ('deutschebank',             'Deutsche Bank',    null),
  ('citigroup',                'Citigroup',        'Citi'),
  ('citi',                     'Citigroup',        null),
  ('barclays',                 'Barclays',         null),
  ('evercoreisi',              'Evercore ISI',     'Evercore'),
  ('evercore',                 'Evercore ISI',     null)
on conflict (alias_key) do update set canonical = excluded.canonical, noterat = excluded.noterat;

create or replace function house_key(raw text)
returns text language sql immutable as $$
  select regexp_replace(lower(btrim(coalesce(raw, ''))), '[^a-z0-9]', '', 'g');
$$;

-- Kanoniskt husnamn: alias-träff → canonical; annars rånamnet trimmat (oförändrat).
create or replace function normalize_house(raw text)
returns text language sql stable as $$
  select coalesce(
    (select canonical from analyst_house_alias where alias_key = house_key(raw)),
    nullif(btrim(coalesce(raw, '')), '')
  );
$$;

-- ---------- 2. Rådata: en rad per revision ----------
-- Lagrar de IRREDUCIBLA fakta FMP ger. old_target/delta HÄRLEDS i vyn nedan
-- (via lag) — inte frysta här, så backfill som fyller en lucka självrättar
-- "föregående" för efterföljande poster. Samma lineage-princip som resten av
-- repot: härlett räknas alltid fram, aldrig fastlagt.
create table if not exists tp_revisions (
  id                bigint generated always as identity primary key,
  ticker            text not null,
  analyst_house     text not null,                 -- normaliserat (normalize_house)
  analyst_name      text,
  revision_date     date not null,
  published_at      timestamptz,                   -- full tidsstämpel för ordning inom dag
  new_target        numeric(14,4) not null,
  fmp_prior_target  numeric(14,4),                 -- oftast null på Starter (planen ger ej prior)
  price_when_posted numeric(14,4),
  currency          text not null default 'USD',
  source            text not null default 'FMP',
  fetched_at        timestamptz not null default now(),
  unique (ticker, analyst_house, revision_date, new_target)  -- idempotent
);

create index if not exists idx_tp_rev_ticker_datum on tp_revisions (ticker, revision_date desc);
create index if not exists idx_tp_rev_hus on tp_revisions (ticker, analyst_house, revision_date);

comment on table tp_revisions is
  'En rad per riktkursrevision per hus. Aggregat beräknas härur (carry-forward). Unik nyckel gör ingest idempotent.';

-- ---------- 3. Berikad vy: old_target/delta/proveniens via lag ----------
-- old_target = FMP:s prior om den finns (kalla='kalla'), annars samma hus
-- föregående new_target (kalla='harledd'). Första posten per hus: old_target
-- null, delta null, kalla null. Reiteration = samma mål igen (delta = 0).
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
      and r.new_target = coalesce(r.fmp_prior_target, lag(r.new_target) over w)) as is_reiteration
  from tp_revisions r
  window w as (partition by r.ticker, r.analyst_house order by r.revision_date, r.published_at, r.id);

-- ---------- 4. Acceleration ur rådata (carry-forward, stale-fönster) ----------
-- Bevarar EXAKT get_target_price_acceleration-formeln (tidsviktad d²TP/dt²) men
-- matar den med en carry-forward-konsensus byggd ur rådatat:
--   konsensus(dag) = medel över hus av husets SENASTE new_target med
--   revision_date <= dag OCH revision_date > dag - p_stale_days.
-- p_stale_days (default 180) är i praktiken en formelparameter: ett hus vars
-- senaste mål är äldre än fönstret bärs INTE med (döda mål ska inte driva
-- konsensusen). Se README.
create or replace function get_tp_acceleration(
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
  points                           integer    -- konsensuspunkter i serien
)
language sql stable as $$
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

comment on function get_tp_acceleration(text, integer, integer) is
  'Tidsviktad d²TP/dt² ur carry-forward-konsensus (stale-fönster p_stale_days). null vid 0 revisioner i fönstret. Enhet: TP/dag².';

-- ---------- 5. Panelvy: en rad per ticker med acc + metadata + uppsida ----------
create or replace view tp_acceleration_current as
  select
    t.ticker,
    a.acceleration,
    a.current_consensus,
    a.n_revisions,
    a.n_houses,
    a.n_houses_live,
    a.largest_single_contribution_pct,
    a.points,
    ac.analyst_count,
    ac.price_at_snapshot,
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

comment on view tp_acceleration_current is
  'Panelens läskälla: acc + n_houses + kluster-metrik + uppsida per ticker, allt beräknat ur rådata.';

-- ---------- 6. consensus_snapshots blir arkiv (epoch-markering) ----------
alter table consensus_snapshots
  add column if not exists epoch text not null default 'pre-raw';
comment on column consensus_snapshots.epoch is
  'pre-raw = aggregat innan rådata fanns (får ALDRIG blandas med carry-forward-serien). parity = parallellperiod för diffning.';

-- ---------- 7. Ingest-anomalier: datakvalitetsfel synliga, inte tysta ----------
create table if not exists ingest_anomalies (
  id          bigint generated always as identity primary key,
  upptackt_at timestamptz not null default now(),
  ticker      text,
  typ         text not null,       -- t.ex. 'upside_utan_analytiker'
  detalj      text
);
comment on table ingest_anomalies is
  'Loggar rader ingesten avvisade (t.ex. uppsida beräkningsbar men analytiker=0). Ska synas, inte tyst passera.';

-- ---------- 8. RLS + grants (speglar consensus_snapshots) ----------
alter table tp_revisions          enable row level security;
alter table analyst_house_alias   enable row level security;
alter table ingest_anomalies      enable row level security;

drop policy if exists "las_tp_revisions" on tp_revisions;
create policy "las_tp_revisions" on tp_revisions for select using (true);
drop policy if exists "las_house_alias" on analyst_house_alias;
create policy "las_house_alias" on analyst_house_alias for select using (true);

grant select on tp_revisions, analyst_house_alias, tp_revisions_enriched, tp_acceleration_current to anon, authenticated;
grant select, insert, update, delete on tp_revisions, analyst_house_alias, ingest_anomalies to service_role;
grant execute on function normalize_house(text), house_key(text), get_tp_acceleration(text, integer, integer) to anon, authenticated, service_role;
grant usage, select on all sequences in schema public to service_role;
