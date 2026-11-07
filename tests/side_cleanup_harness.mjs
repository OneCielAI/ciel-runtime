import {startSide} from './side-intake.js'
const stopped=[]
const $={
 session:{id:async()=>'parent'},
 agent:{list:async()=>[
  {id:'failed-side',spawnedBy:'ciel-work',type:'ciel-work:side-intake',status:'failed',description:'old'},
  {id:'idle-side',spawnedBy:'ciel-work',type:'ciel-work:side-intake',status:'idle',description:'old'},
  {id:'finished-fork',spawnedBy:'ciel-work',type:'fork',status:'completed',description:'Side SSE abcdef123456'},
  {id:'foreign',spawnedBy:'other',type:'fork',status:'idle',description:'Side SSE foreign'},
 ],register:async()=>{}},
 tool:{list:async()=>[],call:async args=>{stopped.push(args);return {text:'ok'}}},
 mcp:{connect:async()=>({})},
 env:{get:async()=>''},ui:{invalidate:()=>{}},clock:{after:()=>({cancel(){}})},
}
await startSide($)
console.log(JSON.stringify(stopped))
