const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
require('../ducky_app/frontend/ui_web/web/node_modules/esbuild').build({entryPoints:[path.join(__dirname,'core-entry.ts')],bundle:true,write:false,format:'iife',plugins:[{name:'local-source',setup(build){build.onResolve({filter:/.*/},args=>({path:path.isAbsolute(args.path)?args.path:path.resolve(path.dirname(args.importer),args.path),namespace:'source'}));build.onLoad({filter:/.*/,namespace:'source'},args=>({contents:fs.readFileSync(args.path,'utf8'),loader:'ts'}));}}]}).then(result=>{
fs.writeFileSync(path.join(__dirname,'core.js'),result.outputFiles[0].contents);
http.createServer((req,res)=>{
  const name = req.url.split('?')[0].slice(1) || 'index.html';
  if (!['index.html','disabled.html','panel.js','panel.css','hero.js','hero.css','hero.disabled.js','hero.disabled.css','shared.css','core.js','check.js'].includes(name)) {res.writeHead(404).end();return;}
  res.setHeader('Content-Type',name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html');
  res.end(fs.readFileSync(path.join(__dirname,name)));
}).listen(34330,'127.0.0.1',()=>console.log('Profile settings check ready on http://127.0.0.1:34330'));
});
