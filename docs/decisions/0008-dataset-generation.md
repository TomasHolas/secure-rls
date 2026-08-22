# ADR 0008 — Dataset generation: seeded, calibrated to sourced distributions

Status: accepted

## Context

The assignment requires ~1000 rows of simulated multi-tenant HR data. Inventing
rows by hand (or letting an LLM hallucinate them) produces unrealistic
distributions and is not reproducible. Reference datasets exist — the de-facto
standard IBM HR Analytics set (1,470 fictional rows; CC BY 4.0 via its Mendeley
mirror) and the Huebner/Patalano HR set (CC-BY-NC-ND, so unusable as a derived
base) — but none is multi-tenant, so we generate our own with distributions
calibrated to citable sources.

## Decision

`scripts/generate_dataset.py` produces `employees.csv` deterministically:

- **Determinism**: single seed in `runtime.json`; `Faker.seed()` for
  PII-like fields and a separately seeded `numpy.random.default_rng` for
  numeric columns (Faker's seed does not govern numpy). `faker` is pinned to
  an exact version — its docs guarantee reproducibility only per patch release.
- **Tenants**: `acme` ~45%, `beta` ~35%, `gamma` ~20% of ~1000 rows — same
  distributions, different samples, so per-tenant answers differ visibly in
  the isolation demo.
- **Departments** (5): Engineering, Sales, Marketing, HR, Finance.
- **Salary**: lognormal per department (salaries are right-skewed; lognormal
  body is the standard model), median-anchored to BLS OEWS May 2024 occupation
  proxies — Software Developers $133,080; Sales Reps $66,780; Marketing
  (between specialist and manager occupations) ~$100,000; HR Specialists
  $72,910 (p10 <$45,440, p90 >$126,540 anchors the spread); Accountants and
  Auditors $81,680. Sigma ~0.3 and the truncation bounds are modeling choices
  consistent with the sourced p10/p90 spread — flagged as such, not sourced
  figures. The distribution is **truncated by rejection, never clipped** (see
  the amendment below).
- **Performance score** (1.0-5.0): left-skewed, clustered 3.5-4.0 with a thin
  low tail — matching documented rating inflation and compression (leniency and
  centrality bias); the IBM reference set itself contains only ratings 3-4 on
  a 1-4 scale. Exact parameters (clipped Normal(3.6, 0.6)) are a modeling
  choice consistent with the sources.
- **Hire date**: tenure drawn from an exponential with median 3.9 years (BLS,
  January 2024), capped, converted to hire_date. Exponential shape is a
  modeling choice consistent with BLS's strong age gradient.
- **Notes**: performance-review prose **composed** from per-band clause pools
  with Faker fillers, coherent with the row's score (see the amendment below).
- **Poisoned records**: ~1-2% of rows carry prompt-injection payloads in
  `notes` (second-order injection test data, OWASP LLM01). Their `user_id`s
  are listed openly in `poisoned_manifest.json` next to the CSV — deliberate
  red-team data, not hidden. The eval suite (ADR 0004) asserts the agent leaks
  nothing when reading them; the live demo shows it.
- CI regenerates the CSV and diffs it against the committed file, proving the
  dataset is exactly what the generator produces — nothing hand-edited.

## Amendment (after issue #89): truncate by rejection, and compose the notes

Two realism defects showed up in the live app, both traced to the generator.
The BLS anchors, the distribution shapes and the sigma above are unchanged and
stay authoritative; only *how* a draw outside the bounds is handled, and *how* a
note is written, changed.

**Salary: clipping became rejection sampling.** `np.clip` mapped every draw
outside the p10/p90-derived bounds *onto* the bound, so the bounds became mass
points — measured on the old CSV: 862 distinct salaries in 1000 rows, with every
department's minimum and maximum repeating (Sales min 41620 x19, Finance min
50910 x16, Marketing min 62320 x16, Engineering min 82940 x13 and max 230970 x9,
HR min 45440 x9). The Tukey fences that `detect_anomalies` uses then flagged
exactly those mass points, so the anomaly tool showed a stack of identical
salaries — acme's list was 7 rows carrying only 2 distinct values, six of them
230970. That reads as fabricated data, which is the opposite of the point.

The generator now draws the lognormal factor and **redraws** while it falls
outside the bounds (`_salary_factor`), which is textbook rejection sampling for
a truncated distribution: the surviving draws are distributed exactly as the
lognormal conditioned on the interval, so the sourced shape is preserved rather
than deformed, and no probability mass is moved to the endpoints. Acceptance is
~91% per draw at sigma 0.3, so the cost is negligible; a loud iteration guard
(`SALARY_MAX_REDRAWS`) raises rather than silently falling back to a clip, so a
future sigma that disagrees with the bounds fails visibly instead of quietly
re-introducing the defect. Measured after the change: 954 distinct salaries in
1000 rows, no value repeating more than 3 times, every department's minimum and
maximum unique, and the anomaly lists now carry only distinct values.

**Notes: one template per row became compositional generation.** The old pools
were 12 templates over three score bands, giving 754 distinct texts in 1000 rows
with one rendering repeated 9 times, so semantic search returned near-identical
prose and undercut the retrieval demo. Notes are now assembled from five
independent per-band clause pools (opening assessment, evidence, development
area, next step, closing) combined through one of several sentence shapes, so
length and structure vary per row and the corpus is combinatorially varied:
1000 distinct texts in 1000 rows, mean length 187 characters against 90 before.

- **Band coherence stays absolute.** The clause pools are disjoint across the
  three score bands, so no clause from one band can ever describe a score in
  another — a 4.7 cannot read as underperformance because the sentence it is
  built from does not exist in the strong pool. This is a structural property of
  the pools, not a filter, and a test asserts it row by row.
- Capitalization is applied from the **sentence shape**, which knows where its
  own sentences begin, rather than inferred from the finished string — inferring
  it mistook a Faker name suffix (`... Jr. `) for a sentence break.
- Everything else is unchanged: determinism under the same seed, no emojis, and
  the injection payloads still appended to otherwise coherent notes and still
  listed openly in `poisoned_manifest.json`.

Both changes shift every row of the CSV (the rejection loop consumes a variable
number of draws), so the committed dataset was regenerated and the eval report's
ground truth must be recomputed — the harness derives it from the CSV, so a
re-run suffices.

## Consequences

- Fully synthetic PII — safe for a public repo; no real persons.
- Every distribution parameter is either cited or explicitly labeled a
  modeling choice; the generator's constants carry these values by name.
- Realistic skews make demo analytics (salary distributions, department
  comparisons, anomalies) look and behave like real payroll data.

## Alternatives

- **Use the IBM dataset directly** — not multi-tenant, only 3 departments,
  monthly (not annual) income; and the assignment asks us to create the data.
- **Uniform random values** — trivially easy, visibly fake, and anomaly
  detection over uniform data is meaningless.

## References

- IBM HR Analytics Employee Attrition dataset —
  https://www.kaggle.com/datasets/pavansubhasht/ibm-hr-analytics-attrition-dataset
  (CC BY 4.0 mirror: https://data.mendeley.com/datasets/zx9w44krt6/1)
- Huebner and Patalano, Human Resources Data Set (schema reference only,
  CC-BY-NC-ND) — https://www.kaggle.com/datasets/rhuebner/human-resources-data-set
- Salary skew: Penn State STAT 100 — https://online.stat.psu.edu/stat100/lesson/3/3.3;
  lognormal body / Pareto tail: Equity Methods, "Statistics 101 for Pay Equity" —
  https://www.equitymethods.com/articles/statistics-101-for-pay-equity/;
  Neal and Rosen, "Theories of the Distribution of Earnings" —
  https://public.econ.duke.edu/~vjh3/e262p/readings/Neal_Rosen.pdf
- BLS OEWS May 2024 medians via Occupational Outlook Handbook —
  https://www.bls.gov/ooh/ (software developers, wholesale/manufacturing sales
  representatives, advertising/promotions/marketing managers, HR specialists,
  accountants and auditors pages)
- Rating inflation/compression: Golman and Bhatia 2012 —
  https://www.sciencedirect.com/science/article/abs/pii/S036136821200092X;
  leniency bias as negative skew — https://pmc.ncbi.nlm.nih.gov/articles/PMC5385382/;
  U.S. OPM on ratings inflation — https://www.opm.gov/news/secrets-of-opm/bad-management/
- BLS Employee Tenure (median 3.9 years, January 2024) —
  https://www.bls.gov/news.release/pdf/tenure.pdf
- Rejection (acceptance-rejection) sampling as the standard way to draw from a
  distribution restricted to an interval, and the reason it preserves the shape
  rather than piling mass on the endpoints — Devroye, *Non-Uniform Random
  Variate Generation*, Springer 1986, chapter II.3 (author's free PDF:
  http://luc.devroye.org/rnbookindex.html); SciPy's truncated-distribution
  documentation for the same construction in library form —
  https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.truncnorm.html
- Faker seeding and version caveat — https://faker.readthedocs.io/en/master/
