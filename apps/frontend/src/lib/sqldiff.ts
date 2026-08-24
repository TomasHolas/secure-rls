/**
 * The SQL diff brick: what the tenant-scoping layer added to the statement the model wrote.
 *
 * `db.execute_scoped` rewrites every `employees` reference into
 * `(SELECT * FROM employees WHERE employees.tenant_id = ?) AS employees` and renders the whole
 * tree back through sqlglot, so the executed statement differs from the generated one in two
 * unrelated ways: the scoping subquery, which is the security story, and sqlglot's own
 * formatting - one flat line, uppercased keywords - which is noise. Three consequences for how
 * the diff is computed:
 *
 * - It diffs TOKENS, not lines, and compares them case-insensitively. A line diff would report
 *   the whole statement as changed and say nothing.
 * - It minimises the number of edit RUNS rather than the number of edited tokens. The injected
 *   subquery repeats the words around it (`employees`, `WHERE`, `FROM`), so the alignment with
 *   the most matched tokens strands the model's own words inside the insertion and renders the
 *   rewrite as confetti. Scoring a run once and its length cheaply keeps it one block.
 * - An alias belongs to the `AS` that introduced it, which the alignment cannot see because it
 *   scores tokens and not syntax. The rewrite spells its alias like the table the model wrote, so
 *   the cheapest alignment explains the alias with that token and ends the insertion on a
 *   dangling `AS`; one pass afterwards hands the alias back to the insertion.
 *
 * The segments cover the EXECUTED statement only, so they concatenate back to it exactly and the
 * card that claims to show what ran shows nothing else. What the rewrite replaced is not marked
 * here: the generated statement sits on screen beside it and carries that text verbatim.
 *
 * A fixed-template tool (`get_stats`, `plot`, `detect_anomalies`) has no generated side to diff
 * against - the model never wrote SQL there - so `markScoping` finds the same injected subquery in
 * the single statement by its known shape and returns the same segments, which is what lets one
 * mark, one legend and one set of rules serve both cards.
 *
 * Pure functions over strings - no React, no DOM - so both are testable directly.
 */

/** What the rewrite did to one run of the executed statement. */
export type SegmentKind = "same" | "add";

/** One run of the executed statement, tagged by what the rewrite did to it. */
export interface DiffSegment {
  kind: SegmentKind;
  text: string;
}

interface Token {
  value: string;
  start: number;
  end: number;
}

/** What the alignment decided about one token: kept, inserted by the rewrite, or replaced by it. */
interface Op {
  kind: SegmentKind | "del";
  index: number;
}

/** An op that survives into the executed statement, so its `index` is a token of that side. */
type Kept = { kind: SegmentKind; index: number };

/** The keyword whose insertion also claims the identifier after it, because that is its alias. */
const ALIAS_KEYWORD = "as";

/**
 * The subquery layer 3 injects around an `employees` reference, alias included - `db.py`'s
 * `_SCOPED_SELECT` as sqlglot renders it, spelled tolerantly of spacing and keyword case. It is a
 * structural identity, not a tunable: a statement that does not carry it was not scoped this way.
 */
const SCOPING_PATTERN =
  /\(\s*SELECT\s+\*\s+FROM\s+employees\s+WHERE\s+employees\.tenant_id\s*=\s*\?\s*\)(?:\s+AS\s+[A-Za-z_][\w$]*)?/gi;

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
 * too long to align. `add` is what the scoping layer inserted and `same` the model's own words;
 * the alignment's deletions are what it read the generated side against, and are dropped here
 * because a segment of the generated statement is not part of the statement that ran.
 */
export function diffSql(generated: string, executed: string): DiffSegment[] | null {
  const from = tokenize(generated);
  const to = tokenize(executed);
  if (from.length > MAX_TOKENS || to.length > MAX_TOKENS) return null;
  if (to.length === 0) return [{ kind: "same", text: executed }];
  const kept = align(from, to).filter((op): op is Kept => op.kind !== "del");
  return merge(render(claimAliases(kept, to), to, executed));
}

/**
 * One statement as diff segments with every scoping subquery in it marked `add`, or null when the
 * statement carries none. Used where there is no generated side to diff - a fixed template, whose
 * SQL the server wrote and whose scoping is therefore a known pattern rather than an alignment.
 * Null is the honest answer for a statement without the pattern: the caller renders it unmarked
 * instead of guessing which of its runs the security layer contributed.
 */
export function markScoping(executed: string): DiffSegment[] | null {
  const parts: DiffSegment[] = [];
  let cursor = 0;
  for (const match of executed.matchAll(SCOPING_PATTERN)) {
    parts.push({ kind: "same", text: executed.slice(cursor, match.index) });
    parts.push({ kind: "add", text: match[0] });
    cursor = match.index + match[0].length;
  }
  if (parts.length === 0) return null;
  parts.push({ kind: "same", text: executed.slice(cursor) });
  return merge(parts);
}

/**
 * The alias of an inserted `AS` marked as inserted too. `db.execute_scoped` emits
 * `(...) AS employees`, so the alias is spelled like the table reference the model wrote and the
 * alignment can always account for it with that token - which ends the highlight on an `AS` with
 * nothing after it, a construct no SQL reader recognises. The alias is the rewrite's own word.
 */
function claimAliases(ops: Kept[], to: Token[]): Kept[] {
  return ops.map((op, position) => {
    const previous = ops[position - 1];
    const aliased =
      op.kind === "same" && previous?.kind === "add" && key(to[previous.index]) === ALIAS_KEYWORD;
    return aliased ? { kind: "add", index: op.index } : op;
  });
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
 * The kept ops as segments over the executed statement. The whitespace after a token belongs to
 * its own run only when the next op continues that run, so an inserted run highlights as one
 * block instead of striping at every space and the mark never bleeds onto the model's own words.
 */
function render(ops: Kept[], to: Token[], executed: string): DiffSegment[] {
  const out: DiffSegment[] = [{ kind: "same", text: executed.slice(0, to[0].start) }];
  ops.forEach((op, position) => {
    const token = to[op.index];
    const next = to[op.index + 1];
    const continues = ops[position + 1]?.kind === op.kind;
    out.push({ kind: op.kind, text: executed.slice(token.start, token.end) });
    out.push({
      kind: continues && op.kind === "add" ? "add" : "same",
      text: executed.slice(token.end, next ? next.start : executed.length),
    });
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
