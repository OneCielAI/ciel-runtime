import {startSide,pollSide,completeSide,decisionOf} from './side-intake.js'

// 1. Fresh mode concurrency: at most 1 ordinary side-fresh actor at a time even
// when settings.max_active is 2 (KOBE 2026-11-25: fresh actors stacked 2-3 deep).
// The reserved P0/P1 slot stays (user 2026-11-14: P1 events go first; Andy
// 2026-11-25: a P0 waited 10 min behind a 14-min gate re-check), and nothing
// starts beyond those two. The claim stub honours urgent_only as the router does.
{
  const events = [
    {receipt_id:'1'.repeat(64),generation:'a'.repeat(32),text:'ev1',event_key:'s:1',lease_until:Date.now()/1000+120,route:{source_kind:'external_event'},workload:{version:1,truncated:false,nodes:[],queue_order:[]}},
    {receipt_id:'2'.repeat(64),generation:'b'.repeat(32),text:'ev2',event_key:'s:2',lease_until:Date.now()/1000+120,route:{source_kind:'external_event'},workload:{version:1,truncated:false,nodes:[],queue_order:[]}},
  ]
  const urgentEvent = {receipt_id:'3'.repeat(64),generation:'c'.repeat(32),text:'ev3',event_key:'s:3',source_priority:'P1',lease_until:Date.now()/1000+120,route:{source_kind:'external_event'},workload:{version:1,truncated:false,nodes:[],queue_order:[]}}
  const claims = []
  const spawns = []
  const hostStore = new Map()
  const $ = {
    state:{get:async ref=>{const h=hostStore.get(ref.plugin+'/'+ref.key);return {value:h?structuredClone(h.value):undefined,version:h?.version||0}},
      set:async(ref,v)=>{const n=ref.plugin+'/'+ref.key,h=hostStore.get(n)||{version:0};hostStore.set(n,{value:structuredClone(v),version:h.version+1});return {isSet:true,version:h.version+1}}},
    session:{model:async()=>'m',id:async()=>'p-fresh-conc',append:async()=>({uuid:'u'}),messages:async()=>[],info:async()=>({})},
    prompt:{compose:async()=>({sections:[{text:'SYS'}]}),submit:async()=>({})},
    mcp:{connect:async()=>({}),call:async(server,name,args)=>{
      const text = v => ({content:[{type:'text',text:JSON.stringify(v)}]})
      if (name === 'work_side_claim') {
        const index = args.urgent_only ? events.findIndex(e => e.source_priority === 'P0' || e.source_priority === 'P1') : (events.length ? 0 : -1)
        const claim = index >= 0 ? events.splice(index,1)[0] : null
        claims.push(args.urgent_only === true)
        return text({claim,settings:{max_active:2,session:'fresh'},handoff_replies:[],main_wakes:[]})
      }
      if (name === 'work_side_status') return text({pending_acks:[]})
      return text({})
    }},
    tool:{list:async()=>[],call:async()=>({})},
    agent:{list:async()=>[],register:async()=>({}),spawn:async args=>{spawns.push(args);return {agentId:'af-'+spawns.length}}},
    env:{get:async()=>'x'.repeat(40)},
    ui:{log:()=>{},status:()=>{},invalidate:()=>{}},
    clock:{after:()=>({cancel(){}})},
  }
  await startSide($)
  await pollSide($,'claude:p-fresh-conc')   // ev1: the ordinary fresh actor
  await pollSide($,'claude:p-fresh-conc')   // reserved claim, no P0/P1 waiting: ev2 stays queued
  const freshConcurrentSpawns = spawns.length
  events.push(urgentEvent)
  await pollSide($,'claude:p-fresh-conc')   // reserved claim takes the P1
  const freshUrgentWhileBusySpawns = spawns.length
  await pollSide($,'claude:p-fresh-conc')   // two running: no claim
  const freshFullSpawns = spawns.length
  const freshClaims = claims

  // 2. decisionOf repairs for live Celly/KOBE shapes + non-hanging rejection for
  // Prody/KOBE leading-brace answers without a decision object (hooks-worker crash root cause).
  const missingBrace = decisionOf('{"understanding":"요약","decision":{"action":"answer","kind":"status","summary":"게이트 유지","reply":"게이트 유지"}')
  const missingBraceWithExpected = decisionOf('{"understanding":"요약","decision":{"action":"answer","kind":"status","summary":"유지","reply":"유지","expected_version":198}')
  const missingUnderstanding = decisionOf('{"decision":{"action":"answer","kind":"status","summary":"게이트 재확인: 변동 없음","reply":"변동 없음","no_reply":true},"expected_version":0}')
  const missingNoReply = decisionOf('{"understanding":"확인","decision":{"action":"answer","kind":"status","summary":"중복 알림 확인, 기록만 남김","no_reply":true},"expected_version":0}')
  const escapedDecisionInSummary = decisionOf('Here: {"understanding":"요약","decision":{"action":"answer","kind":"status","summary":"about \\"decision\\" key","reply":"ok"},"expected_version":7}')
  // KOBE 2026-11-25 (f5ae6c68b340): the corrected answer listed a Walkie
  // conversation UUID as a bare JSON token and message ids as numbers in refs.
  let bareUuidRefs = null
  try {
    bareUuidRefs = decisionOf('\n\n{"understanding":"게이트 10건 판정","decision":{"action":"update","kind":"request","item_id":"n29","summary":"n29 해제: run 45571733507 성공","reply":"n29 해제","refs":[138846,160846,4c846761-5ce7-4f79-b65f-db09b5605413,226],"release_waits":[{"id":"n29","waiting_on":"run 45571733507 attempt 1 result"}]},"expected_version":0}')
  } catch (err) { bareUuidRefs = {error:String(err?.message || err)} }
  const uuidInsideText = decisionOf('{"understanding":"u","decision":{"action":"answer","kind":"status","summary":"대화 [4c846761-5ce7-4f79-b65f-db09b5605413] 확인","reply":"ok"},"expected_version":1}')
  const noDecisionErrors = []
  for (const sample of [
    '{"analysis_focus":"Ciel host gate re-check","answer":"게이트 6건 판정","understanding":"fresh"}',
    '{"analysis_focus":"all","blockers":[{"id":"n1121"}],"decision":"string-not-object"}',
    '}{"no_decision":true}',
  ]) {
    try { decisionOf(sample); noDecisionErrors.push('unexpected_ok') }
    catch (err) { noDecisionErrors.push(String(err?.message || err)) }
  }
  // A decision-shaped object that does not parse (a stray quote in summary).
  let unparsedError = 'unexpected_ok'
  try { decisionOf('{"understanding":"u","decision":{"action":"answer","kind":"status","summary":"a "b" c","reply":"r"},"expected_version":1}') }
  catch (err) { unparsedError = String(err?.message || err) }

  // 3. Correction for invalid priority, queue_position, and missing target Work item
  async function runCorrection(errText, badDecision) {
    let sent = 0, resolves = 0, failures = 0, claimed = false
    const hs = new Map()
    const h$ = {
      state:{get:async ref=>{const h=hs.get(ref.plugin+'/'+ref.key);return {value:h?structuredClone(h.value):undefined,version:h?.version||0}},
        set:async(ref,v)=>{const n=ref.plugin+'/'+ref.key,h=hs.get(n)||{version:0};hs.set(n,{value:structuredClone(v),version:h.version+1});return {isSet:true,version:h.version+1}}},
      session:{model:async()=>'m',id:async()=>'p-corr',append:async()=>({uuid:'u'}),messages:async()=>[],send:async()=>{sent++;return {isDelivered:true}},info:async()=>({})},
      prompt:{compose:async()=>({sections:[{text:'SYS'}]}),submit:async()=>({})},
      mcp:{connect:async()=>({}),call:async(server,name,args)=>{
        const text = v => ({content:[{type:'text',text:JSON.stringify(v)}]})
        if (name === 'work_side_claim') {
          const claim = claimed ? null : {receipt_id:'4'.repeat(64),generation:'d'.repeat(32),text:'ev',event_key:'s:4',lease_until:Date.now()/1000+120,route:{},workload:{version:2,truncated:false,nodes:[],queue_order:[]}}
          claimed = true
          return text({claim,settings:{max_active:1,session:'fresh'},handoff_replies:[],main_wakes:[]})
        }
        if (name === 'work_side_understanding' && args.state === 'prepare') {
          if (args.decision.priority === 'P9' || args.decision.queue_position === 0 || args.decision.item_id === '153865')
            return {isError:true,content:[{type:'text',text:errText}]}
          return text({state:'merged_into_reply'})
        }
        if (name === 'work_side_resolve') { resolves++; return text({receipt:{receipt_id:'4'.repeat(64),stage:'processed'},result:{action:'answer',duplicate:false,version:2}}) }
        if (name === 'work_side_fail') { failures++; return text({receipt:{receipt_id:'4'.repeat(64),stage:'received'}}) }
        return text({})
      }},
      tool:{list:async()=>[],call:async()=>({})},
      agent:{list:async()=>[],register:async()=>({}),spawn:async()=>({agentId:'ac-1'})},
      env:{get:async()=>'x'.repeat(40)},
      ui:{log:()=>{},status:()=>{},invalidate:()=>{}},
      clock:{after:()=>({cancel(){}})},
    }
    await startSide(h$)
    await pollSide(h$,'claude:p-corr')
    await completeSide(h$,{agentId:'ac-1',reason:'answer',answer:JSON.stringify({understanding:'u',decision:badDecision,expected_version:2})})
    await completeSide(h$,{agentId:'ac-1',reason:'answer',answer:JSON.stringify({understanding:'u',decision:{action:'answer',kind:'status',summary:'ok',reply:'ok'},expected_version:2})})
    return {sends:sent,resolves,failures}
  }
  const priorityCorrection = await runCorrection('invalid side decision priority', {action:'answer',kind:'status',summary:'s',reply:'r',priority:'P9'})
  const queuePositionCorrection = await runCorrection('invalid side decision queue_position', {action:'answer',kind:'status',summary:'s',reply:'r',queue_position:0})
  const missingTargetCorrection = await runCorrection('side target Work item missing', {action:'update',kind:'request',item_id:'153865',summary:'s',reply:'r'})

  // 4. Background task follow-up prose turn after a valid decision JSON turn (Andy 50f0bcedf586)
  let bgResolves = 0, bgFailures = 0, bgClaimed = false
  const hs2 = new Map()
  const bg$ = {
    state:{get:async ref=>{const h=hs2.get(ref.plugin+'/'+ref.key);return {value:h?structuredClone(h.value):undefined,version:h?.version||0}},
      set:async(ref,v)=>{const n=ref.plugin+'/'+ref.key,h=hs2.get(n)||{version:0};hs2.set(n,{value:structuredClone(v),version:h.version+1});return {isSet:true,version:h.version+1}}},
    session:{model:async()=>'m',id:async()=>'p-bg',append:async()=>({uuid:'u'}),
      messages:async()=>[
        {role:'assistant',text:'{"understanding":"10개 게이트 유지","decision":{"action":"answer","kind":"status","summary":"전부 유지","reply":"해제 0건"},"expected_version":11809}'},
        {role:'user',text:'[SYSTEM NOTIFICATION - NOT USER INPUT] background task completed'},
        {role:'assistant',text:'백그라운드 조회 완료. 이미 제출한 판정은 유효합니다.'},
      ],
      send:async()=>({isDelivered:false}),info:async()=>({})},
    prompt:{compose:async()=>({sections:[{text:'SYS'}]}),submit:async()=>({})},
    mcp:{connect:async()=>({}),call:async(server,name,args)=>{
      const text = v => ({content:[{type:'text',text:JSON.stringify(v)}]})
      if (name === 'work_side_claim') {
        const claim = bgClaimed ? null : {receipt_id:'5'.repeat(64),generation:'e'.repeat(32),text:'gate',event_key:'gate-check:1',lease_until:Date.now()/1000+120,route:{source_kind:'gate_check'},workload:{version:11809,truncated:false,nodes:[],queue_order:[]}}
        bgClaimed = true
        return text({claim,settings:{max_active:1,session:'fresh'},handoff_replies:[],main_wakes:[]})
      }
      if (name === 'work_side_understanding') return text({state:'merged_into_reply'})
      if (name === 'work_side_resolve') { bgResolves++; return text({receipt:{receipt_id:'5'.repeat(64),stage:'processed'},result:{action:'answer',duplicate:false,version:11809}}) }
      if (name === 'work_side_fail') { bgFailures++; return text({receipt:{receipt_id:'5'.repeat(64),stage:'received'}}) }
      return text({})
    }},
    tool:{list:async()=>[],call:async()=>({})},
    agent:{list:async()=>[],register:async()=>({}),spawn:async()=>({agentId:'abg-1'})},
    env:{get:async()=>'x'.repeat(40)},
    ui:{log:()=>{},status:()=>{},invalidate:()=>{}},
    clock:{after:()=>({cancel(){}})},
  }
  await startSide(bg$)
  await pollSide(bg$,'claude:p-bg')
  await completeSide(bg$,{agentId:'abg-1',reason:'answer',answer:'백그라운드 조회 완료. 이미 제출한 판정은 유효합니다.'})

  console.log(JSON.stringify({
    freshConcurrentSpawns, freshUrgentWhileBusySpawns, freshFullSpawns, freshClaims,
    missingBrace, missingBraceWithExpected, missingUnderstanding, missingNoReply,
    escapedDecisionInSummary, noDecisionErrors, unparsedError, bareUuidRefs, uuidInsideText,
    priorityCorrection, queuePositionCorrection, missingTargetCorrection,
    bgFollowup:{resolves:bgResolves,failures:bgFailures},
  }))
}
