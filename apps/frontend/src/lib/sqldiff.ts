/**
 * The SQL diff brick: what the tenant-scoping layer added to the statement the model wrote.
 *
 * `db.execute_scoped` rewrites every `employees` reference into
 * `(SELECT * FROM employees WHERE employees.tenant_id = ?) AS employees` and renders the whole
 * tree back through sqlglot, so the executed statement differs from the generated one in two
 * unrelated ways: the scoping subquery, which is the security story, and sqlglot's own
 * formatting - one flat line, uppercased keywords - which is noise. Two consequences for how the
 * diff is computed:
 *
 * - It diffs TOKENS, not lines, and compares them case-insensitively. A line diff would report
 *   the whole statement as changed and say nothing.
 * - It minimises the number of edit RUNS rather than the number of edited tokens. The injected
 *   subquery repeats the words around it (`employees`, `WHERE`, `FROM`), so the alignment with
 *   the most matched tokens strands the model's own words inside the insertion and renders the
 *   rewrite as confetti. Scoring a run once and its length cheaply keeps it one block.
 *
 * Pure functions over two strings - no React, no DOM - so the alignment is testable directly.
 */

/** One run of the executed statement, tagged by what the rewrite did to it. */
export interface DiffSegment {
  kind: "same" | "add" | "del";
  text: string;
}

interface Token {
  value: string;
  start: number;
  end: number;
}

/** What the alignment decided about one token: kept, inserted by the rewrite, or replaced by it. */
interface Op {
  kind: "same" | "add" | "del";
  index: number;
}

/** Quoted strings stay whole; words and numbers are single tokens; every other glyph is its own. */
const TOKEN_PATTERN = /'(?:[^']|'')*'|"(?:[^"]|"")*"|[A-Za-z_][\w$]*|\d+(?:\.\d+)?|\S/g;

/**
 * The alignment is quadratic and both statements are bounded by the backend's SQL length cap;
 * this is the token count past which the diff is not worth computing and the caller falls back
 * to showing the two statements whole.
 */
const MAX_TOKENS = 250;

/**
 * What one run of edits costs to start, and each of its tokens to continue. A run is preferred
 * whole over one broken around matched words inside it as long as OPEN exceeds the length of the
 * stranded island: the scoping subquery strands at most `employees WHERE`, so 4 leaves margin
 * without flattening genuinely separate edits into one.
 */
const OPEN = 4;
const EXTEND = 1;

/** The three ways into a cell: continuing matches, continuing an insertion, continuing a deletion. */
const MATCHED = 0;
const INSERTED = 1;
const DELETED = 2;
const STATES = 3;

/**
 * The executed statement as diff segments against the generated one, or null when either side is
 * too long to align. `add` is what the scoping layer inserted, `del` what it replaced - text taken
 * from the generated statement, so nothing is silently dropped - and `same` the model's own words.
 */
export function diffSql(generated: string, executed: string): DiffSegment[] | null {
  const from = tokenize(generated);
  const to = tokenize(executed);
  if (from.length > MAX_TOKENS || to.length > MAX_TOKENS) return null;
  if (to.length === 0) return [{ kind: "same", text: executed }];
  const ops = align(from, to);
  return merge(render(ops, from, to, generated, executed));
}

function tokenize(sql: string): Token[] {
  const tokens: Token[] = [];
  for (const match of sql.matchAll(TOKEN_PATTERN)) {
    const start = match.index;
    tokens.push({ value: match[0], start, end: start + match[0].length });
  }
  return tokens;
}

/** Case-insensitive because sqlglot re-renders keywords; a quoted literal keeps its own case. */
function key(token: Token): string {
  return token.value.toLowerCase();
}

/**
 * The cheapest alignment of the two token streams, as one op per token of either side in reading
 * order.
 *
 * `cost[i][j][state]` is the cheapest way to align the suffixes from `i` and `j` given that the
 * op before them was `state`, which is what makes continuing a run cheaper than opening one. The
 * table is filled from the end backwards, then walked forwards, taking at each cell the option
 * that its own cost says is cheapest - so the walk cannot drift from the optimum it read.
 */
function align(from: Token[], to: Token[]): Op[] {
  const width = to.length + 1;
  const cost = new Float64Array((from.length + 1) * width * STATES);
  const at = (i: number, j: number, state: number) => (i * width + j) * STATES + state;

  for (let i = from.length; i >= 0; i -= 1) {
    for (let j = to.length; j >= 0; j -= 1) {
      if (i === from.length && j === to.length) continue;
      for (let state = 0; state < STATES; state += 1) {
        cost[at(i, j, state)] = Math.min(...options(i, j, state).map((option) => option.cost));
      }
    }
  }

  function options(i: number, j: number, state: number): { op: Op; cost: number }[] {
    const open = (run: number) => (state === run ? EXTEND : OPEN + EXTEND);
    const out: { op: Op; cost: number }[] = [];
    if (i < from.length && j < to.length && key(from[i]) === key(to[j])) {
      out.push({ op: { kind: "same", index: j }, cost: cost[at(i + 1, j + 1, MATCHED)] });
    }
    if (j < to.length) {
      out.push({
        op: { kind: "add", index: j },
        cost: open(INSERTED) + cost[at(i, j + 1, INSERTED)],
      });
    }
    if (i < from.length) {
      out.push({
        op: { kind: "del", index: i },
        cost: open(DELETED) + cost[at(i + 1, j, DELETED)],
      });
    }
    return out;
  }

  const ops: Op[] = [];
  let i = 0;
  let j = 0;
  let state = MATCHED;
  while (i < from.length || j < to.length) {
    const best = options(i, j, state).reduce((left, right) =>
      right.cost < left.cost ? right : left,
    );
    ops.push(best.op);
    if (best.op.kind === "del") {
      i += 1;
      state = DELETED;
    } else if (best.op.kind === "add") {
      j += 1;
      state = INSERTED;
    } else {
      i += 1;
      j += 1;
      state = MATCHED;
    }
  }
  return ops;
}

/**
 * The ops as segments over the two statements. The whitespace after a token belongs to its own
 * run only when the next op continues that run, so an inserted run highlights as one block
 * instead of striping at every space and the mark never bleeds onto the model's own words.
 */
function render(
  ops: Op[],
  from: Token[],
  to: Token[],
  generated: string,
  executed: string,
): DiffSegment[] {
  const out: DiffSegment[] = [{ kind: "same", text: executed.slice(0, to[0].start) }];
  ops.forEach((op, position) => {
    const deleted = op.kind === "del";
    const tokens = deleted ? from : to;
    const source = deleted ? generated : executed;
    const token = tokens[op.index];
    const next = tokens[op.index + 1];
    const continues = ops[position + 1]?.kind === op.kind;
    out.push({ kind: op.kind, text: source.slice(token.start, token.end) });
    // A deletion's trailing whitespace belongs to the generated statement, so it is carried only
    // while the deletion runs on; otherwise the gap is the executed statement's own.
    if (!deleted || continues) {
      out.push({
        kind: continues && op.kind !== "same" ? op.kind : "same",
        text: source.slice(token.end, next ? next.start : source.length),
      });
    }
  });
  return out;
}

/** Adjacent segments of one kind become one, which is what makes a run render as a single mark. */
function merge(parts: DiffSegment[]): DiffSegment[] {
  const out: DiffSegment[] = [];
  for (const part of parts) {
    if (part.text === "") continue;
    const last = out[out.length - 1];
    if (last && last.kind === part.kind) {
      out[out.length - 1] = { kind: last.kind, text: last.text + part.text };
    } else {
      out.push(part);
    }
  }
  return out;
}
