const results = document.querySelector('#results');
let photoOpens=0, saves=0;
document.querySelector('[data-profile-photo]').addEventListener('click',e=>{e.preventDefault();photoOpens++;});
document.addEventListener('submit',e=>{if(!e.target.matches('[data-acct-profile]'))return;e.preventDefault();saves++;e.target.querySelector('[data-acct-profile-status]').hidden=false;e.target.querySelector('[data-acct-profile-status]').textContent='Name updated.';});
document.querySelector('#theme').onclick=()=>document.body.classList.toggle('light');
const settle=()=>new Promise(r=>setTimeout(r,520));
function assert(v,msg){if(!v)throw Error(msg);results.textContent+='PASS '+msg+'\n';}
document.querySelector('#run').onclick=async()=>{
results.textContent='';
try{
await settle();
assert(!document.querySelector('.b-user-profile .user-profile-logout'),'Log out is removed from the profile header');
assert(document.querySelector('.user-profile-actions').children.length===1&&!!document.querySelector('.user-profile-actions .user-profile-lang'),'Only the language flag remains in header actions');
let add=document.querySelector('.ud-remote__pcs .ud-remote__pc--add');
assert(!add.hidden&&getComputedStyle(add).display!=='none','Existing hidden plus card is visible in the browser');
add.click();assert(window.testModalCalls.at(-1)==='Connect to a PC','Plus card opens the existing connection dialog');
document.querySelector('.user-profile-name-display').click();await settle();
const modal=document.querySelector('#uefn-security-modal');
assert(!!modal,'Name opens profile settings');
const logout=modal.querySelector('[data-uefn-account-logout] .user-profile-logout');
assert(!!logout&&logout.method==='post'&&new URL(logout.action).pathname==='/logout','General contains the original logout form');
assert(!logout.parentElement.closest('form'),'Logout form is outside the name form');
assert(getComputedStyle(logout.querySelector('span')).display!=='none','Log out has a visible text label');
const form=modal.querySelector('[data-acct-profile]'),input=form.querySelector('input'),save=form.querySelector('[type=submit]');
const ir=input.getBoundingClientRect(),sr=save.getBoundingClientRect();
assert(sr.left>ir.left&&sr.right<ir.right&&sr.top>ir.top&&sr.bottom<ir.bottom,'Save is inside the display name field');
assert(form.querySelector('label').htmlFor===input.id,'Display name label stays associated');
save.click();await settle();assert(saves===1,'Original form submit still works');
assert(modal.querySelectorAll('.uefn-account-details dt').length===2,'Email and Membership have separate labeled rows');
modal.querySelector('.uefn-avatar-change').click();assert(photoOpens===1,'Photo control opens original file input');
assert(modal.querySelector('.uefn-avatar-preview').textContent==='A','Avatar preview shows current initial');
const avatar=document.querySelector('.user-profile-avatar');
avatar.innerHTML='<img src="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%2240%22 height=%2240%22%3E%3Ccircle cx=%2220%22 cy=%2220%22 r=%2220%22 fill=%22gold%22/%3E%3C/svg%3E" alt="">';
await settle();assert(modal.querySelector('.uefn-avatar-preview img').src===avatar.querySelector('img').src,'Photo preview follows uploaded avatar changes');
for(const tab of modal.querySelectorAll('[data-tab]')){
tab.click();await settle();
const indicator=modal.querySelector('.registry-tabs-indicator'),a=tab.getBoundingClientRect(),b=indicator.getBoundingClientRect(),s=getComputedStyle(indicator);
assert(s.backgroundColor!=='rgba(0, 0, 0, 0)'&&Number(s.opacity)>0,'Visible highlight on '+tab.textContent);
assert(Math.abs(a.left-b.left)<2&&Math.abs(a.width-b.width)<2&&Math.abs(a.top-b.top)<2,'Highlight aligns with '+tab.textContent);
}
modal.querySelector('[data-uefn-security-close]').click();
document.querySelector('.user-profile-avatar-btn').click();await settle();
assert(document.querySelectorAll('.uefn-name-control').length===1,'Reopening does not duplicate controls');
assert(document.querySelectorAll('.uefn-avatar-preview').length===1,'Reopening preserves one photo preview');
assert(document.querySelectorAll('.user-profile-logout').length===1,'Reopening preserves exactly one logout form');
const general=document.querySelector('#acct-1-general'),original=general.innerHTML;
general.innerHTML='<section class="account-security__section" data-acct-section="profile"><form data-acct-profile class="account-security__form"><label>Display name<input name="displayName" value="Alex Ducky"></label><p class="tm-muted">alex@example.com</p><p class="tm-muted">member</p><button type="submit" class="ducky-btn ducky-btn--primary">Save</button><p data-acct-profile-status hidden></p></form></section>';
await settle();assert(document.querySelectorAll('.uefn-name-control').length===1&&document.querySelectorAll('.uefn-avatar-preview').length===1,'Late profile rendering restores controls');
assert(document.querySelectorAll('[data-uefn-account-logout] .user-profile-logout').length===1,'Late profile rendering restores Log out in General');
const list=document.querySelector('.ud-remote__pcs');
list.innerHTML='<li class="ud-remote__pc">Refreshed PC</li>';await settle();
add=list.querySelector('.ud-remote__pc--add');assert(!!add&&!add.hidden&&getComputedStyle(add).display!=='none','PC refresh restores the plus card');
const before=window.testModalCalls.length;add.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
assert(window.testModalCalls.length===before+1,'Fallback plus card works with Enter');
assert(!document.querySelector('.b-user-profile .user-profile-logout'),'PC refresh does not put Log out back in the header');
const box=document.querySelector('#uefn-security-modal .ducky-modal');
assert(box.scrollWidth<=box.clientWidth+1,'Dialog has no horizontal overflow');
results.textContent+='ALL CHECKS PASSED';
}catch(e){results.textContent+='FAIL '+e.message;throw e;}
};
