// Terminal-only Work/SSE dashboard. Runs on Claude Code's drawing thread: no
// Node globals, no `$`, and no nested Client/Raster/Image elements.
const C = {
  cyan: '#4de7ee', blue: '#70a5ff', green: '#62e6a7', yellow: '#ffd166',
  red: '#ff647c', pink: '#ff70a6', violet: '#bda0ff', dim: '#6f879a',
  text: '#dbe8f3', panel: '#101820', selected: '#173241', line: '#2a4354',
}

// The pane's control strip above takes Tab/arrows/Enter and these hotkeys without
// a click; a click here adds ↑↓, PgUp/PgDn, Home/End until Esc.
const FOOTER = 'W/S/G tabs · J/K move · I inspect · P P1 · F focus · R refresh · click: ↑↓ PgUp'

function clip(value, width) {
  const text = String(value ?? '').replace(/[\x00-\x1f\x7f]/g, ' ')
  if (width <= 0) return ''
  return text.length <= width ? text : text.slice(0, Math.max(0, width - 1)) + '…'
}
function pad(value, width) { return clip(value, width).padEnd(Math.max(0, width)) }
function bar(done, total, width) {
  const ratio = total > 0 ? Math.max(0, Math.min(1, done / total)) : 0
  const filled = Math.round(width * ratio)
  return '█'.repeat(filled) + '░'.repeat(Math.max(0, width - filled))
}
function stage(receipt) {
  if (receipt.actor) return 'processing'
  if (receipt.delivery && !['pending', 'not_sent'].includes(receipt.delivery)) return 'answered'
  if (receipt.action === 'create' || receipt.action === 'update' || receipt.item_id) return 'work'
  if (receipt.failed) return 'failed'
  return receipt.stage || 'received'
}
function stageColor(value) {
  if (value === 'processing') return C.cyan
  if (value === 'answered') return C.green
  if (value === 'work') return C.violet
  if (value === 'failed' || value === 'quarantined') return C.red
  return C.yellow
}
function statusColor(value) {
  if (value === 'done') return C.green
  if (value === 'blocked') return C.red
  if (value === 'in_progress') return C.cyan
  return C.dim
}
function statusGlyph(value, waiting) {
  if (value === 'done') return '✓'
  if (value === 'blocked') return '!'
  if (waiting) return '◷'
  if (value === 'in_progress') return '▶'
  return '·'
}
function priorityColor(value) {
  return value === 'P1' ? C.red : value === 'P2' ? C.yellow : value === 'P3' ? C.violet : C.dim
}

export default function Observatory(props, surface) {
  const { Box, Text } = surface.elements
  const width = Math.max(24, surface.columns || 48)
  const height = Math.max(12, surface.rows || 20)
  // Selection and the P1 filter belong to the plugin (props), so the pane's
  // native control strip and this region's click-focused keys move one cursor.
  // Only the scroll offset is local.
  const state = surface.state || { scroll: 0 }
  const p1 = props.p1 === true
  const workRows = (props.nodes || []).filter(row => !p1 || row.priority === 'P1')
  const sideRows = (props.receipts || []).filter(row => !p1 || row.priority === 'P1')
  const rows = props.tab === 'side' ? sideRows : workRows
  const selected = Math.max(0, Math.min(Number(props.selected) || 0, Math.max(0, rows.length - 1)))
  const visible = Math.max(3, height - (props.tab === 'side' ? 13 : 12))
  const scroll = Math.max(0, Math.min(selected, Math.max(state.scroll || 0, selected - visible + 1)))
  const setSelection = next => {
    const index = Math.max(0, Math.min(next, Math.max(0, rows.length - 1)))
    let top = scroll
    if (index < top) top = index
    if (index >= top + visible) top = index - visible + 1
    surface.setState({ scroll: top })
    surface.post({ action: 'select', index })
  }
  surface.onKey(event => {
    if (event.key === 'up' || event.key === 'k') setSelection(selected - 1)
    else if (event.key === 'down' || event.key === 'j') setSelection(selected + 1)
    else if (event.key === 'pageup') setSelection(selected - 8)
    else if (event.key === 'pagedown') setSelection(selected + 8)
    else if (event.key === 'home') setSelection(0)
    else if (event.key === 'end') setSelection(rows.length - 1)
    else if (event.key === 'p') { surface.setState({ scroll: 0 }); surface.post({ action: 'p1' }) }
    else if (event.key === 'w') surface.post({ action: 'tab', tab: 'work' })
    else if (event.key === 's') surface.post({ action: 'tab', tab: 'side' })
    else if (event.key === 'g') surface.post({ action: 'tab', tab: 'settings' })
    else if (event.key === 'r') surface.post({ action: 'refresh' })
    else if (event.key === 'f' && props.tab === 'work' && rows[selected])
      surface.post({ action: 'focus', id: rows[selected].id })
    else if ((event.key === 'return' || event.key === 'i') && rows[selected])
      surface.post({ action: props.tab === 'work' ? 'work' : 'receipt', id: rows[selected].id })
  })

  const hitStart = props.tab === 'side' ? 9 + Math.min(3, (props.actors || []).length) : 7
  surface.onPointer(event => {
    if (event.type !== 'down' || event.button !== 'left') return
    // Header hit targets keep the graphical surface mouse-navigable without
    // adding plain Buttons around it (which would recreate the old list UI).
    if (event.y === 0) {
      if (event.x < Math.floor(width / 3)) surface.post({ action: 'tab', tab: 'work' })
      else if (event.x < Math.floor(width * 2 / 3)) surface.post({ action: 'tab', tab: 'side' })
      else surface.post({ action: 'tab', tab: 'settings' })
      return
    }
    const index = scroll + event.y - hitStart
    if (index >= 0 && index < rows.length) setSelection(index)
  })

  const line = (key, value, color = C.text, extra = {}) => Text({ key, wrap: 'truncate-end', color, ...extra,
    children: clip(value, width) })
  const boxLine = (key, children, extra = {}) => Box({ key, height: 1, overflow: 'hidden', flexShrink: 0,
    flexDirection: 'row', ...extra, children })
  const children = []

  // Mockup-faithful status header: Work progress and parallel lanes.
  children.push(boxLine('top-labels', [
    Text({ color: props.tab === 'work' ? C.cyan : C.dim, weight: props.tab === 'work' ? 'bold' : undefined,
      children: clip('WORK / SESSION ' + (props.version ?? '?'), Math.max(12, Math.floor(width / 2))) }),
    Text({ color: props.tab === 'side' ? C.cyan : C.dim, weight: props.tab === 'side' ? 'bold' : undefined,
      children: clip('SSE FLOW ' + (props.receipts?.length || 0), 14) }),
    Text({ color: C.dim, children: 'SETTINGS' }),
  ], { justifyContent: 'space-between' }))
  children.push(line('goal', (props.active ? '▶ ' + props.active : 'No active goal'), C.text, { weight: 'bold' }))
  const progressWidth = Math.max(8, width - 17)
  children.push(boxLine('progress', [
    Text({ color: C.cyan, children: bar(props.done || 0, props.total || 0, progressWidth) }),
    Text({ color: C.text, children: ' ' + (props.done || 0) + '/' + (props.total || 0) }),
  ]))
  // FORKS counts only Work forks a fresh native agent-list observation shows
  // running. DEVICE is lease slots across every session on this machine (it
  // includes reservations, waits and stale leases). Main is never a fork.
  const max = Math.max(1, props.parallel?.max || 1)
  const active = Math.max(0, props.parallel?.active || 0)
  const forks = Math.max(0, props.parallel?.forks || 0)
  const forkText = props.parallel?.evidence === false ? 'FORKS ? ' : 'FORKS ' + forks + ' running '
  children.push(boxLine('lanes', [
    Text({ color: C.blue, children: forkText }),
    Text({ color: C.dim, children: '· DEVICE ' + active + '/' + max + ' slots · REVIEW ' + (props.parallel?.reviews?.length || 0) + ' · SIDE ' + (props.parallel?.side || 0) }),
  ]))
  for (const [index, review] of (props.parallel?.reviews || []).slice(0, 2).entries())
    children.push(line('review-' + index, '  ◆ ' + [review.status, review.item, review.title].filter(Boolean).join(' · '), C.yellow))
  for (const [index, fork] of (props.parallel?.confirmed || []).entries()) {
    const age = typeof fork.seen === 'number' ? Math.max(0, Math.round(Date.now() / 1000 - fork.seen)) + 's ago' : ''
    children.push(line('fork-' + index, clip('  ▸ ' + [fork.item, fork.priority, fork.title, fork.agent, fork.status, age]
      .filter(Boolean).join(' · '), width), C.green))
  }
  children.push(line('rule', '─'.repeat(width), C.line))

  // At the requested 90-column dock, match the reviewed overview: Work tree
  // and SSE flow are visible together. The active tab only chooses which side
  // owns ↑↓/Enter and the shared inspector; it never hides the other half.
  if (width >= 48) {
    const workWidth = Math.floor(width * .58)
    const sideWidth = width - workWidth - 1
    const bodyRows = Math.max(7, height - 13)
    const workChildren = [Text({ color: C.cyan, weight: 'bold', children: pad('WORK TREE ' + workRows.length, workWidth) })]
    const workPage = workRows.slice(scroll, scroll + bodyRows)
    for (const [offset, node] of workPage.entries()) {
      const index = scroll + offset
      const selectedRow = props.tab === 'work' && index === selected
      const indent = '  '.repeat(Math.min(3, node.depth || 0))
      const left = (selectedRow ? '▸ ' : '  ') + indent + statusGlyph(node.status, node.waiting_on) + ' '
      const priority = node.priority || ''
      const count = node.total ? node.done + '/' + node.total : ''
      const room = Math.max(4, workWidth - left.length - (priority ? 4 : 0) - count.length - 1)
      workChildren.push(Box({ key: 'wide-work-' + node.id, height: 1, overflow: 'hidden',
        flexDirection: 'row', backgroundColor: selectedRow ? C.selected : undefined, children: [
          Text({ color: statusColor(node.status), children: left }),
          ...(priority ? [Text({ color: priorityColor(priority), weight: 'bold', children: priority + ' ' })] : []),
          Text({ color: selectedRow ? C.cyan : C.text, children: pad(node.title, room) }),
          Text({ color: C.dim, children: count }),
        ] }))
    }
    while (workChildren.length < bodyRows + 1) workChildren.push(Text({ key: 'work-space-' + workChildren.length, children: ' ' }))

    const counts = { received: 0, processing: 0, answered: 0, work: 0, failed: 0 }
    for (const receipt of sideRows) counts[stage(receipt)] = (counts[stage(receipt)] || 0) + 1
    // The bordered column draws inside its border and left padding. Text wraps by
    // default, so a row clipped to the outer width wrapped and pushed later rows out
    // (real console, 51-cell dock: summaries and "No active side fork" vanished).
    const inner = Math.max(8, sideWidth - 2)
    const innerRows = Math.max(3, bodyRows - 1)
    // Rows are keyed by position and padded to the full width on a solid background.
    // Content-keyed rows (actor or receipt ids) left a retired row's cells painted in
    // Claude Code 2.1.310 when a fork ended (real console, owned probe).
    const specs = [
      { value: 'SSE FLOW ' + sideRows.length, color: C.cyan, weight: 'bold' },
      { value: 'IN ' + counts.received + ' AI ' + counts.processing + ' ANS ' + counts.answered + ' WORK ' + counts.work, color: C.dim },
    ]
    const activeActors = (props.actors || []).slice(0, 3)
    const actor = activeActors[0]
    // Fixed actor slots keep the first receipt on the same terminal row before,
    // during and after a fork. The header reports the full active count; the full
    // width SSE tab lists up to three actors.
    specs.push({ value: activeActors.length ? 'ACTIVE SSE FORKS ' + activeActors.length : 'No active side fork',
      color: activeActors.length ? C.pink : C.dim, weight: activeActors.length ? 'bold' : undefined })
    specs.push({ value: actor ? '▶ fork ' + actor.id : ' ', color: C.cyan })
    specs.push({ value: actor ? '  native fork · main model/context' : ' ', color: C.violet })
    for (const [index, receipt] of sideRows.slice(0, Math.max(1, innerRows - specs.length)).entries()) {
      const selectedRow = props.tab === 'side' && index === selected
      specs.push({ value: (selectedRow ? '▸' : ' ') + (receipt.priority || '--') + ' ' + stage(receipt) + ' ' + (receipt.summary || ''),
        color: stageColor(stage(receipt)), background: selectedRow ? C.selected : undefined })
    }
    while (specs.length < innerRows) specs.push({ value: ' ', color: C.dim })
    const sideChildren = specs.slice(0, innerRows).map((spec, index) => Box({ key: 'side-row-' + index, height: 1,
      flexShrink: 0, overflow: 'hidden', backgroundColor: spec.background || C.panel, children: [Text({ wrap: 'truncate-end',
        color: spec.color, weight: spec.weight, children: pad(spec.value, inner) })] }))
    children.push(Box({ key: 'wide-body', height: bodyRows + 1, overflow: 'hidden', flexDirection: 'row', children: [
      Box({ key: 'wide-work', width: workWidth, height: bodyRows + 1, overflow: 'hidden',
        flexDirection: 'column', children: workChildren }),
      Box({ key: 'wide-side', width: sideWidth + 1, height: bodyRows + 1, overflow: 'hidden', backgroundColor: C.panel,
        borderStyle: 'single', borderColor: C.line, paddingLeft: 1, flexDirection: 'column', children: sideChildren }),
    ] }))
    const chosen = props.tab === 'side' ? sideRows[selected] : workRows[selected]
    children.push(line('wide-inspector-rule', '─ INSPECTOR ' + (props.tab === 'side' ? 'SSE ' : 'WORK ')
      + clip(chosen?.id || 'none', 12) + ' ' + '─'.repeat(width), C.line))
    if (chosen) children.push(line('wide-inspector-title', chosen.title || chosen.summary || '', C.text, { weight: 'bold' }))
    if (chosen) children.push(line('wide-inspector-meta', props.tab === 'side'
      ? stage(chosen) + (chosen.actor ? ' · fork ' + chosen.actor : '') + (chosen.item_id ? ' → ' + chosen.item_id : '')
      : (chosen.priority || '--') + ' · ' + chosen.status + (chosen.waiting_on ? ' · waiting ' + chosen.waiting_on : ''),
      props.tab === 'side' ? stageColor(stage(chosen)) : statusColor(chosen.status)))
    children.push(line('footer', FOOTER, C.dim))
    return Box({ flexDirection: 'column', backgroundColor: C.panel, children })
  }

  if (props.tab === 'side') {
    const counts = { received: 0, processing: 0, answered: 0, work: 0, failed: 0 }
    for (const row of sideRows) counts[stage(row)] = (counts[stage(row)] || 0) + 1
    children.push(boxLine('side-head', [
      Text({ color: C.cyan, weight: 'bold', children: 'SSE FLOW ' + sideRows.length }),
      Text({ color: p1 ? C.red : C.dim, children: p1 ? ' P1 ONLY' : ' ALL · P P1' }),
    ], { justifyContent: 'space-between' }))
    children.push(boxLine('stage-counts', [
      Text({ color: C.yellow, children: 'IN ' + counts.received + '  ' }),
      Text({ color: C.cyan, children: 'SIDE AI ' + counts.processing + '  ' }),
      Text({ color: C.green, children: 'ANSWER ' + counts.answered + '  ' }),
      Text({ color: C.violet, children: 'WORK ' + counts.work }),
    ]))
    const actors = (props.actors || []).slice(0, 3)
    if (actors.length) {
      children.push(line('fork-title', 'ACTIVE FORKED SSE SESSIONS', C.pink, { weight: 'bold' }))
      for (const actor of actors) children.push(line('actor-' + actor.id,
        '▶ ' + clip(actor.id, 10) + ' · native fork (main model/context) · ' + clip(actor.receipt, 12), C.cyan))
    } else children.push(line('no-fork', 'No active side fork', C.dim))
    const visible = Math.max(3, height - 13 - actors.length)
    const page = sideRows.slice(scroll, scroll + visible)
    children.push(line('receipt-head', 'RECEIPTS · ↑↓ MOVE · ENTER INSPECT', C.dim))
    for (const [offset, receipt] of page.entries()) {
      const index = scroll + offset
      const selectedRow = index === selected
      const value = (selectedRow ? '▸ ' : '  ') + pad(receipt.priority || '--', 3) + ' '
        + pad(stage(receipt), 10) + ' ' + clip(receipt.summary, Math.max(0, width - 19))
      children.push(boxLine('receipt-' + receipt.id, [Text({ color: stageColor(stage(receipt)), children: clip(value, width) })],
        selectedRow ? { backgroundColor: C.selected } : {}))
    }
    const receipt = sideRows[selected]
    if (receipt) {
      children.push(line('inspect-rule', '─ INSPECTOR ' + clip(receipt.id, 12) + ' ' + '─'.repeat(width), C.line))
      children.push(line('inspect-summary', receipt.summary, C.text))
      children.push(line('inspect-meta', (receipt.actor ? 'fork ' + receipt.actor : stage(receipt))
        + (receipt.action ? ' · ' + receipt.action : '') + (receipt.item_id ? ' → ' + receipt.item_id : ''), stageColor(stage(receipt))))
    }
  } else {
    children.push(boxLine('work-head', [
      Text({ color: C.cyan, weight: 'bold', children: 'WORK TREE ' + workRows.length }),
      Text({ color: p1 ? C.red : C.dim, children: p1 ? ' P1 ONLY' : ' ALL · P P1' }),
    ], { justifyContent: 'space-between' }))
    const visible = Math.max(3, height - 12)
    const page = workRows.slice(scroll, scroll + visible)
    for (const [offset, node] of page.entries()) {
      const index = scroll + offset
      const selectedRow = index === selected
      const indent = '  '.repeat(Math.min(4, node.depth || 0))
      const left = (selectedRow ? '▸ ' : '  ') + indent + statusGlyph(node.status, node.waiting_on) + ' '
      const priority = node.priority || ''
      const room = Math.max(3, width - left.length - (priority ? 4 : 0) - 7)
      const count = node.total ? ' ' + node.done + '/' + node.total : ''
      children.push(boxLine('node-' + node.id, [
        Text({ color: statusColor(node.status), children: left }),
        ...(priority ? [Text({ color: priorityColor(priority), weight: 'bold', children: priority + ' ' })] : []),
        Text({ color: selectedRow ? C.cyan : C.text, children: clip(node.title, room) }),
        Text({ color: C.dim, children: clip(count, 7) }),
      ], selectedRow ? { backgroundColor: C.selected } : {}))
    }
    const node = workRows[selected]
    if (node) {
      children.push(line('inspect-rule', '─ INSPECTOR WORK ' + clip(node.id, 10) + ' ' + '─'.repeat(width), C.line))
      children.push(boxLine('inspect-tags', [
        Text({ color: priorityColor(node.priority), children: (node.priority || '--') + '  ' }),
        Text({ color: statusColor(node.status), children: node.status + (node.waiting_on ? ' · waiting' : '') }),
      ]))
      children.push(line('inspect-title', node.title, C.text, { weight: 'bold' }))
      if (node.waiting_on) children.push(line('inspect-wait', 'waiting: ' + node.waiting_on, C.yellow))
    }
  }
  children.push(line('footer', FOOTER, C.dim))
  return Box({ flexDirection: 'column', backgroundColor: C.panel, children })
}
