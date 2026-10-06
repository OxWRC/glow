import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  getDimensions,
  me,
  queryPeriodBased,
  type DimensionsResponse,
  type MeResponse,
  type NewQueryResponse,
  type SchoolSummary,
} from "../lib/api";
import {
  newQueryToChartData,
  newQueryToCSVWithLabels,
  newQueryToFacets,
  type FacetMode,
} from "../lib/chartUtils";
import { useAuth, useCurrentSchools } from "../auth/AuthContext";
import { ChartCard } from "../lib/components/ChartCard";
import { createI18n, availableLocales, type Locale } from "../lib/i18n";

// The old `[locale]/+page.svelte` carried a page-scoped <style> block that
// redefined `.card`, `h1` and `h2`. Svelte compiled those to `.card.svelte-hash`
// / `h1.svelte-hash` (specificity 0,2,0 and 0,1,1), so they beat BOTH app.css's
// global versions and the plain utility classes sitting next to them in the
// markup - verified against the old build output. What that page really
// rendered, and what this port reproduces rather than "fixing":
//   - `.card` is rounded-lg (the global `.card` in app.css is rounded-xl);
//   - every `card bg-red-50` / `card bg-yellow-50` / `card bg-gray-100` element
//     rendered white with a gray-200 border, the colour utility having lost;
//   - likewise `card … py-12` rendered at the rule's own 1.5rem padding, not
//     3rem - any utility the scoped rule also declares loses, whatever the
//     class attribute says;
//   - `<h1 class="text-3xl">` rendered at text-2xl, `<h2 class="text-lg">` at
//     text-xl/gray-900 (app.css's global h2 is gray-800).
// Unifying any of this is a separate job from the React port.
const CARD = "bg-white rounded-lg shadow-sm border border-gray-200 p-6";

export function DashboardPage() {
  const { locale: localeParam } = useParams<{ locale: string }>();
  const locale: Locale = availableLocales.includes(localeParam as Locale)
    ? (localeParam as Locale)
    : "en";
  const i18n = useMemo(() => createI18n(locale), [locale]);

  const { token } = useAuth();
  const currentSchools = useCurrentSchools();

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [meResponse, setMeResponse] = useState<MeResponse | null>(null);
  const [schools, setSchools] = useState<SchoolSummary[]>([]);
  const [selectedSchoolId, setSelectedSchoolId] = useState<number | null>(null);

  // Dimensions from the API
  const [dimensions, setDimensions] = useState<DimensionsResponse | null>(null);

  // Query options derived from dimensions
  const availableVariables = dimensions?.variables ?? [];
  const availableDimensions = dimensions?.dimensions ?? [];

  // Query parameters
  const [selectedVariables, setSelectedVariables] = useState<string[]>([]);
  const [selectedDimensions, setSelectedDimensions] = useState<string[]>([]);

  const [queryResult, setQueryResult] = useState<NewQueryResponse | null>(null);
  const [queryLoading, setQueryLoading] = useState(false);
  const [queryError, setQueryError] = useState<string | null>(null);
  const [facetMode, setFacetMode] = useState<FacetMode>("variable");

  // Mount only, like the old page's onMount: nothing in here is meant to re-run
  // when the token or the auth store's schools change later.
  useEffect(() => {
    async function bootstrap() {
      // Get user identity (works for both anonymous and authenticated)
      const identity = await me(token ?? undefined);
      setMeResponse(identity);

      // Svelte read `selectedSchoolId` back straight after assigning it; React
      // state isn't visible until the next render, so carry it in a local.
      let schoolId: number | null = null;

      // Extract schools from authenticated response
      if (identity.kind === "authenticated") {
        setSchools(identity.schools);

        // Pre-select user's first school if available
        if (currentSchools.length > 0) {
          schoolId = currentSchools[0].id;
        } else if (identity.schools.length > 0) {
          schoolId = identity.schools[0].id;
        }
        setSelectedSchoolId(schoolId);
      }

      // Fetch dimensions - works for both anonymous and authenticated.
      // If authenticated with a selected school, get school-specific dimensions
      const newDimensions = await getDimensions({
        school_id: schoolId ?? undefined,
        token: token ?? undefined,
      });
      setDimensions(newDimensions);

      // Set default variable if available
      if (newDimensions.variables.length > 0) {
        setSelectedVariables([newDimensions.variables[0].key]);
      }
    }

    bootstrap()
      .catch((e: unknown) => {
        setError(
          e instanceof Error ? e.message : i18n.t("dashboard.loadErrorHelp"),
        );
      })
      .finally(() => {
        setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Refetch dimensions when school selection changes. Deps are exactly the
  // values the old $effect read synchronously (Svelte doesn't track reads made
  // later inside .then/.catch): `loading`, `meResponse`, `selectedSchoolId` and
  // the auth token. `selectedVariables` is read only in the .then callback, so
  // it is NOT a dependency - it is updated through the functional form below,
  // which both matches Svelte's "read the live value" behaviour and keeps this
  // effect from re-running itself forever.
  useEffect(() => {
    if (loading || meResponse?.kind !== "authenticated") return;

    // Refetch dimensions for the new school scope
    getDimensions({
      school_id: selectedSchoolId ?? undefined,
      token: token ?? undefined,
    })
      .then((newDimensions) => {
        setDimensions(newDimensions);

        // Clear current selections if they're no longer valid
        setSelectedVariables((prev) => {
          if (prev.length === 0) return prev;
          const validVars = new Set(newDimensions.variables.map((v) => v.key));
          const filtered = prev.filter((v) => validVars.has(v));

          // If no variables remain selected, select first available
          if (filtered.length === 0 && newDimensions.variables.length > 0) {
            return [newDimensions.variables[0].key];
          }
          return filtered;
        });

        // Clear query results when school changes
        setQueryResult(null);
      })
      .catch((e: unknown) => {
        setError(
          e instanceof Error ? e.message : i18n.t("dashboard.loadErrorHelp"),
        );
      });
  }, [loading, meResponse, selectedSchoolId, token, i18n]);

  function toggleVariable(key: string) {
    setSelectedVariables((prev) =>
      prev.includes(key) ? prev.filter((v) => v !== key) : [...prev, key],
    );
  }

  function toggleDimension(key: string) {
    setSelectedDimensions((prev) =>
      prev.includes(key) ? prev.filter((d) => d !== key) : [...prev, key],
    );
  }

  async function executeQuery() {
    if (selectedVariables.length === 0) {
      setQueryError(i18n.t("explore.selectAtLeastOneVariable"));
      return;
    }

    setQueryLoading(true);
    setQueryError(null);
    setQueryResult(null);

    try {
      setQueryResult(
        await queryPeriodBased({
          v: selectedVariables,
          d: selectedDimensions,
          school_id: selectedSchoolId ?? undefined,
          token: token ?? undefined,
        }),
      );
    } catch (e: unknown) {
      setQueryError(
        e instanceof Error ? e.message : i18n.t("dashboard.loadErrorHelp"),
      );
    } finally {
      setQueryLoading(false);
    }
  }

  // Chart rendering using utility functions. The old page called
  // newQueryToChartData twice (once for the data, once for the type); one call
  // gives both.
  const chartOutput = useMemo(
    () =>
      queryResult
        ? newQueryToChartData(queryResult, i18n.chartFormatters)
        : null,
    [queryResult, i18n],
  );
  // Grouped trends: one line chart per variable (or per group) so the grouping
  // isn't flattened away.
  const facetOutput = useMemo(
    () =>
      queryResult &&
      queryResult.dimensions.length > 0 &&
      queryResult.periods.length > 1
        ? newQueryToFacets(
            queryResult,
            // The toggle is hidden for one variable; don't strand a stale mode
            queryResult.variables.length > 1 ? facetMode : "variable",
            i18n.chartFormatters,
          )
        : null,
    [queryResult, facetMode, i18n],
  );
  const chartData = chartOutput?.data ?? { labels: [], datasets: [] };
  const chartType = chartOutput?.type ?? "bar";
  const chartCSV = useMemo(
    () =>
      queryResult
        ? newQueryToCSVWithLabels(queryResult, i18n.chartFormatters)
        : "",
    [queryResult, i18n],
  );

  const variableLabels = useMemo(
    () => selectedVariables.map((v) => i18n.columnLabel(v)).join(", "),
    [selectedVariables, i18n],
  );

  const queryNotes = useMemo(() => {
    if (!queryResult) return [] as string[];

    let hasRescaled = false;
    let hasMultipleVersions = false;

    for (const variableSlice of queryResult.variables) {
      for (const periodSlice of Object.values(variableSlice.periods)) {
        if (periodSlice.notes?.includes("values-rescaled")) {
          hasRescaled = true;
        }
        if (
          periodSlice.question_versions &&
          Object.keys(periodSlice.question_versions).length > 1
        ) {
          hasMultipleVersions = true;
        }
      }
    }

    const notes: string[] = [];
    if (hasMultipleVersions) {
      notes.push(i18n.t("explore.multipleCompatibleVersionsNote"));
    }
    if (hasRescaled) {
      notes.push(i18n.t("explore.rescaledValuesNote"));
    }
    return notes;
  }, [queryResult, i18n]);

  const allSuppressed =
    queryResult !== null &&
    queryResult.variables.every((v) =>
      queryResult.periods.every((p) => v.periods[p]?.suppressed),
    );

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">
          {i18n.t("explore.title")}
        </h1>
        <p className="text-gray-500 mt-1">{i18n.t("explore.subtitle")}</p>
      </div>

      {loading ? (
        <div className={`${CARD} motion-safe:animate-pulse h-64`}></div>
      ) : error ? (
        <div className={CARD}>
          <p className="text-red-700">{error}</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Sidebar: Query Builder */}
          <div className="lg:col-span-1 space-y-4">
            <div className={CARD}>
              <h2 className="text-xl font-semibold text-gray-900 mb-4">
                {i18n.t("explore.queryParameters")}
              </h2>

              {/* School Selector (only for authenticated users) */}
              {meResponse?.kind === "authenticated" && schools.length > 0 && (
                <div className="mb-4">
                  <label
                    htmlFor="school-select"
                    className="block text-sm font-medium text-gray-700 mb-2"
                  >
                    {i18n.t("explore.school")}
                  </label>
                  <select
                    id="school-select"
                    value={selectedSchoolId ?? ""}
                    onChange={(e) =>
                      setSelectedSchoolId(Number(e.target.value))
                    }
                    className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    {schools.map((school) => (
                      <option key={school.id} value={school.id}>
                        {school.name}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {/* Variable Selector (multi-select checkboxes) */}
              <div className="mb-4">
                <p className="block text-sm font-medium text-gray-700 mb-2">
                  {i18n.t("explore.variables")}
                </p>
                <div className="space-y-2 max-h-48 overflow-y-auto">
                  {availableVariables.map((variable) => (
                    <label key={variable.key} className="flex items-center">
                      <input
                        type="checkbox"
                        checked={selectedVariables.includes(variable.key)}
                        onChange={() => toggleVariable(variable.key)}
                        className="mr-2"
                      />
                      <span className="text-sm">
                        {i18n.columnLabel(variable.key)} [{variable.key}]
                      </span>
                    </label>
                  ))}
                </div>
              </div>

              {/* Dimension Selector (optional grouping) */}
              <div className="mb-4">
                <p className="block text-sm font-medium text-gray-700 mb-2">
                  {i18n.t("explore.groupBy")}
                </p>
                <div className="space-y-2">
                  {availableDimensions.map((dimension) => (
                    <label key={dimension.key} className="flex items-center">
                      <input
                        type="checkbox"
                        checked={selectedDimensions.includes(dimension.key)}
                        onChange={() => toggleDimension(dimension.key)}
                        className="mr-2"
                      />
                      <span className="text-sm">
                        {i18n.t(`api.${dimension.key}`)}
                      </span>
                    </label>
                  ))}
                </div>
              </div>

              {/* Execute Button */}
              <button
                type="button"
                onClick={executeQuery}
                disabled={queryLoading || selectedVariables.length === 0}
                className="w-full px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors"
              >
                {queryLoading
                  ? i18n.t("explore.querying")
                  : i18n.t("explore.runQuery")}
              </button>
            </div>
          </div>

          {/* Main: Results */}
          <div className="lg:col-span-2 space-y-4">
            {queryError ? (
              <div className={CARD}>
                <p className="text-red-700">{queryError}</p>
              </div>
            ) : queryResult ? (
              <>
                {/* Display period information */}
                <div className={CARD}>
                  <p className="text-sm text-gray-600">
                    <strong>{i18n.t("explore.periodsObserved")}:</strong>{" "}
                    {queryResult.periods.join(", ")}
                  </p>
                  <p className="text-sm text-gray-600 mt-1">
                    <strong>{i18n.t("explore.variablesSelected")}:</strong>{" "}
                    {variableLabels}
                  </p>
                  {queryNotes.length > 0 && (
                    <div className="mt-3 space-y-1">
                      {queryNotes.map((note) => (
                        <p key={note} className="text-xs text-gray-500">
                          (i) {note}
                        </p>
                      ))}
                    </div>
                  )}
                </div>

                {/* Check if all periods are suppressed for all variables */}
                {allSuppressed ? (
                  <div className={CARD}>
                    <p className="text-yellow-800 font-medium text-center">
                      {i18n.t("explore.allDataSuppressed")}
                    </p>
                    <p className="text-sm text-yellow-700 mt-2 text-center">
                      {i18n.t("explore.tryAdjustingFilters")}
                    </p>
                  </div>
                ) : (
                  <ChartCard
                    title={variableLabels}
                    type={chartType}
                    data={chartData}
                    options={facetOutput?.options ?? chartOutput?.options}
                    facets={facetOutput?.facets}
                    toolbar={
                      facetOutput &&
                      queryResult.variables.length > 1 && (
                        <fieldset className="flex items-center gap-2 text-sm">
                          <legend className="sr-only">
                            {i18n.t("chart.facetBy")}
                          </legend>
                          <span aria-hidden="true">
                            {i18n.t("chart.facetBy")}:
                          </span>
                          {(["variable", "group"] as const).map((mode) => (
                            <label key={mode} className="flex gap-1">
                              <input
                                type="radio"
                                name="facet-mode"
                                value={mode}
                                checked={facetMode === mode}
                                onChange={() => setFacetMode(mode)}
                              />
                              {i18n.t(`chart.facetBy_${mode}`)}
                            </label>
                          ))}
                        </fieldset>
                      )
                    }
                    csv={chartCSV}
                    filename="explore-results"
                  />
                )}
              </>
            ) : queryLoading ? (
              <div className={`${CARD} text-center`}>
                <p className="text-gray-500 mb-2">
                  {i18n.t("explore.querying")}...
                </p>
                <p className="text-sm text-gray-400">
                  {i18n.t("explore.privacyProtection")}
                </p>
              </div>
            ) : (
              <div className={`${CARD} text-center`}>
                <p className="text-gray-500 mb-2">
                  {i18n.t("explore.selectQueryParams")}
                </p>
                <p className="text-sm text-gray-400">
                  {i18n.t("explore.privacyProtection")}
                </p>
              </div>
            )}

            {/* Partner Logos */}
            <div className="flex justify-center items-center gap-8 mt-8 flex-wrap">
              <a
                href="https://www.ox.ac.uk/"
                target="_blank"
                rel="noopener noreferrer"
                aria-label="University of Oxford"
              >
                <img
                  src="/img/university_of_oxford.svg"
                  alt="University of Oxford"
                  className="h-[100px] w-auto transition-opacity duration-200 hover:opacity-80"
                />
              </a>
              <a
                href="https://wellbeing.hmc.ox.ac.uk/"
                target="_blank"
                rel="noopener noreferrer"
                aria-label="Wellbeing Research Centre"
              >
                <img
                  src="/img/wellbeing_research_centre.svg"
                  alt="Wellbeing Research Centre"
                  className="h-[100px] w-auto transition-opacity duration-200 hover:opacity-80"
                />
              </a>
              <a
                href="https://www.rse.ox.ac.uk/"
                target="_blank"
                rel="noopener noreferrer"
                aria-label="Oxford RSE"
              >
                <img
                  src="/img/oxford_rse.svg"
                  alt="Oxford RSE"
                  className="h-[100px] w-auto transition-opacity duration-200 hover:opacity-80"
                />
              </a>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
