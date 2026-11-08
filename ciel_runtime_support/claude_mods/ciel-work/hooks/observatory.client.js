// Terminal-only Work/SSE dashboard. Runs on Claude Code's drawing thread: no
// Node globals, no `$`, and no nested Client/Raster/Image elements.
const C = {
  cyan: '#4de7ee', blue: '#70a5ff', green: '#62e6a7', yellow: '#ffd166',
  red: '#ff647c', pink: '#ff70a6', violet: '#bda0ff', dim: '#6f879a',
  text: '#dbe8f3', panel: '#101820', selected: '#173241', line: '#2a4354',
}

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
  const initial = { selected: 0, scroll: 0, p1: false }
  const state = surface.state || initial
  const workRows = (props.nodes || []).filter(row => !state.p1 || row.priority === 'P1')
  const sideRows = (props.receipts || []).filter(row => !state.p1 || row.priority === 'P1')
  const rows = props.tab === 'side' ? sideRows : workRows
  const selected = Math.max(0, Math.min(state.selected || 0, Math.max(0, rows.length - 1)))
  const setSelection = next => {
    const index = Math.max(0, Math.min(next, Math.max(0, rows.length - 1)))
    const visible = Math.max(3, height - (props.tab === 'side' ? 13 : 12))
    let scroll = state.scroll || 0
    if (index < scroll) scroll = index
    if (index >= scroll + visible) scroll = index - visible + 1
    surface.setState({ ...state, selected: index, scroll })
  }
  surface.onKey(event => {
    if (event.key === 'up' || event.key === 'k') setSelection(selected - 1)
    else if (event.key === 'down' || event.key === 'j') setSelection(selected + 1)
    else if (event.key === 'pageup') setSelection(selected - 8)
    else if (event.key === 'pagedown') setSelection(selected + 8)
    else if (event.key === 'home') setSelection(0)
    else if (event.key === 'end') setSelection(rows.length - 1)
    else if (event.key === 'p') surface.setState({ ...state, p1: !state.p1, selected: 0, scroll: 0 })
    else if (event.key === 'w') surface.post({ action: 'tab', tab: 'work' })
    else if (event.key === 's') surface.post({ action: 'tab', tab: 'side' })
    else if (event.key === 'g') surface.post({ action: 'tab', tab: 'settings' })
    else if (event.key === 'r') surface.post({ action: 'refresh' })
    else if (event.key === 'f' && props.tab === 'work' && rows[selected])
      surface.post({ action: 'focus', id: rows[selected].id })
    else if (event.key === 'return' && rows[selected])
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
    const index = (state.scroll || 0) + event.y - hitStart
    if (index >= 0 && index < rows.length) setSelection(index)
  })

  const line = (key, value, color = C.text, extra = {}) => Text({ key, wrap: 'truncate-end', color, ...extra,
    children: clip(value, width) })
  const boxLine = (key, children, extra = {}) => Box({ key, height: 1, overflow: 'hidden', flexShrink: 0,
    flexDirection: 'row', ...extra, children })
  const children = []

  // Mockup-faithful status header: Work progress, parallel lanes and selected side model.
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
  const max = Math.max(1, props.parallel?.max || 1)
  const active = Math.max(0, props.parallel?.active || 0)
  children.push(boxLine('lanes', [
    Text({ color: C.blue, children: 'PARALLEL ' }),
    Text({ color: C.green, children: active + '/' + max + ' ' }),
    Text({ color: C.dim, children: '□'.repeat(Math.max(0, max - active)) }),
    Text({ color: C.cyan, children: '■'.repeat(Math.min(max, active)) }),
    Text({ color: C.red, children: props.parallel?.reserved ? ' +P1' : '' }),
    Text({ color: C.dim, children: clip('  M ' + (props.model || 'inherit'), Math.max(0, width - max - 25)) }),
  ]))
  children.push(line('rule', '─'.repeat(width), C.line))

  // At the requested 90-column dock, match the reviewed overview: Work tree
  // and SSE flow are visible together. The active tab only chooses which side
  // owns ↑↓/Enter and the shared inspector; it never hides the other half.
  if (width >= 48) {
    const workWidth = Math.floor(width * .58)
    const sideWidth = width - workWidth - 1
    const bodyRows = Math.max(7, height - 13)
    const workChildren = [Text({ color: C.cyan, weight: 'bold', children: pad('WORK TREE ' + workRows.length, workWidth) })]
    const workPage = workRows.slice(state.scroll || 0, (state.scroll || 0) + bodyRows)
    for (const [offset, node] of workPage.entries()) {
      const index = (state.scroll || 0) + offset
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
    const sideChildren = [
      Text({ color: C.cyan, weight: 'bold', children: pad('SSE FLOW ' + sideRows.length, sideWidth) }),
      Text({ color: C.dim, children: clip('IN ' + counts.received + '  AI ' + counts.processing
        + '  ANSWER ' + counts.answered + '  WORK ' + counts.work, sideWidth) }),
    ]
    const activeActors = (props.actors || []).slice(0, 3)
    if (activeActors.length) {
      sideChildren.push(Text({ color: C.pink, weight: 'bold', children: clip('ACTIVE FORKED SSE SESSIONS', sideWidth) }))
      for (const actor of activeActors) sideChildren.push(Text({ key: 'wide-actor-' + actor.id, color: C.cyan,
        children: clip('▶ ' + actor.id + ' · ' + (actor.model || 'inherit'), sideWidth) }))
    } else sideChildren.push(Text({ color: C.dim, children: 'No active side fork' }))
    const sideLimit = Math.max(2, bodyRows - sideChildren.length)
    for (const [index, receipt] of sideRows.slice(0, sideLimit).entries()) {
      const selectedRow = props.tab === 'side' && index === selected
      sideChildren.push(Box({ key: 'wide-side-' + receipt.id, height: 1, overflow: 'hidden',
        backgroundColor: selectedRow ? C.selected : undefined, children: [Text({ color: stageColor(stage(receipt)),
          children: clip((selectedRow ? '▸ ' : '  ') + (receipt.priority || '--') + ' ' + stage(receipt)
            + ' ' + receipt.summary, sideWidth) })] }))
    }
    while (sideChildren.length < bodyRows + 1) sideChildren.push(Text({ key: 'side-space-' + sideChildren.length, children: ' ' }))
    children.push(Box({ key: 'wide-body', height: bodyRows + 1, overflow: 'hidden', flexDirection: 'row', children: [
      Box({ key: 'wide-work', width: workWidth, height: bodyRows + 1, overflow: 'hidden',
        flexDirection: 'column', children: workChildren }),
      Box({ key: 'wide-side', width: sideWidth + 1, height: bodyRows + 1, overflow: 'hidden',
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
    children.push(line('footer', 'click header: Work/SSE/Settings · ↑↓ move · W/S · Enter · P P1 · F focus · R refresh', C.dim))
    return Box({ flexDirection: 'column', backgroundColor: C.panel, children })
  }

  if (props.tab === 'side') {
    const counts = { received: 0, processing: 0, answered: 0, work: 0, failed: 0 }
    for (const row of sideRows) counts[stage(row)] = (counts[stage(row)] || 0) + 1
    children.push(boxLine('side-head', [
      Text({ color: C.cyan, weight: 'bold', children: 'SSE FLOW ' + sideRows.length }),
      Text({ color: state.p1 ? C.red : C.dim, children: state.p1 ? ' P1 ONLY' : ' ALL · P P1' }),
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
        '▶ ' + clip(actor.id, 10) + ' · ' + clip(actor.model || 'inherit', 18) + ' · ' + clip(actor.receipt, 12), C.cyan))
    } else children.push(line('no-fork', 'No active side fork', C.dim))
    const visible = Math.max(3, height - 13 - actors.length)
    const page = sideRows.slice(state.scroll || 0, (state.scroll || 0) + visible)
    children.push(line('receipt-head', 'RECEIPTS · ↑↓ MOVE · ENTER INSPECT', C.dim))
    for (const [offset, receipt] of page.entries()) {
      const index = (state.scroll || 0) + offset
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
      Text({ color: state.p1 ? C.red : C.dim, children: state.p1 ? ' P1 ONLY' : ' ALL · P P1' }),
    ], { justifyContent: 'space-between' }))
    const visible = Math.max(3, height - 12)
    const page = workRows.slice(state.scroll || 0, (state.scroll || 0) + visible)
    for (const [offset, node] of page.entries()) {
      const index = (state.scroll || 0) + offset
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
  children.push(line('footer', 'click header: Work/SSE/Settings · ↑↓ move · W/S · Enter · P P1 · F focus · R refresh', C.dim))
  return Box({ flexDirection: 'column', backgroundColor: C.panel, children })
}
