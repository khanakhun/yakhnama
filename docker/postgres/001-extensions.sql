-- Extensions required by Yakhnama, created once when the data volume is initialised.
-- postgis: WGS84 geometries for places, reports and events.
-- pg_trgm: fuzzy matching of place names across spellings and scripts.
-- unaccent: accent-insensitive search on transliterated place names.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
