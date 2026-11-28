import assert from 'node:assert/strict'
import {applyWalkieSourceStatus} from './side-intake.js'
const job = {server:'walkie', target:{conversation_id:'room',message_id:'163544'}, memo:'registered n3018', ack_key:'ack-key', complete_key:'complete-key', phase:'ack', send:true}
let row = {agent_id:'self',state:'ack',version:1,memo:'received'}
const writes = []
let apply = true
let loseResponse = false
const response = value => ({content:[{type:'text',text:JSON.stringify(value)}]})
const $ = {mcp:{async call(server,tool,args) {
  assert.equal(server,'walkie')
  if (tool === 'walkie_identity') return response({user:{id:'self'}})
  if (tool === 'walkie_call_tool') {
    assert.equal(args.operation_id,'messages.work.get')
    assert.deepEqual(args.arguments,job.target)
    return response([{agent_id:'other',state:'completed'},row])
  }
  assert.equal(tool,'walkie_messages_work_update')
  writes.push(args)
  if (apply) row = {...row,state:args.state,version:args.version+1,memo:args.memo ?? null}
  if (loseResponse) throw new Error('connection lost')
  return response(row)
}}}
assert.equal(await applyWalkieSourceStatus($,{...job,phase:'completed'}),'uncertain')
assert.equal(writes.length,0)
apply = false
assert.equal(await applyWalkieSourceStatus($,job),'uncertain')
assert.equal(await applyWalkieSourceStatus($,{...job,send:false}),'uncertain')
assert.equal(writes.length,1)
apply = true
loseResponse = true
row = {...row,state:'ack',version:2,memo:job.memo}
assert.equal(await applyWalkieSourceStatus($,{...job,send:false}),'submitted')
assert.equal(await applyWalkieSourceStatus($,{...job,phase:'completed'}),'submitted')
assert.equal(await applyWalkieSourceStatus($,{...job,phase:'completed',send:false}),'submitted')
assert.deepEqual(writes,[
  {...job.target,version:1,state:'ack',client_id:'ack-key',memo:job.memo},
  {...job.target,version:2,state:'completed',client_id:'complete-key'},
])
row = {...row,state:'ack',version:7,memo:'received'}
assert.equal(await applyWalkieSourceStatus($,job),'submitted')
assert.equal(row.version,8)
