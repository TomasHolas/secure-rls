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
  Auditors $81,680. Sigma ~0.3 and clip bounds are modeling choices consistent
  with the sourced p10/p90 spread — flagged as such, not sourced figures.
- **Performance score** (1.0-5.0): left-skewed, clustered 3.5-4.0 with a thin
  low tail — matching documented rating inflation and compression (leniency and
  centrality bias); the IBM reference set itself contains only ratings 3-4 on
  a 1-4 scale. Exact parameters (clipped Normal(3.6, 0.6)) are a modeling
  choice consistent with the sources.
- **Hire date**: tenure drawn from an exponential with median 3.9 years (BLS,
  January 2024), capped, converted to hire_date. Exponential shape is a
  modeling choice consistent with BLS's strong age gradient.
- **Notes**: templated performance-review snippets with Faker fillers.
- **Poisoned records**: ~1-2% of rows carry prompt-injection payloads in
  `notes` (second-order injection test data, OWASP LLM01). Their `user_id`s
  are listed openly in `poisoned_manifest.json` next to the CSV — deliberate
  red-team data, not hidden. The eval suite (ADR 0004) asserts the agent leaks
  nothing when reading them; the live demo shows it.
- CI regenerates the CSV and diffs it against the committed file, proving the
  dataset is exactly what the generator produces — nothing hand-edited.

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
- Faker seeding and version caveat — https://faker.readthedocs.io/en/master/
