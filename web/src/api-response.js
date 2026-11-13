export async function readApiResponse(response) {
 let value;
 try {
  value = await response.json();
 } catch {
  throw Error(response.ok
   ? 'サーバーから予期しない形式の応答がありました。画面を再読み込みしてください。'
   : `サーバーとの通信に失敗しました（HTTP ${response.status}）。画面を再読み込みしてください。`);
 }
 if (!response.ok) {
  throw Error(value?.error?.message || value?.error?.code || `操作できませんでした（HTTP ${response.status}）`);
 }
 return value;
}
