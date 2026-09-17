import http from 'node:http';
import fs from 'node:fs';
const html = `<!doctype html><meta charset="utf-8"><title>Profile SPA regression</title>
<style>body{font:16px system-ui;margin:24px}button{padding:10px;margin:8px}.b-user-profile,.ud-remote,.b-account-security{border:1px solid #aaa;padding:16px}.user-profile-actions{display:flex;gap:16px}#results{white-space:pre-wrap}</style>
<h1>Profile SPA regression</h1><button id="run">Run regression checks</button><pre id="results">Ready</pre>
<header id="app-header"><div data-ducky-language></div></header><main id="app-content"></main>
<script src="/fixture.js"></script>`;
http.createServer((req,res)=>{
  const path=new URL(req.url,'http://localhost').pathname;
  if(['/original.js','/fixed.js','/fixture.js'].includes(path)) {
    res.setHeader('Content-Type','text/javascript');res.end(fs.readFileSync(new URL('.'+path,import.meta.url)));
  } else {res.setHeader('Content-Type','text/html');res.end(html)}
}).listen(34326,'127.0.0.1',()=>console.log('Profile regression fixture: http://127.0.0.1:34326/?script=original'));
