// Calls a method on a Cloudflare RPC binding (such as the EMAIL sender) from JavaScript.
// Python can't hold the "RPC promise" such a call returns, so this hands Python an ordinary
// Promise that resolves to plain data. Used by shop/cloud.py → call_binding().
export function callBinding(binding, method, ...args) {
  return Promise.resolve()
    .then(() => binding[method](...args))
    .then((result) => JSON.parse(JSON.stringify(result ?? null)));
}
