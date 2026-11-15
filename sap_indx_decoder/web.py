"""本地 Web 预览(`indx_tool.py serve`)。

两种模式:
  连库        --host/--port/--user/--password(默认),浏览任意簇表
  --offline-dir DIR  浏览本地 dump 的 .bin 文件(自动加载 .ddic.json 侧车)

零框架依赖:stdlib http.server + 单页内联 HTML/JS。
默认只绑定 127.0.0.1。
"""
import json
import os
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import ToolError, __version__, parse_blob
from .db import (connect, current_schema, fetch_cluster, iter_cluster_ids,
                 qid, table_keyinfo)
from .ddic import DDIC, enrich_components
from .marker import component_to_json

_PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>sap-indx-decoder v%(version)s</title>
<style>
 html,body{background:#fff;color:#111}
 body{font:14px/1.5 -apple-system,'Segoe UI',Roboto,'PingFang SC',sans-serif;margin:0;display:flex;height:100vh}
 #left{width:230px;border-right:1px solid #ddd;overflow:auto;padding:10px}
 #mid{width:340px;border-right:1px solid #ddd;overflow:auto;padding:10px}
 #main{flex:1;overflow:auto;padding:14px}
 h3{margin:8px 0 4px} .item{padding:4px 6px;border-radius:4px;cursor:pointer;word-break:break-all}
 .item:hover{background:#eef}.item.sel{background:#dcecff}
 table{border-collapse:collapse;margin:6px 0}td,th{border:1px solid #ccc;padding:3px 8px;font-size:13px}
 th{background:#f2f2f2;text-align:left}.badge{display:inline-block;background:#e2e8f0;border-radius:8px;padding:1px 8px;margin-left:6px;font-size:12px}
 input{width:calc(100%% - 12px);padding:4px;margin-bottom:6px;background:#fff;color:#111}
 pre{background:#f6f8fa;padding:8px;overflow:auto;font-size:12px}
 .muted{color:#888;font-size:12px}
</style></head><body>
<div id="left"><h3>簇表</h3><input id="tq" placeholder="过滤…"><div id="tables"></div></div>
<div id="mid"><h3>簇</h3><input id="cq" placeholder="过滤…"><div id="clusters"></div></div>
<div id="main"><div id="detail" class="muted">← 选择一个簇</div></div>
<script>
let MODE=null, TABLES=[], CUR_TABLE=null, CLUSTERS=[], CUR_KV=null, CUR_FILE=null;
const $=id=>document.getElementById(id);
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
async function jget(url){const r=await fetch(url);const t=await r.text();
  try{return JSON.parse(t)}catch(e){throw new Error(t)}}
async function init(){
  const d=await jget('/api/tables'); MODE=d.mode; TABLES=d.tables;
  if(d.error){$('detail').innerHTML='<div class="muted">'+esc(d.error)+'</div>';}
  renderTables();
}
function renderTables(){
  const q=$('tq').value.toLowerCase();
  $('tables').innerHTML=TABLES.filter(t=>t.toLowerCase().includes(q))
    .map(t=>`<div class="item" data-t="${esc(t)}">${esc(t)}</div>`).join('');
  [...$('tables').children].forEach(el=>el.onclick=()=>pickTable(el.dataset.t));
}
async function pickTable(t){
  CUR_TABLE=t; CUR_FILE=null;
  document.querySelectorAll('#tables .item').forEach(e=>e.classList.toggle('sel',e.dataset.t===t));
  await loadClusters();
}
async function loadClusters(){
  let qs='table='+encodeURIComponent(CUR_TABLE);
  if(MODE==='db' && $('cq').value) qs+='&q='+encodeURIComponent($('cq').value);
  const d=await jget('/api/clusters?'+qs);
  CLUSTERS=d.items;
  renderClusters();
}
function renderClusters(){
  const q=$('cq').value.toLowerCase();
  const src=(MODE==='db')?CLUSTERS:CLUSTERS.filter(c=>c.label.toLowerCase().includes(q));
  $('clusters').innerHTML=src.map((c,i)=>`<div class="item" data-i="${i}">${esc(c.label)} <span class="muted">${c.chunks}块 ${c.bytes}B</span></div>`).join('');
  [...$('clusters').children].forEach(el=>el.onclick=()=>pickCluster(+el.dataset.i));
}
async function pickCluster(i){
  const c=CLUSTERS[i];
  document.querySelectorAll('#clusters .item').forEach((e,j)=>e.classList.toggle('sel',j===i));
  let qs;
  if(MODE==='offline'){CUR_FILE=c.file; qs='file='+encodeURIComponent(c.file);}
  else{CUR_KV=c.kv; qs=Object.entries(c.kv).map(([k,v])=>k+'='+encodeURIComponent(v)).join('&');
       qs='table='+encodeURIComponent(CUR_TABLE)+'&'+qs;}
  const d=await jget('/api/cluster?'+qs);
  renderDetail(d);
}
function renderDetail(d){
  if(d.error){$('detail').innerHTML='<div class="muted">'+esc(d.error)+'</div>';return;}
  const c=d.container;
  let h=`<h3>${esc(d.label)}</h3><div class="muted">容器 v${c.version} · 代码页 ${c.codepage} · `+
        `${c.compressed_bytes}→${c.uncompressed_bytes}B (${esc(c.algorithm||'未压缩')}) · `+
        `覆盖 ${d.coverage_pct}%%</div>`;
  d.components.forEach((comp,ci)=>{
    h+=`<div style="margin-top:14px"><b>${esc(comp.name)}</b> <span class="badge">${esc(comp.kind)}</span>`;
    if(comp.typed)h+='<span class="badge" style="background:#d1fae5">DDIC 类型化</span>';
    h+=`<span class="badge">${comp.kind==='value'?'':comp.rows.length+' 行'}</span></div>`;
    if(comp.kind==='value'){h+=`<pre>${esc(comp.value_text)}</pre>`;return;}
    if(comp.ddic){
      h+='<table><tr>'+comp.columns.map(col=>`<th>${esc(col.name)}</th>`).join('')+'</tr>';
      comp.typed_rows.slice(0,50).forEach(r=>{
        h+='<tr>'+comp.columns.map(col=>`<td>${esc(r[col.name])}</td>`).join('')+'</tr>';});
      if(comp.typed_rows.length>50)h+=`<tr><td colspan="${comp.columns.length}" class="muted">… 共 ${comp.typed_rows.length} 行</td></tr>`;
      h+='</table>';
    }else{
      h+='<pre>';
      comp.rows.slice(0,100).forEach(r=>{h+=esc(r.text)+'\\n';});
      if(comp.rows.length>100)h+=`… 共 ${comp.rows.length} 行\\n`;
      h+='</pre>';
    }
  });
  $('detail').innerHTML=h;
}
$('tq').oninput=renderTables;
let cqTimer=null;
$('cq').oninput=()=>{clearTimeout(cqTimer);cqTimer=setTimeout(loadClusters,250);};
init();
</script></body></html>""" % {"version": escape(__version__)}


class _App:
    """serve 的共享状态:连库模式或离线目录模式。"""

    def __init__(self, args):
        self.args = args
        self.offline_dir = getattr(args, "offline_dir", None)
        self.schema = None
        self._keyinfo = {}
        self._offline_files = None
        if not self.offline_dir:
            conn = connect(args)
            cur = conn.cursor()
            self.schema = args.schema or current_schema(cur)
            conn.close()

    # ---- 表清单
    def tables(self):
        if self.offline_dir:
            return sorted(f[:-4] for f in os.listdir(self.offline_dir)
                          if f.endswith(".bin"))
        conn = connect(self.args)
        cur = conn.cursor()
        cur.execute("""SELECT TABLE_NAME FROM TABLE_COLUMNS
                       WHERE SCHEMA_NAME=? AND COLUMN_NAME IN
                       ('RELID','SRTF2','CLUSTR','CLUSTD')
                       GROUP BY TABLE_NAME HAVING COUNT(DISTINCT COLUMN_NAME)=4
                       ORDER BY TABLE_NAME""", (self.schema,))
        tables = [r[0] for r in cur.fetchall()]
        conn.close()
        return tables

    # ---- 键结构(带缓存)
    def keyinfo(self, table):
        if table not in self._keyinfo:
            conn = connect(self.args)
            self._keyinfo[table] = table_keyinfo(conn, self.schema, table)
            conn.close()
        return self._keyinfo[table]

    def clusters(self, table, offset, limit, search=None):
        if self.offline_dir:
            files = self.tables()
            if search:
                files = [f for f in files if search.lower() in f.lower()]
            items = [dict(label=f, file=f + ".bin", chunks=1,
                          bytes=os.path.getsize(os.path.join(self.offline_dir, f + ".bin")))
                     for f in files]
            return items[offset:offset + limit]
        conn = connect(self.args)
        ident = table_keyinfo(conn, self.schema, table)
        ids = iter_cluster_ids(conn, self.schema, table, ident, None,
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
                    body = _PAGE.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif u.path == "/api/tables":
                    self._json(dict(mode="offline" if app.offline_dir else "db",
                                    tables=app.tables()))
                elif u.path == "/api/clusters":
                    table = qs["table"][0]
                    offset = int(qs.get("offset", ["0"])[0])
                    limit = min(int(qs.get("limit", ["100"])[0]), 500)
                    search = qs.get("q", [None])[0]
                    items = app.clusters(table, offset, limit, search)
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
                    info, out, comps, covered, named = parse_blob(blob)
                    if app.offline_dir:
                        if preload:
                            enrich_components(comps, info, DDIC(None, preload))
                    else:
                        conn = connect(app.args)
                        enrich_components(comps, info, DDIC(conn))
                        conn.close()
                    label = " / ".join("%s" % v for k, v in kv.items()
                                       if k in ("RELID", "SRTFD")) or table
                    self._json(dict(
                        label=label, container=info, coverage_pct=covered,
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
