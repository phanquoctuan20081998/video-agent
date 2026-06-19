import { feature as topoFeature, merge as topoMerge } from "topojson-client";
import type { Topology, GeometryCollection, GeometryObject } from "topojson-specification";
import type { Feature, FeatureCollection, MultiPolygon, Polygon } from "geojson";
import worldTopology from "world-atlas/countries-50m.json";
import worldCountries from "world-countries";
import { registerLocale, numericToAlpha2, getName } from "i18n-iso-countries";
import viLocale from "i18n-iso-countries/langs/vi.json";

registerLocale(viLocale);

type CountryProps = { name: string };
type CountryGeometry = GeometryObject<CountryProps>;

const topology = worldTopology as unknown as Topology<{
  countries: GeometryCollection<CountryProps>;
  land: GeometryCollection;
}>;

export type CountryFeature = Feature<Polygon | MultiPolygon, CountryProps>;

export const allCountriesFC: FeatureCollection<Polygon | MultiPolygon, CountryProps> = topoFeature(
  topology,
  topology.objects.countries
) as unknown as FeatureCollection<Polygon | MultiPolygon, CountryProps>;

export const countryFeatures: CountryFeature[] = allCountriesFC.features;

const padCcn3 = (id: string | number | undefined): string | undefined =>
  id === undefined ? undefined : String(id).padStart(3, "0");

const wcByCcn3 = new Map(worldCountries.map((c) => [c.ccn3, c]));

// Continent for atlas features that lack a ccn3 crosswalk match (disputed/unrecognized territories).
const CONTINENT_OVERRIDES: Record<string, string> = {
  Kosovo: "Europe",
  Somaliland: "Africa",
  "N. Cyprus": "Asia",
};

interface CountryEntry {
  feature: CountryFeature;
  geometry: CountryGeometry;
  ccn3?: string;
  continent?: string;
}

const geometryByAtlasName = new Map<string, CountryGeometry>(
  topology.objects.countries.geometries.map((g) => [(g.properties as CountryProps).name, g])
);

export const countryEntries: CountryEntry[] = countryFeatures.map((f) => {
  const atlasName = f.properties.name;
  const ccn3 = padCcn3(f.id);
  const wc = ccn3 ? wcByCcn3.get(ccn3) : undefined;
  return {
    feature: f,
    geometry: geometryByAtlasName.get(atlasName)!,
    ccn3,
    continent: wc?.region ?? CONTINENT_OVERRIDES[atlasName],
  };
});

const norm = (s: string) => s.trim().toLowerCase();

// Common Vietnamese exonyms for continents/regions used by the script-gen pipeline.
const VIETNAMESE_CONTINENT_ALIASES: Record<string, string> = {
  "châu phi": "africa",
  "châu á": "asia",
  "châu âu": "europe",
  "châu mỹ": "americas",
  "châu đại dương": "oceania",
  "bắc mỹ": "north america",
  "nam mỹ": "south america",
};

// Common Vietnamese exonyms for countries, mapped to their English common name.
const VIETNAMESE_COUNTRY_ALIASES: Record<string, string> = {
  "việt nam": "Vietnam",
  "trung quốc": "China",
  "ấn độ": "India",
  "nhật bản": "Japan",
  "hàn quốc": "South Korea",
  "triều tiên": "North Korea",
  "bắc triều tiên": "North Korea",
  "thái lan": "Thailand",
  lào: "Laos",
  campuchia: "Cambodia",
  "miến điện": "Myanmar",
  "mông cổ": "Mongolia",
  nga: "Russia",
  mỹ: "United States",
  "hoa kỳ": "United States",
  pháp: "France",
  đức: "Germany",
  "ý": "Italy",
  italia: "Italy",
  "tây ban nha": "Spain",
  "bồ đào nha": "Portugal",
  "hà lan": "Netherlands",
  "bỉ": "Belgium",
  "thụy sĩ": "Switzerland",
  "thụy điển": "Sweden",
  "na uy": "Norway",
  "đan mạch": "Denmark",
  "phần lan": "Finland",
  "ba lan": "Poland",
  ukraina: "Ukraine",
  "hy lạp": "Greece",
  "thổ nhĩ kỳ": "Turkey",
  "ai cập": "Egypt",
  "nam phi": "South Africa",
  "ma-rốc": "Morocco",
  maroc: "Morocco",
  "úc": "Australia",
  "ả rập xê út": "Saudi Arabia",
  iran: "Iran",
  cuba: "Cuba",
  "anh, vương quốc anh": "United Kingdom",
  "vương quốc anh": "United Kingdom",
  anh: "United Kingdom",
};

const countryAliasIndex = new Map<string, CountryEntry>();
for (const entry of countryEntries) {
  countryAliasIndex.set(norm(entry.feature.properties.name), entry);
  const wc = entry.ccn3 ? wcByCcn3.get(entry.ccn3) : undefined;
  if (wc) {
    countryAliasIndex.set(norm(wc.name.common), entry);
    countryAliasIndex.set(norm(wc.name.official), entry);
    for (const alt of wc.altSpellings ?? []) countryAliasIndex.set(norm(alt), entry);
  }
  // Official Vietnamese ISO name (i18n-iso-countries), covers all 250 ISO entries — full continent/country
  // coverage beyond the curated colloquial list below.
  const alpha2 = entry.ccn3 ? numericToAlpha2(entry.ccn3) : undefined;
  const viOfficial = alpha2 ? getName(alpha2, "vi") : undefined;
  if (viOfficial) countryAliasIndex.set(norm(viOfficial), entry);
}
// Short colloquial Vietnamese names that differ from the official ISO register (e.g. "Mỹ" vs.
// "Hợp chủng quốc Hoa Kỳ"), layered on top so both forms resolve.
for (const [vi, en] of Object.entries(VIETNAMESE_COUNTRY_ALIASES)) {
  const entry = countryAliasIndex.get(norm(en));
  if (entry) countryAliasIndex.set(norm(vi), entry);
}

type ContinentKey = "Africa" | "Asia" | "Europe" | "Americas" | "Oceania" | "North America" | "South America";

const CONTINENT_FILTERS: Record<ContinentKey, (e: CountryEntry) => boolean> = {
  Africa: (e) => e.continent === "Africa",
  Asia: (e) => e.continent === "Asia",
  Europe: (e) => e.continent === "Europe",
  Americas: (e) => e.continent === "Americas",
  Oceania: (e) => e.continent === "Oceania",
  "North America": (e) => {
    const wc = e.ccn3 ? wcByCcn3.get(e.ccn3) : undefined;
    return e.continent === "Americas" && ["North America", "Central America", "Caribbean"].includes(wc?.subregion ?? "");
  },
  "South America": (e) => {
    const wc = e.ccn3 ? wcByCcn3.get(e.ccn3) : undefined;
    return e.continent === "Americas" && wc?.subregion === "South America";
  },
};

const continentAliasToKey = new Map<string, ContinentKey>(
  (Object.keys(CONTINENT_FILTERS) as ContinentKey[]).map((k) => [norm(k), k])
);
for (const [vi, en] of Object.entries(VIETNAMESE_CONTINENT_ALIASES)) {
  const key = continentAliasToKey.get(norm(en));
  if (key) continentAliasToKey.set(norm(vi), key);
}

const continentOutlineCache = new Map<ContinentKey, Feature<MultiPolygon>>();

function getContinentOutline(key: ContinentKey): Feature<MultiPolygon> {
  const cached = continentOutlineCache.get(key);
  if (cached) return cached;
  const geometries = countryEntries.filter(CONTINENT_FILTERS[key]).map((e) => e.geometry);
  const merged = topoMerge(topology, geometries as unknown as Parameters<typeof topoMerge>[1]) as MultiPolygon;
  const result: Feature<MultiPolygon> = { type: "Feature", properties: {}, geometry: merged };
  continentOutlineCache.set(key, result);
  return result;
}

export type RegionMatch =
  | { kind: "country"; displayName: string; outline: CountryFeature }
  | { kind: "continent"; displayName: string; outline: Feature<MultiPolygon> };

export function resolveRegion(query: string): RegionMatch | null {
  const key = norm(query);

  const continentKey = continentAliasToKey.get(key);
  if (continentKey) {
    return { kind: "continent", displayName: continentKey, outline: getContinentOutline(continentKey) };
  }

  const country = countryAliasIndex.get(key);
  if (country) {
    return { kind: "country", displayName: country.feature.properties.name, outline: country.feature };
  }

  return null;
}
