import {startSide} from './side-intake.js'
const stopped=[]
const $={
 session:{id:async()=>'parent'},
 agent:{list:async()=>[{id:'failed-side',spawnedBy:'ciel-work',type:'ciel-work:side-intake',status:'failed',description:'old'}],register:async()=>{}},
 tool:{list:async()=>[],call:async args=>{stopped.push(args);return {text:'ok'}}},
 mcp:{connect:async()=>({})},
 env:{get:async()=>''},ui:{invalidate:()=>{}},clock:{after:()=>({cancel(){}})},
}
await startSide($)
console.log(JSON.stringify(stopped))
