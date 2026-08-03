-- ============================================================================
-- Migration 0001 DOWN — reverserar 0001_tp_revisions_up.sql
-- Rör INTE consensus_snapshots-datat; tar bara bort epoch-kolumnen och de nya
-- objekten. Kör i Supabase SQL-editorn med service-rollen.
-- ============================================================================
drop view if exists tp_acceleration_current;
drop function if exists get_tp_acceleration(text, integer, integer);
drop view if exists tp_revisions_enriched;
drop table if exists tp_revisions;
drop table if exists ingest_anomalies;
drop function if exists normalize_house(text);
drop function if exists house_key(text);
drop table if exists analyst_house_alias;
alter table consensus_snapshots drop column if exists epoch;
