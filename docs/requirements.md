# Assignment requirements (distilled)

Working checklist paraphrased from an AI Engineer case-study assignment.
The full compliance mapping (requirement → where satisfied) lives
in [architecture.md](architecture.md#assignment-compliance-map); this file is
the requirement source of truth to check against at any time.

## Main goal

Row-level security (RLS) on LLM processing: a secure LLM-powered system over
multi-tenant data where the LLM can **never** access unauthorized rows — not
via generated queries, not via retrievals, not via tools.

## Evaluation areas

- Secure AI system design (RLS integrated with LLM/agent)
- Full-stack development capabilities
- Code quality, architecture, problem-solving
- Independent thinking (probed in the live demo and follow-ups)
- Familiarity with agentic development tools (Claude Code / Copilot / Open
  Code); advanced agentic techniques are a plus

## Hard requirements (take-home)

1. Public GitHub repo with code and README
2. Python 3.10+, open-source libraries; local/offline LLM via Ollama
3. Commit history showing real iteration
4. RLS focus: the LLM must never access unauthorized rows, even in generated
   queries and tools
5. Agentic tools and agentic development must be used
6. Deployment implemented as a GitHub CI/CD pipeline

## The task

A React / Dash / Streamlit app hosting a secure conversational data-analyst
agent over a multi-tenant employee dataset; natural-language analytics with
RLS enforced by the logged-in user's identity. RAG to be considered and used
where applicable.

### Dataset

`employees.csv`, ~1000 simulated multi-tenant HR rows, 3 tenants
(acme / beta / gamma), columns: `user_id, tenant_id, name, department, salary,
performance_score, hire_date, notes`.

### Core features (MVP)

1. **Data storage with RLS** — CSV loaded into SQLite (or pandas with
   filtering); the agent queries only via a secure interface, no raw SQL
   passthrough.
2. **Agent** — LangChain/LangGraph; schema + sample rows embedded; all tools
   RLS-enforced:
   - Query DB (SQL generation/execution, tenant filter enforced)
   - Stats (aggregates, e.g. average salary by department)
   - Plot (charts, e.g. salary distribution)
   - Bonus: anomaly detection (flag outliers)
3. **UI** — login with hardcoded tenant users; chat where the agent reasons,
   uses tools, and shows SQL/execution; user switching to prove isolation;
   reasoning plus final answer visible.
4. **Security guarantees** — prompts reinforce RLS but access goes only
   through tools; malicious queries ("show all salaries") blocked or scoped
   to the caller's tenant.
5. **Evaluation** — a demonstrated way of evaluating model performance.

### Deliverables

- Repo `secure-rls` containing `app.py`, `db.py`, `agent.py`,
  `employees.csv`, `requirements.txt`
- README: architecture/design, technical setup, tenant credentials,
  challenges, time spent

## Live demo call (60 minutes)

1. Intro (5 min) — repo overview, setup, design
2. Live demo + deep dive (30 min) — run the app, cross-tenant queries,
   isolation proof; code walk-through: RLS implementation, prompt
   engineering, tool binding
3. Agentic tools demo (10 min) — walk through the configured agentic dev
   setup with a live agent-mode task
4. Brainstorming on future evolution (15 min)
