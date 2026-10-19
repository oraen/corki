// Playwright waitForFunction treats a returned Promise as truthy. Poll async
// host queries here, awaiting the actual value on every attempt instead.
export async function poll(check, {timeout=20000, label='asynchronous condition'}={}) {
  const deadline=Date.now()+timeout;
  do {
    const value=await check();
    if(value)return value;
    await new Promise(resolve=>setTimeout(resolve,50));
  } while(Date.now()<deadline);
  throw new Error('Timed out waiting for '+label);
}
