import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {timingSafeEqual} from 'node:crypto';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StreamableHTTPClientTransport} from '@modelcontextprotocol/sdk/client/streamableHttp.js';

export const runtime='nodejs';
export const dynamic='force-dynamic';
const allowed=new Set(['health_check','get_instance_info','list_databases','list_datasets','get_dataset_info',
  'create_virtual_dataset','generate_chart','get_chart_info','get_chart_data','generate_dashboard','get_dashboard_info']);

export async function POST(request:Request){
  const headers={'Cache-Control':'no-store'};
  if(request.headers.get('origin'))return Response.json({error:'Internal requests only'},{status:403,headers});
  try{
    const directory=process.env.DATAPILOT_SUPERSET_DIR||resolve(process.cwd(),'../.local/superset');
    const expected=Buffer.from((await readFile(resolve(directory,'secrets/relay_key'),'utf8')).trim());
    const supplied=Buffer.from(request.headers.get('x-datapilot-relay-key')||'');
    if(expected.length!==supplied.length||!timingSafeEqual(expected,supplied))return Response.json({error:'Unauthorized'},{status:401,headers});
    const authorization=request.headers.get('authorization')||'';
    if(!authorization.startsWith('Bearer '))return Response.json({error:'Missing MCP authentication'},{status:401,headers});
    const text=await request.text();
    if(text.length>100000)return Response.json({error:'Request too large'},{status:413,headers});
    const body=JSON.parse(text);
    if(typeof body.name!=='string'||!allowed.has(body.name)||!body.arguments||typeof body.arguments!=='object'||Array.isArray(body.arguments)){
      return Response.json({error:'Unsupported tool'},{status:400,headers});
    }
    const client=new Client({name:'datapilot-local',version:'1.0.0'});
    const transport=new StreamableHTTPClientTransport(new URL('http://127.0.0.1:5008/mcp'),{
      requestInit:{headers:{Authorization:authorization}},reconnectionOptions:{maxRetries:0,
        initialReconnectionDelay:1000,maxReconnectionDelay:1000,reconnectionDelayGrowFactor:1},
    });
    try{
      await client.connect(transport);
      const result=await client.callTool({name:body.name,arguments:body.arguments},undefined,{timeout:90000});
      if(result.isError)return Response.json({error:'Superset rejected the tool call'},{status:502,headers});
      let data=result.structuredContent;
      if(!data){
        const content=result.content as {type:string;text?:string}[];
        const block=content.find(item=>item.type==='text'&&item.text);
        if(!block?.text)throw new Error('No structured tool result');
        data=JSON.parse(block.text);
      }
      return Response.json({data},{headers});
    }finally{await client.close();}
  }catch{
    return Response.json({error:'Local Superset MCP unavailable'},{status:503,headers});
  }
}
