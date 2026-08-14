/**
 * Optional path proxy for otrservices.com/market*.
 *
 * Configure a Worker route for:
 *   otrservices.com/market*
 *
 * Add a Worker variable named MARKET_ORIGIN containing the HTTPS origin where
 * the OTR FastAPI dashboard is running, for example:
 *   https://market-origin.example.com
 */
export default {
  async fetch(request, env) {
    if (!env.MARKET_ORIGIN) {
      return new Response("MARKET_ORIGIN is not configured", { status: 500 });
    }

    const incoming = new URL(request.url);
    if (!incoming.pathname.startsWith("/market")) {
      return new Response("Not found", { status: 404 });
    }

    const origin = new URL(env.MARKET_ORIGIN);
    incoming.protocol = origin.protocol;
    incoming.host = origin.host;

    const proxied = new Request(incoming.toString(), request);
    return fetch(proxied);
  },
};
