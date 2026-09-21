// Preserve existing scheduling/cancellation regression fixtures at the account API boundary.
// The real backend transport and account UI are tested separately in cardhobby-account.test.cjs.
const legacy = require('../lib/cardhobby-cost-search.ts');
let fixtureFetch = global.fetch;
const response = data => ({ok:true,status:200,json:async()=>data});
Object.defineProperty(global, 'fetch', {
  configurable:true,
  get: () => { const current = fixtureFetch; return async (url, options) => {
    if (String(url).endsWith('/cardhobby/search')) return response(await legacy.searchLegacyCardHobbyOrders(JSON.parse(options.body).keywords,options.signal));
    if (String(url).endsWith('/cardhobby/status')) {
      await legacy.connectLegacyCostSearch(options?.signal);
      return response({saved:true,state:'connected'});
    }
    return current(url,options);
  }; },
  set: value => { fixtureFetch = value; },
});
