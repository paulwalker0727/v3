// Dev-server proxy: serves the ORIGINAL project UI (Jinja templates, rendered by the
// FastAPI backend on :8001) through the frontend port, because only /api/* is routed
// to the backend by the ingress.
const { createProxyMiddleware } = require('http-proxy-middleware');

const TARGET = 'http://127.0.0.1:8001';
const STATIC_FILES = ['/static/app.css', '/static/app.js'];

module.exports = function (app) {
  const filter = (pathname) =>
    pathname === '/' ||
    pathname === '/admin' || pathname.startsWith('/admin/') ||
    pathname === '/portal' || pathname.startsWith('/portal/') ||
    STATIC_FILES.includes(pathname);
  app.use(createProxyMiddleware({ pathFilter: filter, target: TARGET, changeOrigin: false, xfwd: true }));
};
