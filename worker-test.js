// Midlertidig test — erstattes af Python worker når Pages Functions er bekræftet
export default {
  async fetch(request) {
    return new Response("MadShopper worker OK: " + new URL(request.url).pathname, {
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
  },
};
