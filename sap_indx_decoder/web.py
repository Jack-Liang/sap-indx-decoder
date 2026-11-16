"""本地 Web 预览(`sap-indx-decoder serve`)。

两种模式:
  连库        --host/--port/--user/--password(默认),浏览任意簇表
  --offline-dir DIR  浏览本地 dump 的 .bin 文件(自动加载 .ddic.json 侧车)

零框架依赖:stdlib http.server + 单页内联 HTML/CSS/JS。
设计令牌采用语义化 CSS 变量,自动跟随系统明暗主题;覆盖率用 SVG 圆环
展示;组件卡片带渐显动画。默认只绑定 127.0.0.1。
"""
import json
import os
import time
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import ToolError, __version__, parse_blob
from .db import (connect, current_schema, fetch_cluster, iter_cluster_ids, qid,
                 table_keyinfo)
from .ddic import DDIC, enrich_components
from .marker import component_to_json

_PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>sap-indx-decoder</title>
<style>
/* ---- 设计令牌(语义层,自动明暗) ---- */
:root{
  --bg:#f2f4f7; --panel:#ffffff; --panel-2:#f7f9fb; --border:#e2e6eb;
  --text:#1a2129; --text-2:#55616d; --muted:#8b95a1;
  --accent:#0a6ed1; --accent-2:#3aa0f0; --accent-soft:#e9f1fb;
  --green:#0f7b3d; --green-soft:#e4f3ea;
  --amber:#a35200; --amber-soft:#fcf0e2;
  --red:#c6262e; --red-soft:#fbebeb;
  --chip:#eef1f5; --chip-text:#45525f;
  --panel-glass:rgba(255,255,255,.78);
  --shadow:0 1px 2px rgba(16,24,40,.06),0 4px 14px rgba(16,24,40,.06);
  --radius:12px;
  --mono:ui-monospace,'SF Mono',SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#0d1218; --panel:#151d26; --panel-2:#1a232d; --border:#2a3540;
    --text:#e7ecf2; --text-2:#a7b2bd; --muted:#6e7b89;
    --accent:#54a6f2; --accent-2:#7db8f5; --accent-soft:#122a40;
    --green:#5cc98c; --green-soft:#123021;
    --amber:#efa24d; --amber-soft:#322411;
    --red:#f2777a; --red-soft:#3a1a1c;
    --chip:#212b35; --chip-text:#a7b2bd;
    --panel-glass:rgba(21,29,38,.72);
    --shadow:0 1px 2px rgba(0,0,0,.4),0 6px 18px rgba(0,0,0,.28);
  }
}
/* ---- 基础 ---- */
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:
  radial-gradient(900px 500px at 85% -10%, var(--accent-soft), transparent 60%),
  var(--bg);
  color:var(--text);
  font:14px/1.5 -apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif;
  display:flex;flex-direction:column}
header{display:flex;align-items:center;gap:10px;padding:10px 16px;
  background:var(--panel-glass);backdrop-filter:blur(12px) saturate(1.15);
  border-bottom:1px solid var(--border)}
.logo{width:28px;height:28px;border-radius:8px;display:inline-flex;align-items:center;
  justify-content:center;background:linear-gradient(135deg,var(--accent),var(--accent-2));
  color:#fff;box-shadow:0 2px 8px color-mix(in srgb,var(--accent) 45%,transparent)}
.logo svg{width:15px;height:15px}
.brand{font-weight:650;font-size:15px;letter-spacing:.2px}
.brand .ver{color:var(--muted);font-weight:500;font-size:12px;margin-left:2px}
#mode{margin-left:auto}
.chip{display:inline-flex;align-items:center;gap:5px;background:var(--chip);
  color:var(--chip-text);border:1px solid var(--border);border-radius:999px;
  padding:3px 11px;font-size:12px}
.chip b{color:var(--text);font-weight:600}
.gh{margin-left:8px;display:inline-flex;align-items:center;color:var(--muted);
  transition:color .15s}
.gh:hover{color:var(--text)}
.gh svg{width:17px;height:17px}
/* ---- 详情头部:分段切换 + 按钮 ---- */
.detail-head .head-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.detail-head h2{margin:0;font-size:16px;font-family:var(--mono);
  word-break:break-all;display:flex;align-items:center;gap:10px;flex:1;min-width:200px}
.seg{display:inline-flex;background:var(--chip);border:1px solid var(--border);
  border-radius:9px;padding:2px;gap:2px;flex:none}
.seg button{border:none;background:transparent;color:var(--text-2);
  font:600 12px/1 -apple-system,'Segoe UI',Roboto,sans-serif;
  padding:5px 14px;border-radius:7px;cursor:pointer;transition:all .12s}
.seg button.on{background:var(--accent);color:#fff}
.btn{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--border);
  background:var(--panel);color:var(--text-2);border-radius:9px;padding:6px 12px;
  font-size:12.5px;font-weight:550;cursor:pointer;transition:all .12s;flex:none}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn.primary:hover{filter:brightness(1.1);color:#fff}
.keys{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.keys .chip{font-family:var(--mono);font-size:11.5px;padding:2px 9px}
.badge.unt{background:var(--amber-soft);color:var(--amber)}
/* ---- RELID 过滤 chips ---- */
.relids{display:flex;gap:6px;flex-wrap:wrap;padding:7px 12px 3px}
.relids .chip{cursor:pointer;user-select:none;transition:all .12s}
.relids .chip.on{background:var(--accent);border-color:var(--accent);color:#fff}
.relids .chip.on b{color:#fff}
/* ---- Hex 视图 ---- */
.legend{display:flex;gap:16px;align-items:center;padding:7px 14px 0;
  font-size:11.5px;color:var(--text-2)}
.legend .dot{width:9px;height:9px;border-radius:2px;display:inline-block;
  margin-right:5px;vertical-align:-1px}
.legend .dot.hd{background:var(--accent)}
.legend .dot.dt{background:var(--green)}
.hexdump{background:var(--panel-2);margin:8px 12px 10px;padding:10px 14px;
  border-radius:8px;overflow:auto;font:12px/1.75 var(--mono);white-space:pre;
  color:var(--text-2)}
.hrow .off{color:var(--muted);margin-right:10px}
.hrow .b{padding:0 1px;border-radius:3px}
.hrow .b-hd{color:var(--accent);background:var(--accent-soft);
  box-shadow:0 0 0 1px var(--accent-soft)}
.hrow .asc{color:var(--text);margin-left:10px}
.hexmore{padding:0 14px 10px;font-size:11.5px;color:var(--muted)}
.badge{display:inline-flex;align-items:center;border-radius:999px;padding:2px 10px;
  font-size:12px;font-weight:550}
.badge.kind{background:var(--accent-soft);color:var(--accent)}
.badge.ddic{background:var(--green-soft);color:var(--green)}
.badge.rows{background:var(--chip);color:var(--chip-text)}
/* ---- 三栏 ---- */
#wrap{flex:1;display:flex;min-height:0}
.pane{display:flex;flex-direction:column;min-height:0;overflow:hidden}
#left{width:240px;border-right:1px solid var(--border);background:var(--panel)}
#mid{width:360px;border-right:1px solid var(--border);background:var(--panel)}
#main{flex:1;overflow:auto;padding:14px 18px}
.pane-head{padding:10px 12px 8px;position:sticky;top:0;z-index:2;
  background:var(--panel-glass);backdrop-filter:blur(10px);
  border-bottom:1px solid var(--border)}
.pane-title{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px}
.pane-title h3{margin:0;font-size:12px;font-weight:700;letter-spacing:.8px;
  text-transform:uppercase;color:var(--text-2)}
.pane-title .cnt{color:var(--muted);font-size:12px;font-family:var(--mono)}
.pane-body{flex:1;overflow:auto;padding:6px}
input{width:100%;padding:7px 11px 7px 30px;border:1px solid var(--border);border-radius:8px;
  background:var(--panel) url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none' stroke='%238b95a1' stroke-width='1.5' stroke-linecap='round'><circle cx='7' cy='7' r='4.5'/><path d='M10.8 10.8L14 14'/></svg>") 9px center no-repeat;
  background-size:14px 14px;color:var(--text);font-size:13px}
input::placeholder{color:var(--muted)}
input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
/* ---- 列表项 ---- */
.item{display:flex;justify-content:space-between;align-items:baseline;gap:8px;
  padding:6px 10px;border-radius:8px;cursor:pointer;user-select:none;
  transition:background .12s}
.item:hover{background:var(--accent-soft)}
.item.sel{background:var(--accent-soft);box-shadow:inset 3px 0 0 var(--accent)}
.item .label{font-family:var(--mono);font-size:12.5px;word-break:break-all}
.item .meta{color:var(--muted);font-size:11px;white-space:nowrap;font-family:var(--mono)}
.empty{color:var(--muted);text-align:center;padding:30px 12px;font-size:13px}
.empty svg{width:56px;height:56px;opacity:.45;margin-bottom:8px}
/* ---- 详情 ---- */
.detail-head{position:sticky;top:0;z-index:3;margin:-14px -18px 4px;
  padding:12px 18px 10px;background:var(--panel-glass);backdrop-filter:blur(12px);
  border-bottom:1px solid var(--border)}
.detail-head h2{margin:0 0 8px;font-size:16px;font-family:var(--mono);
  word-break:break-all;display:flex;align-items:center;gap:10px}
.chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.chip{padding:3px 11px}
.ring{position:relative;width:42px;height:42px;flex:none}
.ring svg{width:42px;height:42px;transform:rotate(-90deg)}
.ring .ring-bg{fill:none;stroke:var(--chip);stroke-width:5}
.ring .ring-fg{fill:none;stroke-width:5;stroke-linecap:round;
  transition:stroke-dashoffset .4s ease}
.ring.ok .ring-fg{stroke:var(--green)}
.ring.warn .ring-fg{stroke:var(--amber)}
.ring.bad .ring-fg{stroke:var(--red)}
.ring-num{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  font-size:10.5px;font-weight:700;font-family:var(--mono)}
.card{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);
  margin:12px 0;box-shadow:var(--shadow);overflow:hidden;
  animation:rise .28s ease both}
@keyframes rise{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}
.card-h{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:9px 14px;
  background:var(--panel-2);border-bottom:1px solid var(--border)}
.card-h .icon{width:15px;height:15px;color:var(--accent);flex:none}
.card-h .name{font-family:var(--mono);font-weight:600;font-size:13.5px;word-break:break-all}
.card-b{padding:4px 0}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th{background:var(--panel-2);text-align:left;font-weight:600;position:sticky;top:0;
  color:var(--text-2)}
td,th{border-bottom:1px solid var(--border);padding:6px 12px}
tbody tr:hover td{background:var(--accent-soft)}
pre{background:var(--panel-2);border-radius:8px;margin:8px 12px;padding:10px 12px;
  overflow:auto;font:12px/1.55 var(--mono);white-space:pre;color:var(--text)}
.muted{color:var(--muted)}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:6px}
::-webkit-scrollbar-thumb:hover{background:var(--muted)}
::-webkit-scrollbar-track{background:transparent}
</style></head><body>
<header>
  <span class="logo"><svg viewBox="0 0 16 16" fill="none"><path d="M8 1l6 3.5v7L8 15 2 11.5v-7L8 1z" fill="currentColor" opacity=".92"/><path d="M8 1v14M2 4.5l12 7M14 4.5l-12 7" stroke="rgba(255,255,255,.55)" stroke-width="1"/></svg></span>
  <span class="brand">sap-indx-decoder <span class="ver">__VERSION__</span></span>
  <span id="mode" class="chip">…</span>
  <a class="gh" href="https://github.com/Jack-Liang/sap-indx-decoder" target="_blank" rel="noopener" title="GitHub 仓库"><svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg></a>
</header>
<div id="wrap">
  <div class="pane" id="left">
    <div class="pane-head">
      <div class="pane-title"><h3>簇表</h3><span class="cnt" id="tcount"></span></div>
      <input id="tq" placeholder="过滤簇表…">
    </div>
    <div class="pane-body" id="tables"></div>
  </div>
  <div class="pane" id="mid">
    <div class="pane-head">
      <div class="pane-title"><h3>簇</h3><span class="cnt" id="ccount"></span></div>
      <input id="cq" placeholder="搜索簇(RELID/键名)…">
      <div class="relids" id="relids"></div>
    </div>
    <div class="pane-body" id="clusters"></div>
  </div>
  <div id="main">
    <div id="detail" class="empty">
      <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="1.6">
        <ellipse cx="24" cy="11" rx="14" ry="5"/>
        <path d="M10 11v26c0 2.8 6.3 5 14 5s14-2.2 14-5V11"/>
        <path d="M10 24c0 2.8 6.3 5 14 5s14-2.2 14-5"/>
      </svg>
      <div>← 选择一个簇查看内容</div>
    </div>
  </div>
</div>
<script>
let MODE=null, TABLES=[], CUR_TABLE=null, CLUSTERS=[], CUR_KV=null, CUR_FILE=null;
let RELIDS=[], CUR_RELID=null, VIEW='table', CUR_DETAIL=null;
let clusterReqId=0;   // 防竞态: 只渲染最后一次簇列表请求
const $=id=>document.getElementById(id);
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const fmtN=n=>n==null?'':n>=1000?n.toLocaleString('en-US'):n;
const fmtB=n=>n>=1024*1024?(n/1024/1024).toFixed(1)+'M':n>=1024?(n/1024).toFixed(1)+'K':n+'B';
const kindText=k=>({value:'单值',struct:'结构',table:'表'}[k]||k);
const kindIcon=k=>({
  table:'<svg class="icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="2" y="2.5" width="12" height="11" rx="1.5"/><path d="M2 6.5h12M6.5 6.5v7"/></svg>',
  struct:'<svg class="icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="2" y="2.5" width="5" height="5" rx="1"/><rect x="9" y="2.5" width="5" height="5" rx="1"/><rect x="2" y="8.5" width="5" height="5" rx="1"/><rect x="9" y="8.5" width="5" height="5" rx="1"/></svg>',
  value:'<svg class="icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="8" cy="8" r="5.5"/><path d="M8 5v3l2 1.6"/></svg>'
}[k]||'');
function ringSvg(v){
  const C=113.1, off=(C*(1-Math.min(Math.max(v,0),100)/100)).toFixed(1);
  const cls=v>=95?'ok':v>=80?'warn':'bad';
  return `<span class="ring ${cls}"><svg viewBox="0 0 44 44">
    <circle class="ring-bg" cx="22" cy="22" r="18"/>
    <circle class="ring-fg" cx="22" cy="22" r="18" stroke-dasharray="${C}" stroke-dashoffset="${off}"/>
  </svg><span class="ring-num">${v}%</span></span>`;
}
async function jget(url){const r=await fetch(url);const t=await r.text();
  try{return JSON.parse(t)}catch(e){throw new Error(t)}}
async function init(){
  const d=await jget('/api/tables'); MODE=d.mode; TABLES=d.tables;
  $('mode').innerHTML = MODE==='offline' ? '<b>离线模式</b>'
    : '<span style="color:var(--green)">●</span> <b>连库模式</b> '+esc((TABLES[0]||{}).schema||'');
  renderTables();
}
function renderTables(){
  const q=$('tq').value.toLowerCase();
  const src=TABLES.filter(t=>t.name.toLowerCase().includes(q));
  $('tcount').textContent=src.length+' 张';
  $('tables').innerHTML=src.length
    ? src.map(t=>{
        const meta = t.rows!=null ? '<span class="meta">'+fmtN(t.rows)+'</span>'
                   : t.bytes!=null ? '<span class="meta">'+fmtB(t.bytes)+'</span>' : '';
        return `<div class="item" data-t="${esc(t.name)}"><span class="label">${esc(t.name)}</span>${meta}</div>`;
      }).join('')
    : '<div class="empty">无匹配</div>';
  [...$('tables').children].forEach(el=>el.onclick=()=>pickTable(el.dataset.t));
}
async function pickTable(t){
  CUR_TABLE=t; CUR_FILE=null; CUR_RELID=null;
  document.querySelectorAll('#tables .item').forEach(e=>e.classList.toggle('sel',e.dataset.t===t));
  $('ccount').textContent='…';
  $('clusters').innerHTML='<div class="empty">加载中…</div>';
  await loadRelids();
  await loadClusters();
}
async function loadRelids(){
  RELIDS=[]; CUR_RELID=null;
  $('relids').innerHTML='';
  if(MODE!=='db')return;
  try{
    const d=await jget('/api/relids?table='+encodeURIComponent(CUR_TABLE));
    RELIDS=d.relids||[];
  }catch(e){return;}
  $('relids').innerHTML=RELIDS.length<2 ? '' :
    '<span class="chip on" data-r="">全部 <b>'+RELIDS.reduce((a,r)=>a+r.n,0)+'</b></span>'
    + RELIDS.map(r=>`<span class="chip" data-r="${esc(r.relid)}">${esc(r.relid)} <b>${fmtN(r.n)}</b></span>`).join('');
  [...$('relids').children].forEach(el=>el.onclick=()=>{
    CUR_RELID=el.dataset.r||null;
    [...$('relids').children].forEach(e=>e.classList.toggle('on',e===el));
    loadClusters();
  });
}
async function loadClusters(){
  const reqId=++clusterReqId;
  let qs='table='+encodeURIComponent(CUR_TABLE);
  if(MODE==='db'){
    if($('cq').value) qs+='&q='+encodeURIComponent($('cq').value);
    if(CUR_RELID) qs+='&relid='+encodeURIComponent(CUR_RELID);
  }
  const d=await jget('/api/clusters?'+qs);
  if(reqId!==clusterReqId)return;   // 已有更新的请求,丢弃本次结果
  CLUSTERS=d.items;
  renderClusters();
}
function renderClusters(){
  const q=$('cq').value.toLowerCase();
  const src=(MODE==='db')?CLUSTERS:CLUSTERS.filter(c=>c.label.toLowerCase().includes(q));
  $('ccount').textContent=src.length+' 个';
  $('clusters').innerHTML=src.length
    ? src.map((c,i)=>`<div class="item" data-i="${i}"><span class="label">${esc(c.label)}</span><span class="meta">${c.chunks}块 ${fmtB(c.bytes)}</span></div>`).join('')
    : '<div class="empty">无匹配</div>';
  [...$('clusters').children].forEach(el=>el.onclick=()=>pickCluster(+el.dataset.i));
}
async function pickCluster(i){
  const c=CLUSTERS[i];
  document.querySelectorAll('#clusters .item').forEach((e,j)=>e.classList.toggle('sel',j===i));
  $('detail').innerHTML='<div class="empty">解析中…</div>';
  let qs;
  if(MODE==='offline'){CUR_FILE=c.file; qs='file='+encodeURIComponent(c.file);}
  else{CUR_KV=c.kv; qs=Object.entries(c.kv).map(([k,v])=>k+'='+encodeURIComponent(v)).join('&');
       qs='table='+encodeURIComponent(CUR_TABLE)+'&'+qs;}
  try{
    const d=await jget('/api/cluster?'+qs);
    CUR_DETAIL=d; VIEW='table';
    renderDetail();
  }catch(e){$('detail').innerHTML='<div class="empty">'+esc(e.message)+'</div>';}
}
function hexCard(d){
  const raw=d.raw_hex, total=d.raw_len, ROW=16, MAXROWS=300;
  const nRows=Math.ceil(total/ROW), rows=Math.min(nRows,MAXROWS);
  let h='';
  for(let r=0;r<rows;r++){
    let cells='',asc='';
    for(let j=0;j<ROW;j++){
      const idx=r*ROW+j;
      if(idx>=total){cells+='   ';continue;}
      const b=parseInt(raw.substr(idx*2,2),16);
      cells+=`<span class="${idx<24?'b-hd':'b'}">${b.toString(16).padStart(2,'0')}</span> `;
      asc += b>=32&&b<127 ? String.fromCharCode(b) : '·';
    }
    h+=`<div class="hrow"><span class="off">${(r*ROW).toString(16).padStart(8,'0')}</span>${cells}<span class="asc">${esc(asc)}</span></div>`;
  }
  const legend=`<div class="legend"><span><span class="dot hd"></span>容器头 24 B</span>`
    +(d.container.compressed?`<span><span class="dot dt"></span>${esc(d.container.algorithm||'LZH')} 压缩数据</span>`:'')+`</div>`;
  const more=nRows>MAXROWS?`<div class="hexmore">… 共 ${fmtN(total)} 字节 / ${fmtN(nRows)} 行,网页仅渲染前 ${MAXROWS} 行(完整导出用 CLI)</div>`:'';
  return `<div class="card"><div class="card-h"># 原始字节 CLUSTD
      <span class="badge rows">${fmtB(total)}</span>
      <span class="badge kind">${esc(d.container.algorithm||'RAW')}</span></div>
    ${legend}<div class="hexdump">${h}</div>${more}</div>`;
}
function tableCards(d){
  let h='';
  d.components.forEach((comp)=>{
    h+=`<div class="card"><div class="card-h">${kindIcon(comp.kind)}<span class="name">${esc(comp.name)}</span>
      <span class="badge kind">${esc(kindText(comp.kind))}</span>`;
    if(comp.kind!=='value')h+=`<span class="badge rows">${comp.rows.length} 行</span>`;
    if(comp.ddic)h+='<span class="badge ddic">✓ DDIC 类型化</span>';
    else if(comp.kind!=='value')h+='<span class="badge unt">未类型化</span>';
    h+='</div><div class="card-b">';
    if(comp.kind==='value'){h+=`<pre>${esc(comp.value_text)}</pre></div></div>`;return;}
    if(comp.ddic){
      h+='<table><thead><tr>'+comp.columns.map(col=>`<th>${esc(col.name)}</th>`).join('')+'</tr></thead><tbody>';
      comp.typed_rows.slice(0,50).forEach(r=>{
        h+='<tr>'+comp.columns.map(col=>`<td>${esc(r[col.name])}</td>`).join('')+'</tr>';});
      h+='</tbody></table>';
      if(comp.typed_rows.length>50)h+=`<div class="empty">… 共 ${comp.typed_rows.length} 行(网页仅显示前 50 行,导出用 CLI)</div>`;
    }else{
      h+='<pre>';
      comp.rows.slice(0,100).forEach(r=>{h+=esc(r.text)+'\\n';});
      if(comp.rows.length>100)h+=`\\n… 共 ${comp.rows.length} 行(网页仅显示前 100 行)`;
      h+='</pre>';
    }
    h+='</div></div>';
  });
  return h;
}
function renderDetail(){
  const d=CUR_DETAIL;
  if(!d||d.error){$('detail').innerHTML='<div class="empty">'+esc(d&&d.error||'无数据')+'</div>';return;}
  const c=d.container;
  const nRows=d.components.reduce((a,comp)=>a+(comp.rows?comp.rows.length:0),0);
  const keys=d.keys?Object.entries(d.keys).slice(0,3)
    .map(([k,v])=>`<span class="chip">${esc(k)}=<b>${esc(v)}</b></span>`).join(''):'';
  const head=`<div class="detail-head">
    <div class="head-row"><h2>${esc(d.label)} ${ringSvg(d.coverage_pct)}</h2>
      <div class="seg"><button id="vTab" class="${VIEW==='table'?'on':''}">表格</button><button id="vHex" class="${VIEW==='hex'?'on':''}">Hex</button></div>
      <button class="btn" id="copyBtn">⧉ 复制 JSON</button>
      <button class="btn primary" id="dlBtn">↓ 导出</button>
    </div>
    ${keys?`<div class="keys">${keys}</div>`:''}
    <div class="chips" style="margin-top:8px">
      <span class="chip">容器 <b>v${c.version}</b></span>
      <span class="chip">代码页 <b>${esc(c.codepage)}</b></span>
      <span class="chip"><b>${c.compressed_bytes}</b> → <b>${c.uncompressed_bytes}</b> B</span>
      <span class="chip">${esc(c.algorithm||'未压缩')}</span>
      <span class="chip">组件 <b>${d.components.length}</b></span>
      <span class="chip">行 <b>${nRows}</b></span>
      ${d.parse_ms!=null?`<span class="chip">解析 <b>${d.parse_ms}</b> ms</span>`:''}
    </div></div>`;
  const body=VIEW==='hex'?hexCard(d):tableCards(d);
  $('detail').innerHTML=head+body;
  $('vTab').onclick=()=>{VIEW='table';renderDetail();};
  $('vHex').onclick=()=>{VIEW='hex';renderDetail();};
  $('copyBtn').onclick=async()=>{
    try{
      await navigator.clipboard.writeText(JSON.stringify(
        {file:d.label,container:c,coverage_pct:d.coverage_pct,
         components:d.components},null,2));
      $('copyBtn').textContent='✓ 已复制';
      setTimeout(()=>$('copyBtn').textContent='⧉ 复制 JSON',1200);
    }catch(e){$('copyBtn').textContent='复制失败';}
  };
  $('dlBtn').onclick=()=>{
    const blob=new Blob([JSON.stringify(
      {cluster:d.keys||{file:CUR_FILE},container:c,coverage_pct:d.coverage_pct,
       components:d.components},null,1)],{type:'application/json'});
    const a=document.createElement('a');
    a.href=URL.createObjectURL(blob);
    a.download=(CUR_FILE||d.label.replace(/\\//g,'_'))+'.json';
    a.click();URL.revokeObjectURL(a.href);
  };
  $('main').scrollTop=0;
}
$('tq').oninput=renderTables;
let cqTimer=null;
$('cq').oninput=()=>{clearTimeout(cqTimer);cqTimer=setTimeout(loadClusters,250);};
init();
</script></body></html>"""


class _App:
    """serve 的共享状态:连库模式或离线目录模式。"""

    def __init__(self, args):
        self.args = args
        self.offline_dir = getattr(args, "offline_dir", None)
        self.schema = None
        self._keyinfo = {}
        if not self.offline_dir:
            conn = connect(args)
            cur = conn.cursor()
            self.schema = args.schema or current_schema(cur)
            conn.close()

    # ---- 表清单
    def tables(self):
        """[{name, rows|None, bytes|None}];db 行数取 M_TABLES(查不到则缺省)。"""
        if self.offline_dir:
            return [dict(name=f[:-4], rows=None,
                         bytes=os.path.getsize(os.path.join(self.offline_dir, f)))
                    for f in sorted(os.listdir(self.offline_dir))
                    if f.endswith(".bin")]
        conn = connect(self.args)
        cur = conn.cursor()
        cur.execute("""SELECT TABLE_NAME FROM TABLE_COLUMNS
                       WHERE SCHEMA_NAME=? AND COLUMN_NAME IN
                       ('RELID','SRTF2','CLUSTR','CLUSTD')
                       GROUP BY TABLE_NAME HAVING COUNT(DISTINCT COLUMN_NAME)=4
                       ORDER BY TABLE_NAME""", (self.schema,))
        names = [r[0] for r in cur.fetchall()]
        rows_map = {}
        try:
            cur.execute("""SELECT TABLE_NAME, RECORD_COUNT FROM M_TABLES
                           WHERE SCHEMA_NAME=?""", (self.schema,))
            rows_map = dict(cur.fetchall())
        except Exception:
            pass    # M_TABLES 权限缺失时退化为无行数
        conn.close()
        return [dict(name=t, rows=rows_map.get(t)) for t in names]

    # ---- RELID 分布(中栏过滤 chips)
    def relids(self, table):
        if self.offline_dir:
            return []
        conn = connect(self.args)
        cur = conn.cursor()
        cur.execute("""SELECT RELID, COUNT(*) FROM %s
                       GROUP BY RELID ORDER BY 2 DESC LIMIT 12""" % qid(self.schema, table))
        out = [dict(relid=r, n=n) for r, n in cur.fetchall()]
        conn.close()
        return out

    # ---- 键结构(带缓存)
    def keyinfo(self, table):
        if table not in self._keyinfo:
            conn = connect(self.args)
            self._keyinfo[table] = table_keyinfo(conn, self.schema, table)
            conn.close()
        return self._keyinfo[table]

    def clusters(self, table, offset, limit, search=None, relid=None):
        if self.offline_dir:
            files = [d["name"] for d in self.tables()]
            if search:
                files = [f for f in files if search.lower() in f.lower()]
            items = [dict(label=f, file=f + ".bin", chunks=1,
                          bytes=os.path.getsize(os.path.join(self.offline_dir, f + ".bin")))
                     for f in files]
            return items[offset:offset + limit]
        conn = connect(self.args)
        ident = table_keyinfo(conn, self.schema, table)
        ids = iter_cluster_ids(conn, self.schema, table, ident, relid,
                               limit, offset=offset, search=search)
        conn.close()
        items = []
        for kv, nch, tot in ids:
            parts = []
            for c in ident:
                if c != "MANDT":
                    parts.append("%s" % kv[c] if c in ("RELID", "SRTFD")
                                 else "%s=%s" % (c, kv[c]))
            items.append(dict(label="/".join(parts)[:60], chunks=nch, bytes=tot, kv=kv))
        return items

    def cluster_blob(self, table, kv):
        if self.offline_dir:
            path = os.path.join(self.offline_dir, table + ".bin")
            return open(path, "rb").read(), self._sidecar(path)
        conn = connect(self.args)
        ident = self.keyinfo(table)
        blob = fetch_cluster(conn, self.schema, table, ident, kv)
        return blob, None

    def _sidecar(self, path):
        for cand in (path + ".ddic.json", os.path.splitext(path)[0] + ".ddic.json"):
            if os.path.exists(cand):
                with open(cand, encoding="utf-8") as f:
                    return json.load(f)
        return None


def _make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):  # 静默访问日志
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            try:
                if u.path in ("/", "/index.html"):
                    body = _PAGE.replace("__VERSION__", __version__).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif u.path == "/api/tables":
                    self._json(dict(mode="offline" if app.offline_dir else "db",
                                    tables=app.tables()))
                elif u.path == "/api/relids":
                    self._json(dict(relids=app.relids(qs["table"][0])))
                elif u.path == "/api/clusters":
                    table = qs["table"][0]
                    offset = int(qs.get("offset", ["0"])[0])
                    limit = min(int(qs.get("limit", ["100"])[0]), 500)
                    search = qs.get("q", [None])[0]
                    relid = qs.get("relid", [None])[0]
                    items = app.clusters(table, offset, limit, search, relid)
                    self._json(dict(identity="key", items=items))
                elif u.path == "/api/cluster":
                    if app.offline_dir:
                        table = qs["file"][0][:-4]
                        blob, preload = app.cluster_blob(table, {})
                        kv = {}
                    else:
                        table = qs["table"][0]
                        kv = {k: v[0] for k, v in qs.items() if k != "table"}
                        blob, preload = app.cluster_blob(table, kv)
                    t0 = time.perf_counter()
                    info, out, comps, covered, named = parse_blob(blob)
                    if app.offline_dir:
                        if preload:
                            enrich_components(comps, info, DDIC(None, preload))
                    else:
                        conn = connect(app.args)
                        enrich_components(comps, info, DDIC(conn))
                        conn.close()
                    parse_ms = round((time.perf_counter() - t0) * 1000, 1)
                    label = " / ".join("%s" % v for k, v in kv.items()
                                       if k in ("RELID", "SRTFD")) or table
                    self._json(dict(
                        label=label, container=info, coverage_pct=covered,
                        keys=kv if kv else None, parse_ms=parse_ms,
                        raw_hex=blob.hex(), raw_len=len(blob),
                        components=[component_to_json(c, info["char_size"],
                                                      info["codepage"])
                                    for c in comps]))
                else:
                    self._json(dict(error="not found"), 404)
            except ToolError as e:
                self._json(dict(error=str(e)), 400)
            except Exception as e:
                self._json(dict(error="%s: %s" % (type(e).__name__, e)), 500)
    return Handler


def serve(args):
    app = _App(args)
    if getattr(args, "offline_dir", None):
        print("离线模式: %s" % os.path.abspath(args.offline_dir))
    else:
        print("连库模式: %s:%s schema=%s" % (args.host, args.port, app.schema))
    httpd = ThreadingHTTPServer((args.http_bind, args.http_port), _make_handler(app))
    url = "http://%s:%d/" % (args.http_bind, args.http_port)
    print("预览地址: %s (Ctrl+C 退出)" % url)
    if args.open:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
