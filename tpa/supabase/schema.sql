-- ============================================================================
-- DATAPANEL — Supabase-schema för backend-motorn (v1)
-- Körs idempotent: hela filen kan köras om utan bieffekter.
-- Frontend för panelen byggs separat; detta är datalagret den kommer läsa ur.
--
--   macro_regimes        → dagliga makrokvoter (HYG/TLT, SOX/SPY, HG1/XAU)
--   consensus_snapshots  → riktkurser & analytikerdata per ticker och dag
--   get_target_price_acceleration → tidsviktad andra-derivata av riktkursen
-- ============================================================================

-- ---------- Gemensam updated_at-trigger ----------
create or replace function set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ============================================================================
-- macro_regimes — en rad per handelsdag
-- Kvoterna lagras färdigberäknade OCH med underliggande stängningar,
-- så att historiken kan räknas om om en källa visar sig felaktig.
-- ============================================================================
create table if not exists macro_regimes (
  id             bigint generated always as identity primary key,
  as_of_date     date not null unique,

  -- kvoter (kreditaptit / AI-cykel / tillväxt-mot-skydd)
  hyg_tlt_ratio  numeric(14,6),
  sox_spy_ratio  numeric(14,6),
  hg1_xau_ratio  numeric(14,6),

  -- underliggande stängningar (spårbarhet / omräkning)
  hyg_close      numeric(14,4),
  tlt_close      numeric(14,4),
  sox_close      numeric(14,4),   -- ^SOX-index
  spy_close      numeric(14,4),
  hg_close       numeric(14,4),   -- kopparfront HG=F, USD/lb
  gold_close     numeric(14,4),   -- guldfront GC=F, USD/oz

  source         text not null default 'yahoo',
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

drop trigger if exists trg_macro_regimes_updated on macro_regimes;
create trigger trg_macro_regimes_updated
  before update on macro_regimes
  for each row execute function set_updated_at();

comment on table macro_regimes is
  'Dagliga makrokvoter. En rad per handelsdag; ingest-skriptet UPSERT:ar på as_of_date.';

-- ============================================================================
-- consensus_snapshots — riktkurser & analytikerdata, en rad per ticker och dag
-- ============================================================================
create table if not exists consensus_snapshots (
  id                bigint generated always as identity primary key,
  ticker            text not null,
  as_of_date        date not null,

  -- riktkurser (FMP price-target-consensus)
  target_consensus  numeric(14,4),
  target_median     numeric(14,4),
  target_high       numeric(14,4),
  target_low        numeric(14,4),

  -- analytikerbild (null när endpointen inte ingår i API-planen)
  analyst_count     integer,        -- antal riktkurser senaste kvartalet (FMP price-target-summary)
  strong_buy        integer,
  buy               integer,
  hold              integer,
  sell              integer,
  strong_sell       integer,

  price_at_snapshot numeric(14,4),  -- kurs vid ögonblicksbilden (för TP-uppsida)

  source            text not null default 'fmp',
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),

  unique (ticker, as_of_date)
);

create index if not exists idx_consensus_ticker_datum
  on consensus_snapshots (ticker, as_of_date desc);

drop trigger if exists trg_consensus_updated on consensus_snapshots;
create trigger trg_consensus_updated
  before update on consensus_snapshots
  for each row execute function set_updated_at();

comment on table consensus_snapshots is
  'Daglig ögonblicksbild av analytikerkonsensus per ticker. UPSERT på (ticker, as_of_date).';

-- ============================================================================
-- get_target_price_acceleration(ticker, dagar)
-- Tidsviktad andra-derivata av konsensusriktkursen: d²TP/dt².
--
-- Metod:
--   1. Hämta snapshots i fönstret (default 30 dagar).
--   2. Första derivatan v = ΔTP/Δt mellan på varandra följande punkter (TP/dag).
--   3. Andra derivatan a = Δv / medelintervallet ((Δt₁+Δt₂)/2) (TP/dag²).
--   4. Tidsvikta: vikt = (fönster − ålder i dagar), golvat till 1 — färska
--      accelerationer väger linjärt tyngre. Returnera viktat medel.
--
-- Returnerar NULL vid färre än 3 observationer — hellre inget svar än ett hittat.
-- Enhet: riktkursenheter per dag². Positivt = upprevideringarna accelererar,
-- negativt = de bromsar in eller vänder ned.
-- ============================================================================
create or replace function get_target_price_acceleration(
  p_ticker text,
  p_days   integer default 30
)
returns numeric
language sql
stable
as $$
  with pts as (
    select as_of_date, target_consensus
    from consensus_snapshots
    where ticker = p_ticker
      and target_consensus is not null
      and as_of_date >= current_date - p_days
    order by as_of_date
  ),
  d1 as (
    select
      as_of_date,
      (as_of_date - lag(as_of_date) over (order by as_of_date))::numeric as dt,
      (target_consensus - lag(target_consensus) over (order by as_of_date))
        / nullif((as_of_date - lag(as_of_date) over (order by as_of_date))::numeric, 0) as v
    from pts
  ),
  d2 as (
    select
      as_of_date,
      (v - lag(v) over (order by as_of_date))
        / nullif((dt + lag(dt) over (order by as_of_date)) / 2.0, 0) as a
    from d1
    where v is not null
  ),
  viktat as (
    select
      a,
      greatest(p_days - (current_date - as_of_date), 1)::numeric as vikt
    from d2
    where a is not null
  )
  select case
    when count(*) >= 1 and sum(vikt) > 0 then round(sum(a * vikt) / sum(vikt), 6)
    else null
  end
  from viktat;
$$;

comment on function get_target_price_acceleration(text, integer) is
  'Tidsviktad d²TP/dt² över p_days dagar. NULL vid < 3 snapshots. Enhet: TP-enheter/dag².';

-- ============================================================================
-- Row Level Security: läsning öppen (panelen läser med anon-nyckeln),
-- skrivning endast via service role-nyckeln (ingest-skriptet) som kringgår RLS.
-- ============================================================================
alter table macro_regimes enable row level security;
alter table consensus_snapshots enable row level security;

drop policy if exists "las_macro_regimes" on macro_regimes;
create policy "las_macro_regimes" on macro_regimes
  for select using (true);

drop policy if exists "las_consensus_snapshots" on consensus_snapshots;
create policy "las_consensus_snapshots" on consensus_snapshots
  for select using (true);

-- ============================================================================
-- Behörigheter: nya Supabase-projekt (sb_publishable/sb_secret-nycklar) delar
-- inte ut tabellrättigheter automatiskt — utan detta svarar PostgREST
-- "42501 permission denied". Läsning: anon + authenticated (RLS-policyerna
-- ovan gäller). Skrivning: endast service_role (ingest-skriptet).
-- ============================================================================
grant usage on schema public to anon, authenticated, service_role;
grant select on macro_regimes, consensus_snapshots to anon, authenticated;
grant select, insert, update, delete on macro_regimes, consensus_snapshots to service_role;
grant usage, select on all sequences in schema public to service_role;
