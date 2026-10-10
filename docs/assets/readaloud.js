/* Read Aloud: word-by-word highlight, "Read to the end", pause, stop; double-tap a block to start there. Call ReadAloud.init() after content is rendered. */
window.ReadAloud={init:function(){
var S=window.speechSynthesis,bar=document.getElementById('ra-bar');if(!S){if(bar)bar.style.display='none';return;}
var blocks=[].slice.call(document.querySelectorAll('main h1,main h2,main h3,main p,main li,main td,main th'));
blocks.forEach(function(b){var w=document.createTreeWalker(b,NodeFilter.SHOW_TEXT,null),n,l=[];while(n=w.nextNode())l.push(n);
 l.forEach(function(t){var f=document.createDocumentFragment();t.nodeValue.split(/(\s+)/).forEach(function(p){if(!p)return;if(/^\s+$/.test(p))f.appendChild(document.createTextNode(p));else{var s=document.createElement('span');s.className='w';s.textContent=p;f.appendChild(s);}});t.parentNode.replaceChild(f,t);});});
var i=0,cur=null,stopped=true;function clear(){if(cur)cur.classList.remove('on');cur=null;}
function speak(){if(stopped||i>=blocks.length){clear();stopped=true;return;}
 var ws=[].slice.call(blocks[i].querySelectorAll('.w'));if(!ws.length){i++;return speak();}
 var txt='',o=[];ws.forEach(function(s){o.push(txt.length);txt+=s.textContent+' ';});
 var u=new SpeechSynthesisUtterance(txt);
 u.onboundary=function(e){if(e.name&&e.name!=='word')return;var k=0;while(k+1<o.length&&o[k+1]<=e.charIndex)k++;clear();cur=ws[k];cur.classList.add('on');cur.scrollIntoView({block:'center',behavior:'smooth'});};
 u.onend=function(){clear();i++;speak();};S.speak(u);}
document.getElementById('ra-all').onclick=function(){S.cancel();if(S.paused)S.resume();stopped=false;i=0;speak();};
document.getElementById('ra-pause').onclick=function(){if(S.paused){S.resume();this.textContent='❚❚ Pause';}else{S.pause();this.textContent='▶ Resume';}};
document.getElementById('ra-stop').onclick=function(){stopped=true;S.cancel();clear();};
blocks.forEach(function(b,ix){b.addEventListener('dblclick',function(){S.cancel();stopped=false;i=ix;speak();});});
}};
