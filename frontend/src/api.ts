export async function api<T=any>(path:string, method='GET', data?:unknown):Promise<T> {
  const response = await fetch('/api'+path,{method,headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined});
  const body = await response.json();
  if(!response.ok) {
    const detail = body.detail;
    throw new Error(typeof detail==='string'?detail:detail?.message || JSON.stringify(detail));
  }
  return body;
}
