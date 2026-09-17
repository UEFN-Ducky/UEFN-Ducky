const results=document.querySelector('#results');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
window.duckyosLanguageSwitcherInit=root=>{if(!root.querySelector('.ducky-language-menu'))root.innerHTML='<button class="ducky-language-menu">Language</button>'};
window.DuckyUI={modal:{open(){return Promise.resolve(false)}}};
const script=document.createElement('script');script.src=new URLSearchParams(location.search).get('script')==='original'?'/original.js':'/fixed.js';document.body.append(script);
const identity=()=>{document.querySelector('.user-profile-body').innerHTML='<form class="user-profile-form"><div class="user-profile-layout"><div class="user-profile-details">Test account</div></div></form>'};
const pc=(live=false)=>'<li class="ud-remote__pc">Studio PC'+(live?'<span class="ud-remote__pc-live">Live</span>':'')+'<button class="ud-remote__pc-remove">Remove</button></li>';
function route(profile,replace=false){
  history.pushState({},'',profile?'/profile':'/');
  let root=document.querySelector('#app-content');
  if(replace){const next=document.createElement('main');next.id='app-content';root.replaceWith(next);root=next}
  root.innerHTML=profile?'<section class="b-user-profile"><div class="user-profile-body">Loading…</div></section><section class="ud-root ud-remote"><header class="ud-remote__head">Connect to a PC<button data-ud-retry hidden>Retry</button></header><ul class="ud-remote__pcs"></ul></section><section class="b-account-security"><h3>Security</h3><div class="account-security-body"></div></section>':'Home';
  document.dispatchEvent(new CustomEvent('ducky:route-applied'));
}
function check(label,pred){results.textContent+='\n'+(pred?'PASS ':'FAIL ')+label;if(!pred)results.dataset.failed='1'}
function checkIdentity(label){check(label,document.querySelectorAll('.b-user-profile .user-profile-logout').length===1&&document.querySelectorAll('.b-user-profile .user-profile-lang .ducky-language-menu').length===1)}
document.querySelector('#run').onclick=async()=>{
  results.textContent='Running';delete results.dataset.failed;
  route(true);await pause(80);identity();await pause(80);checkIdentity('first SPA visit');
  route(false);route(true);await pause(80);identity();await pause(80);checkIdentity('return visit with persistent main');
  document.querySelector('.ud-remote__pcs').innerHTML=pc(true);await pause(80);
  check('PC refresh: add card, live status and remove icon',document.querySelectorAll('.ud-remote__pc--add').length===1&&!!document.querySelector('.ud-remote__pc-dot--live')&&!!document.querySelector('.ud-remote__pc-remove svg'));
  document.querySelector('.ud-root.ud-remote').outerHTML='<section class="ud-root ud-remote"><header class="ud-remote__head">Connect to a PC<button data-ud-retry hidden>Retry</button></header><ul class="ud-remote__pcs">'+pc(false)+'</ul></section>';
  await pause(80);check('replacement PC component gets refresh and offline status',!!document.querySelector('[data-uefn-refresh-pcs]')&&!!document.querySelector('.ud-remote__pc-dot--off'));
  identity();await pause(80);checkIdentity('identity body replaced after mount');
  const heading=document.querySelector('.b-account-security > h3');heading.click();
  document.querySelector('.account-security-body').innerHTML='<div class="registry-tabs"><button class="registry-tab-btn" data-tab="sessions"><span>Sessions</span></button></div>';
  await pause(80);check('security loads late and keeps expanded state',document.querySelector('.b-account-security').classList.contains('is-open')&&!!document.querySelector('.registry-tab-btn svg'));
  route(false);route(true,true);await pause(3300);identity();await pause(80);checkIdentity('slow hydration beyond old three-second timeout');
  route(false);await pause(30);route(true,true);await pause(80);identity();await pause(80);checkIdentity('replacement main and repeated navigation');
  route(false);document.querySelector('#app-content').innerHTML='<ul class="ud-remote__pcs"></ul>';await pause(80);
  check('profile observer stops on route exit',!document.querySelector('.ud-remote__pc--add'));
  results.textContent+='\n'+(results.dataset.failed?'FAILED':'ALL PASSED');
};
