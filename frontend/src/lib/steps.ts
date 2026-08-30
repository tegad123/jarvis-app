/**
 * TOOL → NAMED THINKING STEP
 *
 * The brief asks the thinking state to be a NAMED step rather than an
 * invented sequence. What the gateway actually exposes on
 * /v1/chat/completions, and nothing more, is a tool lifecycle:
 *
 *   event: hermes.tool.progress
 *   {"tool":"terminal","emoji":"…","label":"ls -la","toolCallId":"…","status":"running"}
 *   {"tool":"terminal","toolCallId":"…","status":"completed"}
 *
 * Reasoning/thinking text is NOT available on this route (the gateway routes
 * thinking deltas to a callback it never wires here), and internal pseudo-tools
 * are filtered out upstream. So there are exactly two honest states: "we are
 * waiting" and "we are running <this tool>". Anything more granular would be
 * fabricated.
 *
 * `label` is the tool's primary argument (the command, the query, the path)
 * and is shown verbatim beneath the step name. Note the 'completed' payload
 * carries no label — cache it from 'running'.
 *
 * Richer steps (reasoning text, subagent progress) exist on POST /v1/runs +
 * GET /v1/runs/{id}/events. That is the upgrade path if this ever needs to
 * show more; do not invent steps here instead.
 */

/** Base state, before any tool starts. */
export const THINKING = 'Thinking…'

const VERBS: Record<string, string> = {
  terminal: 'Running a command',
  web_search: 'Searching the web',
  web_fetch: 'Reading a web page',
  read_file: 'Looking through files',
  write_file: 'Writing a file',
  edit_file: 'Editing a file',
  search_files: 'Looking through files',
  list_files: 'Looking through files',
  skill_view: 'Using a skill',
  skills_list: 'Using a skill',
  skill_manage: 'Using a skill',
  delegate_task: 'Working on a subtask',
  memory_search: 'Checking what it remembers',
  memory_write: 'Making a note',
}

/** 'search_files' → 'Search Files' */
function prettify(tool: string): string {
  const words = tool
    .replace(/[._-]+/g, ' ')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
  if (!words.length) return THINKING
  return words
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ')
}

/** Human-readable name for a running tool. Never throws, never blank. */
export function stepName(tool: string | undefined | null): string {
  const key = (tool ?? '').trim().toLowerCase()
  if (!key) return THINKING
  return VERBS[key] ?? prettify(key)
}

/** Labels can be whole shell commands; keep the step row to one line. */
export function truncateLabel(label: string | undefined, max = 48): string {
  if (!label) return ''
  const clean = label.replace(/\s+/g, ' ').trim()
  return clean.length <= max ? clean : clean.slice(0, max - 1) + '…'
}
