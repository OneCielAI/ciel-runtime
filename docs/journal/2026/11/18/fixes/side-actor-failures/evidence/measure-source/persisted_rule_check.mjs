// Applies side-intake.js's persisted-result path rule (current and pre-ab6edbe
// name tail) to the saved-result files Claude Code wrote in one window.
// usage: node persisted_rule_check.mjs <file list> <config dir> <session id>
import { readFileSync } from 'node:fs'
const [list, configDir, sessionId] = process.argv.slice(2)
const lines = readFileSync(list, 'utf8').split(/\r?\n/).filter(Boolean)
const CURRENT = '-\\d{1,20}(?:-[0-9a-z]{1,6})?\\.txt$'
const OLD = '-\\d{1,20}\\.txt$'
const slashes = value => String(value || '').replace(/\\/g, '/').replace(/\/+$/, '')
const fold = value => value.toLowerCase()
function accepted(savedPath, tail) {
  const saved = slashes(savedPath)
  const root = slashes(configDir) + '/projects/'
  const parts = fold(saved).startsWith(fold(root)) ? saved.slice(root.length).split('/') : []
  const tool = /^mcp-walkie-(walkie_[a-z_]+)-/.exec(parts[3] || '')?.[1] || ''
  const file = new RegExp('^mcp-walkie-' + tool + tail)
  return parts.length === 4 && fold(parts[1]) === fold(sessionId) && parts[2] === 'tool-results' && file.test(parts[3])
}
console.log(JSON.stringify({ files: lines.length,
  accepted_current_rule: lines.filter(line => accepted(line, CURRENT)).length,
  accepted_pre_ab6edbe_rule: lines.filter(line => accepted(line, OLD)).length,
  suffixed_names: lines.filter(line => /-\d+-[0-9a-z]{1,6}\.txt$/.test(line)).length }))
