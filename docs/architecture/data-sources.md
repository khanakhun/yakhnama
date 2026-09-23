# Data sources

Every dataset Yakhnama ingests is an entry in `data/reference/datasets.yaml`
(validated by `DatasetReferenceFile`) with its licence recorded **before** the first
run, one `SourceAdapter` in `src/yakhnama/modules/ingestion/infrastructure/adapters/`
registered under its adapter name, and one `IngestionPipeline` subclass registered
under the same name (`docs/architecture/ingestion.md`, skill `add-source-adapter`).

No live network calls are made in this phase. The only implemented source is a
synthetic fixture; every other source below is **documented, not implemented**.

## Implemented

### `fixture.temperature_sample` (synthetic)

| Field | Value |
|-------|-------|
| Publisher | Yakhnama fixtures (synthetic, no real observations) |
| Licence | `CC0-1.0`, attribution "Synthetic fixture, no real observations" |
| File | `data/fixtures/ingestion/temperature_sample.csv` (UTF-8, LF, one header line) |
| Adapter | `local_csv_temperature`: `LocalCsvSourceAdapter` + `FixturePathResolver` |
| Pipeline | `TemperatureCsvPipeline` |
| Cadence | `static` |
| Feeds | Observations: `air_temperature` in kelvin, station-based, hour precision |
| Content | 3 fictitious stations `FIX-HUNZA-01..03` inside 74-75 E / 36-36.5 N, 2000-3000 m, 48 hourly values each (144 rows), one missing value, one `suspect` flag |
| Registration | `reference_adapters(fixtures_dir, clock=...)` and `reference_pipelines()` in `adapters/reference.py` |

**Where the file comes from.** `LocalCsvSourceAdapter` maps the dataset code to a
path relative to the configured fixtures directory. `Dataset.homepage_url` is not used
because it is an `http`/`https` URL for readers, and letting catalog data name a file
would turn a catalog edit into a file read. Traversal is refused when the mapping is
built (no absolute paths, no `.`, `..` or empty parts, no backslash) and again after
symbolic links are resolved at read time. Files over 10 MiB are refused (proposed
operational default). Reads run in a worker thread so the event loop is not blocked.

**Column contract.** `station_code,station_name,longitude,latitude,elevation_m,
observed_at,air_temperature_c,quality`. `observed_at` must carry a UTC offset (the
fixture uses `+05:00`) and is stored in UTC at `hour` precision. A header that differs
fails the run. A row with the wrong number of fields is rejected at `parse`.

**Conversion.** Kelvin = °C + 273.15 in decimal arithmetic, rounded half-even to
3 decimals (1 mK). A value below absolute zero, or one that is not finite, is rejected.

**Quality mapping.** `good`, `suspect`, `missing` and `estimated` map one to one. An
unknown code maps to `suspect` with a warning. An empty value is stored as `missing`
with `value: null`, with a warning if the code said otherwise. A value whose code is
`missing` is rejected as contradictory.

**Duplicates.** The template default applies: the first row wins. Re-running a version
stores nothing twice (`append_many` is idempotent on the natural key).

## Candidate sources (documented, not implemented)

Licence, format and cadence are as commonly described and **must be confirmed from the
publisher's own documentation** before an entry is added to `datasets.yaml`. Nothing
here is a recorded licence.

| Source | Publisher | Licence (to confirm) | Format (to confirm) | Cadence (to confirm) | Would feed | Status |
|--------|-----------|----------------------|---------------------|----------------------|-----------|--------|
| Copernicus Sentinel-1 (SAR) | European Commission / ESA (Copernicus) | Copernicus Sentinel data terms (free, full and open; attribution required) | SAFE products; COG via third-party catalogs | Revisit of several days, depending on constellation status | Rasters (STAC catalog entries, flood and mass-movement mapping) | documented, not implemented |
| Copernicus Sentinel-2 (optical) | European Commission / ESA (Copernicus) | Copernicus Sentinel data terms | SAFE / JPEG2000; COG via third-party catalogs | Revisit of about five days with two satellites | Rasters (glacial lakes, debris flows, snow cover) | documented, not implemented |
| Landsat 8/9 | USGS / NASA | U.S. public domain (USGS attribution requested) | Collection 2 COG with STAC metadata | Combined revisit of about eight days | Rasters (long-term change, lake area) | documented, not implemented |
| MODIS (Terra/Aqua) | NASA (LP DAAC, NSIDC) | NASA open data policy | HDF-EOS | Daily | Rasters (snow cover, land surface temperature); gridded observations | documented, not implemented |
| ERA5-Land | ECMWF / Copernicus Climate Change Service | Copernicus licence (attribution required) | GRIB / NetCDF | Hourly values, released with a delay | Gridded observations (`air_temperature`, `precipitation_depth`, `snow_depth`) | documented, not implemented |
| GLIMS glacier inventory | GLIMS / NSIDC | To confirm per contributed dataset | Shapefile / GeoPackage | Irregular releases | Geography (glacier outlines, GLIMS ids linked to events) | documented, not implemented |
| ICIMOD glacier and glacial lake inventories | ICIMOD | To confirm per dataset (ICIMOD Regional Database System terms) | Shapefile / tables | Irregular releases | Geography (glaciers, glacial lakes, GLOF-prone lakes) | documented, not implemented |
| OCHA COD for Pakistan (via HDX) | OCHA; source agencies named on HDX | To confirm on the HDX dataset page | Shapefile / GeoPackage / XLSX | Irregular updates | Geography (administrative boundaries, open question Q1) | documented, not implemented |
| UNDP GLOF-II project data | UNDP Pakistan / Ministry of Climate Change | Only where a licence is published; otherwise not ingested | To confirm | To confirm | Observations (early-warning stations); geography (monitored lakes) | documented, not implemented |
| PMD station data | Pakistan Meteorological Department | To confirm; availability and terms unknown | To confirm | To confirm | Observations (station `air_temperature`, `precipitation_depth`) | documented, not implemented |

Before any candidate is implemented, it needs:

1. The licence copied exactly (SPDX id or custom text, URL, attribution) into a
   `datasets.yaml` entry with a `source` citing the publisher's documentation.
2. A variable mapping to `VARIABLES`, or a proposed new variable with its SI unit.
3. The publisher's quality vocabulary mapped to `QualityFlag`, and the duplicate rule
   taken from the publisher's documentation.
4. An adapter with `httpx`, explicit timeouts, a size cap and retries from the
   composition root, tested against fixtures only (no live calls).
5. A security review of the new outbound integration.
