# MOCK_DATA agent notes

This file is for implementation-oriented execution notes.

## Objective

Implement messy, realistic dev/demo questionnaire data that exercise:

- multi-form ingestion
- mixed questionnaire versions
- namespaced variables
- sparse submission coverage
- timestamp-derived longitudinal periods

## Hard decisions already made

- Use about 20 schools in the large generated dataset.
- Reserve 2 generated schools as donor-only schools in the transformation step.
- Final output schools use this split:
- `5/5/4/1/1` for the standard 3-wave patterns plus one 4-wave school and one 5-wave school.
- Map waves to academic years `2020-2021` through `2024-2025`.
- `wave` is seed-only and should not remain in ODK submissions.
- `uid` must be unique across schools by embedding school identity.
- `class` is dropped.
- `yearGroup` lives only on the demographics form.
- `d_*` fields live only on the demographics form.
- BeWell current full form is `v2`.
- BeWell historical form is `v1` under the same `xmlFormId`.
- Old BeWell is mostly early-use, with one all-wave school and one middle-wave-only school.
- At least one school-period must contain mixed BeWell versions.
- PHQ-9 is a separate form and has patchy school-wave coverage.
- Some student-periods should be missing one expected form.
- API variables must be namespaced as `xmlFormId__field`.
- The PHQ-9 form should deliberately contain one extra field named `bw_wbeing_1` to test collisions.

## Canonical generator split

Do not try to make the `glow-dummies` base wide CSV contain two real columns with the same raw field name.

Current implementation approach:

- the base generator emits a disambiguated overlap-control column named `phq_overlap_bw_wbeing_1`
- the local transformation script later renames that field to raw `bw_wbeing_1` only in the PHQ-9 form output

This is the cleanest way to preserve both a valid wide base dataset and a genuine downstream cross-form clash.

## Recommended canonical data model

Maintain two conceptual layers.

1. Raw stacked submission table
2. Materialized analytic table

The raw stacked submission table should contain one row per submission and include:

- `uid`
- `school`
- `createdAt`
- `period_id`
- `__xmlFormId`
- `__version`
- raw form fields

The materialized analytic table should contain one row per:

- `uid`
- `school`
- `period_id`

Shared demographic columns in the analytic table may remain unprefixed:

- `yearGroup`
- `d_age`
- `d_sex`
- `d_ethnicity`
- `d_sexualOrientation`
- `d_genderIdentity`

Questionnaire variables must be namespaced:

- `bewell_questionnaire__bw_wbeing_1`
- `phq9_questionnaire__phq9_1`
- `phq9_questionnaire__bw_wbeing_1`

Do not silently merge same-named variables from different forms.

## Important code reality

Current code only supports one ODK form and only whitelists numerical variables by prefix in `settings.DATA_PREFIXES`, currently `bw`.

Current deduplication is per-variable latest-non-null, not true row materialization.

Any implementation must account for that and not assume the current pipeline already produces one row per student-period.

## Suggested implementation order

### Phase 1: form artifacts first

Create the actual form surfaces before refactoring the API.

Deliverables:

- `odk-forms/bewell_questionnaire_v2.xml` as current `v2`
- `odk-forms/bewell_questionnaire_v1.xml`
- `odk-forms/phq9_questionnaire.xml`
- `odk-forms/demographics_questionnaire.xml`

Recommended `v1` structure:

- unchanged overlap: `bw_selfest_*`
- shifted overlap: `bw_wbeing_*` from `1..6`
- rescaled overlap: `bw_stress_*` from `0..3`
- many missing later groups

Note:

- `odk-forms/README.md` already documents multi-form upload behavior during deployment
- `scripts/generate_xlsform.py` already gives us a BeWell form-generation foothold

### Phase 2: generate and transform mock data

Add a deterministic transformation script for dev/demo data.

Suggested outputs:

- per-form CSVs for seeding
- a manifest CSV or JSON that records submission intent

Manifest should include at least:

- `uid`
- `school`
- `xmlFormId`
- `target_wave`
- `target_period_id`
- `target_version`
- `instance_id`

Current implementation files:

- canonical base generator config: `../glow-dummies/examples/glow_model.toml`
- local transformation script: `scripts/odk/transform_mock_data.py`

Transformation responsibilities:

- assign school wave patterns deterministically
- reserve two donor-only schools and reuse their rows deterministically for extra later waves
- assign BeWell version usage deterministically
- assign PHQ-9 coverage deterministically
- progress `yearGroup` over time
- introduce a few repeats and skips
- remove form-inappropriate columns
- introduce controlled submission missingness
- rename `phq_overlap_bw_wbeing_1` into raw `bw_wbeing_1` only in the PHQ-9 output
- derive v1 BeeWell rows from the v1 XML field set and documented transforms

Potential new script names:

- implemented: `scripts/odk/transform_mock_data.py`

### Phase 3: seed ODK in phases

The current `seed_odk_test_data.py` seeds one form at a time and uses `uid-wave` instance IDs.

This will need to evolve.

Requirements:

- seed multiple forms
- seed multiple BeWell versions in ordered phases
- generate instance IDs that include form identity and period intent
- remain idempotent

Recommended instance-id shape:

- `uuid:{uid}-{xmlFormId}-{period-token}`

Seed order:

1. publish BeWell `v1`
2. seed BeWell `v1` submissions
3. publish BeWell `v2`
4. seed BeWell `v2` submissions
5. seed PHQ-9 submissions
6. seed demographics submissions

### Phase 4: rewrite timestamps and inspect ODK outputs

Implement a demo/dev timestamp rewrite script after seeding.

Do not hard-code guessed ODK table names without first confirming the current local schema.

First inspect the running ODK Postgres schema in the local stack, then implement a deterministic script keyed by submission instance ID.

The script should rewrite at least the timestamp used by ODK's `submissions.csv` export as `createdAt`.

If `updatedAt` or related audit timestamps also need to remain coherent, rewrite those too.

Potential script name:

- `scripts/odk/rewrite_odk_submission_timestamps.py`

This phase should end with exported submission CSVs that we can treat as the ingestion target for API work.

### Phase 5: tests for the API changes

Add or adjust tests once the target ODK-exported shape is understood.

Focus areas:

- multi-form fetch and concatenation
- namespaced variable discovery
- collision safety for `bw_wbeing_1` across BeWell and PHQ-9
- demographics-only authority for `yearGroup` and `d_*`
- PHQ-9 queryability
- mixed-version BeWell period handling
- version-specific metadata lookup
- timestamp-to-period behavior around cutoff dates

Likely files:

- `api/tests/test_data.py`
- `api/tests/test_query.py`
- `api/tests/test_api.py`

### Phase 6: replace single-form configuration

Current single-form config:

- `GLOW_ODK_FORM_ID`
- `settings.ODK_FORM_ID`

Replace with a form registry, probably a list of form configs.

Minimal useful shape:

- `xmlFormId`
- `kind`
- `historical_metadata_paths` or equivalent

Likely files:

- `api/src/glow_api/settings.py`
- `api/src/glow_api/data.py`
- `api/src/glow_api/odk_client.py`
- `compose.yml`
- `dev-init.sh`

### Phase 7: fetch and tag multiple forms

Update ODK fetching to:

- fetch `submissions.csv` for each configured form
- add `__xmlFormId` column before concatenation
- collect form metadata per form
- support historical metadata lookup for BeWell by `__version`

Do not assume one global metadata dict is enough anymore.

### Phase 8: materialize analytic rows

Build a materialization step after normalization.

Recommended algorithm:

1. normalize submissions and derive `period_id`
2. split rows by form type
3. namespace questionnaire columns per form
4. deduplicate within each form and variable source as needed
5. outer-join or otherwise assemble one analytic row per `uid` + `school` + `period_id`
6. fill demographic authority only from the demographics form
7. recompute derived totals on the assembled analytic table

This may be implemented as a new transformation function rather than by stretching `deduplicate_submissions()` too far.

### Phase 9: query-layer adjustments

Update query execution so that:

- namespaced variables are selectable
- derived totals for PHQ-9 are treated as numerical variables
- mixed-version BeWell checks consult the correct version metadata
- notes such as `values-rescaled` remain attached to period slices

Likely files:

- `api/src/glow_api/query_execution.py`
- `api/src/glow_api/version_compatibility.py`
- `api/src/glow_api/data.py`
- `api/src/glow_api/routers/dimensions.py`

### Phase 10: dev bootstrap integration

Update bootstrap flows so they can:

- upload all required forms
- run the transformation step
- seed in phases
- rewrite timestamps
- restart or refresh the API

Likely files:

- `dev-init.sh`

## Dashboard implications

Do not forget the dashboard contract surface.

Likely follow-up work:

- variable labels for namespaced questionnaire variables
- no assumption that every variable exists in every period
- no assumption that variables are all BeWell-prefixed

The dashboard may not need major design changes immediately, but it must not break when namespaced variables appear.

## Curated fixture strategy

Keep most tests on small curated datasets.

Add targeted fixtures covering:

- two forms sharing the same raw field name
- missing demographics in one period
- missing PHQ-9 in one period
- mixed BeWell versions in the same period
- a school that returns to an old pattern such as `1,2,5`
- boundary timestamps near the academic-year cutoff

Do not use the large generated dataset as the primary checked-in unit-test fixture.

## Acceptance criteria

Implementation is complete when:

- API ingests multiple ODK forms together
- analytic data are materialized by `uid` + `school` + `period_id`
- questionnaire variables are namespaced
- PHQ-9 variables and totals are queryable
- BeWell version comparisons use real historical metadata
- seeded dev/demo data span five academic years via timestamp rewriting
- seeded dev/demo data include irregular school participation, mixed versions, and missing forms

## Warning

The biggest trap is trying to bolt namespacing and multi-form behavior directly onto the existing per-variable deduplication path without introducing a clearer analytic materialization step.

Prefer a small explicit transformation layer over clever special cases.
