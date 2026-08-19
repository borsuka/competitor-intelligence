/**
 * A fake competitor website for end-to-end tests.
 *
 * The real crawler crawls this: real HTTP, real redirects, real HTML parsing, real price
 * extraction. Only the internet is replaced. That is the point — a suite that also stubs
 * the crawler would prove the UI works and nothing about whether the product does.
 *
 * The pricing is mutable at runtime through /__set-price, so a test can raise a plan's
 * price between two analyses and assert that change detection notices.
 *
 *   node e2e/fixtures/competitor-site.mjs [port]
 */

import { createServer } from "node:http";

const port = Number(process.argv[2] ?? process.env.FIXTURE_PORT ?? 4319);

// Mutated by /__set-price so a second crawl sees a different site.
const state = {
  proPrice: 49,
  extraProduct: null,
};

const layout = (title, body) => `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>${title} — Northwind Analytics</title>
  <meta name="description" content="Privacy-first product analytics for growing teams.">
  <meta property="og:title" content="Northwind Analytics">
  <meta property="og:description" content="Privacy-first product analytics.">
  <meta property="og:type" content="website">
  <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Organization","name":"Northwind Analytics"}
  </script>
</head>
<body>
  <nav><a href="/">Home</a> <a href="/pricing">Pricing</a> <a href="/features">Features</a>
       <a href="/about">About</a> <a href="/blog">Blog</a></nav>
  <main>${body}</main>
  <footer>
    <a href="https://twitter.com/northwind">Twitter</a>
    <a href="https://linkedin.com/company/northwind">LinkedIn</a>
    <a href="https://github.com/northwind">GitHub</a>
    <p>© 2026 Northwind Analytics</p>
  </footer>
</body>
</html>`;

const pages = {
  "/": () =>
    layout(
      "Product analytics without the creep",
      `<h1>Product analytics without the creep</h1>
       <p>Northwind helps product teams understand what users actually do, without
          shipping their data to three continents. Set up in under five minutes.</p>
       <h2>Dashboards</h2>
       <p>Answer the obvious questions without writing SQL.</p>
       <h2>Funnels</h2>
       <p>See where people drop out, step by step.</p>
       ${state.extraProduct ? `<h2>${state.extraProduct}</h2><p>Newly launched.</p>` : ""}
       <a href="/pricing">See pricing</a>
       <a href="/signup">Start free trial</a>`,
    ),

  "/pricing": () =>
    layout(
      "Pricing",
      `<h1>Simple, published pricing</h1>
       <h2>Free</h2>
       <p>EUR 0 per month. Up to 10,000 events. For side projects.</p>
       <h2>Pro</h2>
       <p>EUR ${state.proPrice} per month. Unlimited events, funnels and dashboards.</p>
       <h2>Enterprise</h2>
       <p>Contact sales for pricing. SSO, audit logs and a data processing agreement.</p>
       <a href="/signup">Start free trial</a>`,
    ),

  "/features": () =>
    layout(
      "Features",
      `<h1>Features</h1>
       <ul>
         <li>Funnels</li><li>Retention cohorts</li><li>Custom dashboards</li>
         <li>EU data residency</li><li>CSV export</li><li>Public API</li>
         ${state.extraProduct ? `<li>${state.extraProduct}</li>` : ""}
       </ul>
       <a href="/signup">Start free trial</a>`,
    ),

  "/about": () =>
    layout(
      "About",
      `<h1>About Northwind</h1>
       <p>Six people, based in Rotterdam, building analytics we would want to use
          ourselves. Independent and profitable since 2023.</p>`,
    ),

  "/blog": () =>
    layout(
      "Blog",
      `<h1>Blog</h1>
       <article><h2>Why we do not use cookies</h2><p>A short explanation.</p></article>`,
    ),
};

const server = createServer((request, response) => {
  const url = new URL(request.url ?? "/", `http://127.0.0.1:${port}`);

  // Test control surface. Not part of the crawled site.
  if (url.pathname === "/__set-price") {
    state.proPrice = Number(url.searchParams.get("pro") ?? state.proPrice);
    const extra = url.searchParams.get("product");
    state.extraProduct = extra === "" ? null : (extra ?? state.extraProduct);
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify(state));
    return;
  }

  if (url.pathname === "/__reset") {
    state.proPrice = 49;
    state.extraProduct = null;
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify(state));
    return;
  }

  if (url.pathname === "/robots.txt") {
    // Deliberately permissive but non-empty: the crawler reads and parses this, and a
    // sitemap directive exercises the discovery path.
    response.writeHead(200, { "content-type": "text/plain" });
    response.end(`User-agent: *\nAllow: /\nDisallow: /signup\nSitemap: http://127.0.0.1:${port}/sitemap.xml\n`);
    return;
  }

  if (url.pathname === "/sitemap.xml") {
    const urls = ["/", "/pricing", "/features", "/about", "/blog"]
      .map((path) => `<url><loc>http://127.0.0.1:${port}${path}</loc></url>`)
      .join("");
    response.writeHead(200, { "content-type": "application/xml" });
    response.end(`<?xml version="1.0" encoding="UTF-8"?><urlset>${urls}</urlset>`);
    return;
  }

  // A redirect, so the manual per-hop validation in the fetcher is exercised too.
  if (url.pathname === "/plans") {
    response.writeHead(302, { location: "/pricing" });
    response.end();
    return;
  }

  const page = pages[url.pathname];
  if (!page) {
    response.writeHead(404, { "content-type": "text/html; charset=utf-8" });
    response.end(layout("Not found", "<h1>Not found</h1>"));
    return;
  }

  response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
  response.end(page());
});

// No host argument, so Node binds the dual-stack wildcard. The comparison test adds the
// same server twice, as 127.0.0.1 and as localhost, to get two distinct competitor
// domains — and localhost resolves to ::1 first on most machines.
server.listen(port, () => {
  console.log(`competitor fixture site listening on port ${port}`);
});
