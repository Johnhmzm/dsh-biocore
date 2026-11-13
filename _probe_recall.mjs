import { mkdtempSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { pathToFileURL } from 'node:url'
const root = process.cwd()
const host = await import(pathToFileURL(join(root,'lib','index.js')).href)
const dataDir = mkdtempSync(join(tmpdir(),'mw-'))
const listeners = new Map()
const ctx = { tools: [], contexts: [], routes: [], commands: [],
  on(n,f){ if(!listeners.has(n)) listeners.set(n,[]); listeners.get(n).push(f); return ()=>{} },
  inject(names,cb){ if(names.includes('tools')) cb({ tools:{ register(d){ctx.tools.push(d); return ()=>{}} } })
    else if(names.includes('systemPrompt')) cb({ systemPrompt:{ context(d){ctx.contexts.push(d); return ()=>{}} } })
    else if(names.includes('webServer')) cb({ webServer:{ register(){ return ()=>{} } } })
    else if(names.includes('commands')) cb({ commands:{ register(){ return ()=>{} } } }) } }
host.apply(ctx, { dataDir, memoryEnabled: true, injectMaxChars: 420, hourlyCap: 20 })
const user=(t)=>({type:'user/message',data:{source:{kind:'user'},content:[{type:'text',text:t}]}})
for (const l of listeners.get('session/event')||[]) { try { l({id:'sess-a'}, user('我叫小林，今天在杭州西湖边出差')) } catch(e){} }
await new Promise(r=>setTimeout(r,4000))
const tool = ctx.tools.find(t=>t.name==='biocore_recall')
console.log('tool?', !!tool)
console.log('A', JSON.stringify(await tool.execute({query:'西湖',limit:3},{agent:{sessionId:'sess-a'}})).slice(0,160))
console.log('B', JSON.stringify(await tool.execute({query:'西湖',limit:3},{sessionId:'sess-a'})).slice(0,160))