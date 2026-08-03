-- ============================================================================
-- Migration 0002 UP — rättar rättigheterna från 0001.
--
-- Två fel i 0001:s grants-block:
--   FEL 1 (blockerande): service_role fick DML på råtabellerna men ALDRIG
--     SELECT på vyerna. parity_check.js/backfill kör som service_role och
--     nekas därför "permission denied for view tp_acceleration_current".
--   FEL 2 (läckage): anon/authenticated fick läsa RÅTABELLERNA (tp_revisions,
--     analyst_house_alias) och den per-hus-detaljerade vyn. Panelen behöver
--     bara den aggregerade vyn tp_acceleration_current. Minsta-privilegium:
--     publishable/anon ser den vyn, aldrig råraderna.
--
-- För att FEL 2 ska kunna rättas UTAN att panelen går sönder vid TPA_KALLA='raw'
-- måste get_tp_acceleration bli SECURITY DEFINER: annars läser funktionen
-- tp_revisions som anropande roll (anon), och då krävs råtabell-grant igen.
-- Som DEFINER läser den råtabellen som ägaren och vyn blir rättighetsgränsen.
-- search_path pinnas (public, pg_temp) — standardhärdning för DEFINER-funktioner.
--
-- Kör i Supabase SQL-editorn med service-rollen (samma som 0001).
-- ============================================================================

-- ---------- FEL 1: service_role måste kunna läsa vyerna ----------
grant select on tp_revisions_enriched, tp_acceleration_current to service_role;

-- ---------- Gör aggregatfunktionen till rättighetsgräns ----------
-- Kör som ägare så den aggregerade vyn kan läsas utan direkt råtabell-grant.
alter function get_tp_acceleration(text, integer, integer) security definer;
alter function get_tp_acceleration(text, integer, integer) set search_path = public, pg_temp;

-- ---------- FEL 2: dra tillbaka anon/authenticated från råraderna ----------
-- Panelen läser bara tp_acceleration_current (behåller sin SELECT från 0001).
-- Vyn körs som ägare (security_invoker=false, default) och funktionen som
-- DEFINER ovan, så bortdraget bryter INTE panelen — det stänger bara den
-- direkta råtabells-vägen för publishable-nyckeln.
revoke select on tp_revisions        from anon, authenticated;
revoke select on analyst_house_alias from anon, authenticated;
revoke select on tp_revisions_enriched from anon, authenticated;

-- tp_acceleration_current: anon/authenticated behåller SELECT från 0001 (panelens
-- enda läsyta). service_role fick den ovan. Inget mer behövs här.

-- Be PostgREST läsa om schemat (Supabase gör detta automatiskt vid DDL via
-- event-trigger, men manuell körning i SQL-editorn kan behöva en knuff).
notify pgrst, 'reload schema';
