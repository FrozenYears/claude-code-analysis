#!/usr/bin/env python3
"""
Build a self-contained HTML ebook from Claude Code analysis markdown files.
Output: ebook/index.html — open in any browser to read.
"""
import json
from pathlib import Path

BASE = Path(__file__).parent
OUT = BASE / "ebook"

CHAPTERS = [
    ("00-阅读路线", "📖 阅读路线图"),
    ("01-整体架构", "🏗️ 整体架构"),
    ("02-CLI入口与启动流程", "🚀 CLI入口与启动流程"),
    ("03-QueryEngine核心", "⚙️ QueryEngine核心"),
    ("04-Agent系统", "🤖 Agent系统"),
    ("05-Tool系统", "🔧 Tool系统"),
    ("06-Prompt系统", "📝 Prompt系统"),
    ("07-Context系统", "📦 Context系统"),
    ("08-Session与状态管理", "💾 Session与状态管理"),
    ("09-Streaming与API通信", "📡 Streaming与API通信"),
    ("10-多Agent协作", "🤝 多Agent协作"),
    ("11-命令系统", "⌨️ 命令系统"),
    ("12-UI组件系统", "🎨 UI组件系统"),
    ("13-插件与扩展", "🔌 插件与扩展"),
    ("14-安全与权限", "🔒 安全与权限"),
]

def load_chapters():
    chapters = []
    for folder, title in CHAPTERS:
        md_path = BASE / folder / "README.md"
        if md_path.exists():
            content = md_path.read_text(encoding="utf-8")
            chapters.append({"id": folder, "title": title, "content": content, "size": len(content)})
    return chapters

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Code 源码深度剖析</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@fontsource/cascadia-code@4.2.1/400.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@fontsource/cascadia-code@4.2.1/700.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.1/marked.min.js"></script>
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--tx:#e6edf3;--tx2:#8b949e;--tx3:#484f58;--ac:#58a6ff;--ac2:#1f6feb;--acg:rgba(88,166,255,.15);--bd:#30363d;--sw:320px;--cbg:#0d1117;--gn:#3fb950;--yl:#d29922;--rd:#f85149;--sh:rgba(210,153,34,.35)}
[data-theme="light"]{--bg:#fff;--bg2:#f6f8fa;--bg3:#eaeef2;--tx:#1f2328;--tx2:#656d76;--tx3:#8b949e;--ac:#0969da;--ac2:#0550ae;--acg:rgba(9,105,218,.08);--bd:#d0d7de;--cbg:#f6f8fa;--gn:#1a7f37;--yl:#9a6700;--rd:#cf222e;--sh:rgba(154,103,0,.2)}
*{margin:0;padding:0;box-sizing:border-box}
html{scroll-behavior:smooth}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans",Helvetica,Arial,sans-serif;background:var(--bg);color:var(--tx);line-height:1.8;display:flex;min-height:100vh;-webkit-font-smoothing:antialiased}
.sidebar{width:var(--sw);min-width:var(--sw);background:var(--bg2);border-right:1px solid var(--bd);height:100vh;position:fixed;top:0;left:0;overflow-y:auto;z-index:100;transition:transform .3s;display:flex;flex-direction:column}
.sidebar::-webkit-scrollbar{width:6px}.sidebar::-webkit-scrollbar-thumb{background:var(--bd);border-radius:3px}
.sh{padding:20px 20px 16px;border-bottom:1px solid var(--bd);position:sticky;top:0;background:var(--bg2);z-index:1}
.sh h1{font-size:17px;font-weight:700;color:var(--ac);letter-spacing:-.3px}
.sh .sub{font-size:12px;color:var(--tx3);margin-top:4px;display:flex;gap:12px}
.sb{padding:12px 16px;border-bottom:1px solid var(--bd);position:relative}
.sb .si{position:absolute;left:26px;top:50%;transform:translateY(-50%);color:var(--tx3);font-size:13px;pointer-events:none}
.sb input{width:100%;padding:8px 12px 8px 32px;background:var(--bg3);border:1px solid var(--bd);border-radius:8px;color:var(--tx);font-size:13px;outline:none;transition:border-color .2s,box-shadow .2s}
.sb input:focus{border-color:var(--ac);box-shadow:0 0 0 3px var(--acg)}
.sb input::placeholder{color:var(--tx3)}
.sb .sk{position:absolute;right:26px;top:50%;transform:translateY(-50%);font-size:11px;color:var(--tx3);background:var(--bg2);border:1px solid var(--bd);border-radius:4px;padding:1px 6px}
.src{padding:6px 16px;font-size:11px;color:var(--tx3);border-bottom:1px solid var(--bd);display:none}.src.v{display:block}
.nl{list-style:none;padding:8px 0;flex:1}
.ni{padding:10px 16px 10px 20px;cursor:pointer;font-size:13px;color:var(--tx2);border-left:3px solid transparent;transition:all .15s;display:flex;align-items:center;gap:10px;line-height:1.4}
.ni:hover{background:var(--bg3);color:var(--tx)}
.ni.a{background:var(--acg);color:var(--ac);border-left-color:var(--ac);font-weight:600}
.ni .nn{font-size:11px;color:var(--tx3);min-width:20px;font-weight:500}.ni.a .nn{color:var(--ac)}
.ni .nt{flex:1}.ni .ns{font-size:10px;color:var(--tx3);background:var(--bg3);padding:2px 6px;border-radius:4px;font-weight:500}
.ni.sm{background:var(--sh)}.ni.sh{display:none}
.ni mark{background:var(--yl);color:var(--bg);padding:0 2px;border-radius:2px;font-weight:600}
.rs{padding:12px 16px;border-top:1px solid var(--bd);font-size:11px;color:var(--tx3);display:flex;gap:16px;background:var(--bg2)}
.main{margin-left:var(--sw);flex:1;min-width:0}
.tb{position:sticky;top:0;border-bottom:1px solid var(--bd);padding:10px 32px;display:flex;align-items:center;gap:12px;z-index:50;backdrop-filter:blur(12px);background:color-mix(in srgb,var(--bg) 85%,transparent)}
.tb .bc{font-size:13px;color:var(--tx2);display:flex;align-items:center;gap:6px}
.tb .bc .sp{color:var(--tx3)}.tb .bc .cur{color:var(--tx);font-weight:600}
.ta{margin-left:auto;display:flex;gap:6px;align-items:center}
.btn{padding:6px 14px;background:var(--bg3);border:1px solid var(--bd);border-radius:8px;color:var(--tx2);cursor:pointer;font-size:13px;transition:all .15s;display:flex;align-items:center;gap:4px}
.btn:hover{background:var(--bd);color:var(--tx)}.btn:active{transform:scale(.97)}
.bn{font-size:12px;padding:5px 10px}.bn.d{opacity:.3;pointer-events:none}
.cw{max-width:1020px;margin:0 auto;padding:40px 56px 120px}
.c{font-size:15px;line-height:1.85;color:var(--tx)}
.c h1{font-size:2.2em;font-weight:800;margin:0 0 20px;padding-bottom:14px;border-bottom:2px solid var(--ac);letter-spacing:-.5px;line-height:1.3}
.c h2{font-size:1.55em;font-weight:700;margin:48px 0 18px;padding-bottom:10px;border-bottom:1px solid var(--bd);letter-spacing:-.3px;position:relative}
.c h2::before{content:'';position:absolute;bottom:-1px;left:0;width:60px;height:2px;background:var(--ac);border-radius:1px}
.c h3{font-size:1.25em;font-weight:600;margin:36px 0 14px;padding-left:14px;border-left:3px solid var(--ac)}
.c h4{font-weight:600;margin:28px 0 10px;color:var(--tx2);text-transform:uppercase;letter-spacing:.3px;font-size:.9em}
.c p{margin:0 0 18px}.c strong{font-weight:600}.c em{color:var(--tx2)}
.c a{color:var(--ac);text-decoration:none;border-bottom:1px solid transparent;transition:border-color .15s}.c a:hover{border-bottom-color:var(--ac)}
.c ul,.c ol{margin:0 0 18px;padding-left:28px}.c li{margin:6px 0;line-height:1.75}.c li::marker{color:var(--tx3)}
.c code{background:var(--bg3);padding:2px 7px;border-radius:5px;font-size:.88em;font-family:"Cascadia Mono","Fira Code","JetBrains Mono","SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;color:var(--ac);border:1px solid var(--bd)}
.c pre{background:var(--cbg);border:1px solid var(--bd);border-radius:10px;padding:18px 20px;overflow-x:auto;margin:0 0 20px;line-height:1.65}
.c pre code{background:none;padding:0;font-size:13px;color:var(--tx);border:none;font-family:"Cascadia Mono","Fira Code","JetBrains Mono","SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace}
.c blockquote{border-left:4px solid var(--ac);padding:12px 20px;margin:0 0 20px;background:var(--acg);border-radius:0 10px 10px 0;color:var(--tx2);font-size:.95em}
.c blockquote p:last-child{margin-bottom:0}
.c table{width:100%;border-collapse:collapse;margin:0 0 20px;font-size:14px;border-radius:10px;overflow:hidden;border:1px solid var(--bd)}
.c th,.c td{border:1px solid var(--bd);padding:10px 14px;text-align:left}
.c th{background:var(--bg2);font-weight:600;font-size:13px;text-transform:uppercase;letter-spacing:.3px;color:var(--tx2)}
.c tr:nth-child(even){background:color-mix(in srgb,var(--bg2) 50%,transparent)}.c tr:hover{background:var(--acg)}
.c hr{border:none;border-top:1px solid var(--bd);margin:40px 0}
.c img{max-width:100%;border-radius:10px;border:1px solid var(--bd)}
/* Visual emphasis */
.c p>strong:first-child:not(:only-child){display:inline;background:var(--acg);padding:2px 8px;border-radius:4px;border-left:3px solid var(--ac);margin-right:4px}
.c li>strong:first-child{color:var(--ac);font-weight:600}
.c h2+.c p,.c h3+.c p{font-size:1.02em;color:var(--tx)}
.c p:has(code):not(:has(pre code)){line-height:2}
.c ul li,.c ol li{position:relative;padding-left:4px}
.c ul li::marker{color:var(--ac)}
.c blockquote strong{color:var(--ac)}
.c code+a{border-bottom:none}
.c table code{font-size:.82em;padding:1px 5px}
.cbw{position:relative}.cpb{position:absolute;top:10px;right:10px;padding:4px 10px;background:var(--bg3);border:1px solid var(--bd);border-radius:6px;color:var(--tx2);cursor:pointer;font-size:11px;opacity:0;transition:all .15s;z-index:5}
.cbw:hover .cpb{opacity:1}.cpb:hover{background:var(--ac2);color:#fff}
.toc{position:fixed;right:24px;top:80px;width:200px;max-height:calc(100vh - 120px);overflow-y:auto;font-size:12px;display:none}
@media(min-width:1400px){.toc{display:block}}
.toc::-webkit-scrollbar{width:4px}.toc::-webkit-scrollbar-thumb{background:var(--bd);border-radius:2px}
.tt{font-weight:700;color:var(--tx3);margin-bottom:10px;text-transform:uppercase;letter-spacing:.8px;font-size:10px}
.tl{list-style:none;border-left:2px solid var(--bd);padding-left:14px}.tl li{margin:3px 0}
.tl a{color:var(--tx3);text-decoration:none;transition:color .15s;display:block;padding:2px 0;line-height:1.4}.tl a:hover{color:var(--ac)}
.tl .h3{padding-left:10px;font-size:11px}.tl .h4{padding-left:20px;font-size:11px}
.pb{position:fixed;top:0;left:0;height:3px;background:linear-gradient(90deg,var(--ac),var(--gn));z-index:200;transition:width .1s;border-radius:0 2px 2px 0}
.st{position:fixed;bottom:28px;right:28px;width:44px;height:44px;background:var(--ac2);border:none;border-radius:50%;color:#fff;cursor:pointer;font-size:18px;display:none;align-items:center;justify-content:center;z-index:100;box-shadow:0 4px 12px rgba(0,0,0,.3);transition:all .2s}
.st.v{display:flex}.st:hover{transform:scale(1.1)}
@media(max-width:768px){.sidebar{transform:translateX(-100%)}.sidebar.o{transform:translateX(0);box-shadow:4px 0 20px rgba(0,0,0,.5)}.main{margin-left:0}.cw{padding:20px 16px 80px}.toc{display:none}.mb{display:block}.tb{padding:10px 16px}}
.mb{display:none;padding:6px 10px;background:var(--bg3);border:1px solid var(--bd);border-radius:8px;color:var(--tx);cursor:pointer;font-size:16px}
.ov{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:99}.ov.v{display:block}
</style>
</head>
<body>
<div class="pb" id="pb"></div>
<div class="ov" id="ov"></div>
<aside class="sidebar" id="sb">
  <div class="sh"><h1>🦞 Claude Code 源码剖析</h1><div class="sub"><span>📚 15章</span><span>💾 935KB</span><span>📝 691代码块</span></div></div>
  <div class="sb"><span class="si">🔍</span><input type="text" id="si" placeholder="搜索章节标题、内容..." /><span class="sk">/</span></div>
  <div class="src" id="src"></div>
  <ul class="nl" id="nl"></ul>
  <div class="rs"><span id="sc">📖 0/15 章</span><span id="sp">⏱️ 0%</span></div>
</aside>
<main class="main">
  <div class="tb">
    <button class="mb" id="mb">☰</button>
    <div class="bc"><span>Claude Code 源码剖析</span><span class="sp">/</span><span class="cur" id="ct">选择章节</span></div>
    <div class="ta"><button class="btn bn" id="pv">← 上一章</button><button class="btn bn" id="nx">下一章 →</button><button class="btn" id="tt">🌙</button></div>
  </div>
  <div class="cw"><div class="c" id="co">
    <h1>🦞 Claude Code 源码深度剖析</h1>
    <p>欢迎阅读 Claude Code 源码深度剖析电子书。点击左侧目录开始学习。</p>
    <blockquote><p><strong>项目规模</strong>：15 章 · 935KB · 691 个代码块 · 505 处源码引用</p><p>从 CLI 入口到安全机制，逐行剖析 51.5 万行 TypeScript 源码</p></blockquote>
    <h2>快速开始</h2>
    <ul><li><strong>新手</strong>：从 <code>00-阅读路线</code> 开始，按推荐顺序学习</li><li><strong>有经验</strong>：直接跳到感兴趣的章节，每章独立可读</li><li><strong>面试突击</strong>：重点看 03-QueryEngine、04-Agent系统、10-多Agent协作</li></ul>
    <h2>快捷键</h2>
    <table><tr><th>按键</th><th>功能</th></tr><tr><td><code>←</code> / <code>→</code></td><td>上一章 / 下一章</td></tr><tr><td><code>/</code></td><td>聚焦搜索框</td></tr><tr><td><code>Esc</code></td><td>清空搜索</td></tr></table>
  </div></div>
</main>
<nav class="toc" id="toc"><div class="tt">本章目录</div><ul class="tl" id="tl"></ul></nav>
<button class="st" id="st">↑</button>
<script>
CHAPTERS=__CHAPTERS_JSON__;
const nl=document.getElementById('nl'),co=document.getElementById('co'),ct=document.getElementById('ct'),si=document.getElementById('si'),src=document.getElementById('src'),tl=document.getElementById('tl'),pb=document.getElementById('pb'),st=document.getElementById('st'),sc=document.getElementById('sc'),sp=document.getElementById('sp'),sb=document.getElementById('sb'),ov=document.getElementById('ov');
let ci=-1,vc=new Set(JSON.parse(localStorage.getItem('ce-v')||'[]'));
function us(){sc.textContent='📖 '+vc.size+'/'+CHAPTERS.length+' 章';sp.textContent='⏱️ '+Math.round(vc.size/CHAPTERS.length*100)+'%'}
function rn(f=''){nl.innerHTML='';const lf=f.toLowerCase();let mc=0;CHAPTERS.forEach((ch,i)=>{const li=document.createElement('li');li.className='ni';if(i===ci)li.classList.add('a');const nm=i===0?'📖':String(i).padStart(2,'0');const kb=Math.round(ch.size/1024);let th=false,cm=false,thl=ch.title;if(lf){const tl2=ch.title.toLowerCase(),il=ch.id.toLowerCase();if(tl2.includes(lf)||il.includes(lf)){th=true;const re=new RegExp('('+lf.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','gi');thl=ch.title.replace(re,'<mark>$1</mark>')}if(ch.content&&ch.content.toLowerCase().includes(lf))cm=true;if(!th&&!cm){li.classList.add('sh');return}li.classList.add('sm');mc++}const v=vc.has(ch.id);li.innerHTML='<span class="nn">'+nm+'</span><span class="nt">'+thl+(cm&&!th?' <span style="font-size:10px;color:var(--yl)">⚡内容匹配</span>':'')+'</span><span class="ns">'+kb+'KB'+(v?' ✓':'')+'</span>';li.onclick=()=>lc(i);nl.appendChild(li)});if(lf){src.textContent='找到 '+mc+' 个结果';src.classList.add('v')}else src.classList.remove('v')}
function lc(idx){ci=idx;const ch=CHAPTERS[idx];ct.textContent=ch.title;vc.add(ch.id);localStorage.setItem('ce-v',JSON.stringify([...vc]));us();co.innerHTML=marked.parse(ch.content);co.querySelectorAll('pre code').forEach(b=>{const w=document.createElement('div');w.className='cbw';b.parentNode.parentNode.insertBefore(w,b.parentNode);w.appendChild(b.parentNode);const btn=document.createElement('button');btn.className='cpb';btn.textContent='📋 复制';btn.onclick=()=>{navigator.clipboard.writeText(b.textContent).then(()=>{btn.textContent='✅ 已复制';setTimeout(()=>btn.textContent='📋 复制',2000)})};w.appendChild(btn);hljs.highlightElement(b)});bt();rn(si.value);document.getElementById('pv').classList.toggle('d',idx<=0);document.getElementById('nx').classList.toggle('d',idx>=CHAPTERS.length-1);window.scrollTo(0,0);sb.classList.remove('o');ov.classList.remove('v');localStorage.setItem('ce-p',idx)}
function bt(){tl.innerHTML='';co.querySelectorAll('h2,h3,h4').forEach((h,i)=>{const id='h-'+i;h.id=id;const li=document.createElement('li');li.className='h'+h.tagName.toLowerCase();const a=document.createElement('a');a.href='#'+id;a.textContent=h.textContent.replace(/^#\s*/,'').substring(0,45);a.onclick=e=>{e.preventDefault();h.scrollIntoView({behavior:'smooth',block:'start'})};li.appendChild(a);tl.appendChild(li)})}
let st2;si.addEventListener('input',e=>{clearTimeout(st2);st2=setTimeout(()=>rn(e.target.value),150)});si.addEventListener('keydown',e=>{if(e.key==='Escape'){si.value='';rn();si.blur()}});
const tt=document.getElementById('tt'),stm=localStorage.getItem('ce-t')||'dark';if(stm==='light'){document.documentElement.setAttribute('data-theme','light');tt.textContent='☀️'}
tt.onclick=()=>{const il=document.documentElement.getAttribute('data-theme')==='light';document.documentElement.setAttribute('data-theme',il?'dark':'light');tt.textContent=il?'🌙':'☀️';localStorage.setItem('ce-t',il?'dark':'light')};
document.getElementById('pv').onclick=()=>{if(ci>0)lc(ci-1)};document.getElementById('nx').onclick=()=>{if(ci<CHAPTERS.length-1)lc(ci+1)};
document.getElementById('mb').onclick=()=>{sb.classList.toggle('o');ov.classList.toggle('v')};ov.onclick=()=>{sb.classList.remove('o');ov.classList.remove('v')};
window.addEventListener('scroll',()=>{const s=window.scrollY,t=document.documentElement.scrollHeight-window.innerHeight;p();pb.style.width=(t>0?s/t*100:0)+'%';st.classList.toggle('v',s>500)});
st.onclick=()=>window.scrollTo({top:0,behavior:'smooth'});
document.addEventListener('keydown',e=>{if(document.activeElement===si)return;if(e.key==='ArrowLeft'&&ci>0)lc(ci-1);if(e.key==='ArrowRight'&&ci<CHAPTERS.length-1)lc(ci+1);if(e.key==='/'){e.preventDefault();si.focus()}});
rn();us();const sp2=parseInt(localStorage.getItem('ce-p'));if(!isNaN(sp2)&&sp2>=0&&sp2<CHAPTERS.length)lc(sp2);
</script>
</body>
</html>"""

def build():
    OUT.mkdir(exist_ok=True)
    chapters = load_chapters()
    html = HTML.replace("__CHAPTERS_JSON__", json.dumps(chapters, ensure_ascii=False))
    out = OUT / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"Built: {out} ({len(html)} bytes, {len(chapters)} chapters)")

if __name__ == "__main__":
    build()
