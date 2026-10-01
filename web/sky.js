"use strict";
class CourseSky {
  constructor(helpers) {
    Object.assign(this, helpers);
    this.el = id => document.getElementById(id);
    this.canvas = this.el("sky-canvas"); this.ctx = this.canvas.getContext("2d");
    this.colors = ["#ffb3c6", "#80d5ff", "#ffc66d", "#b4a1ff", "#68e0ba", "#f18bea", "#b9e778", "#ff977d", "#86aaff", "#e8d49c"];
    this.points = []; this.byId = new Map(); this.evidence = new Map();
    this.scale = 1; this.panX = 0; this.panY = 0; this.filter = ""; this.selected = null;
    this.sequence = 0; this.frame = null; this.drag = null; this.width = 1; this.height = 1;
    this.el("sky-bank").addEventListener("change", () => { this.filter = this.el("sky-bank").value; this.reset(); });
    this.el("sky-reset").addEventListener("click", () => this.reset());
    this.el("sky-zoom-in").addEventListener("click", () => this.zoom(1.45));
    this.el("sky-zoom-out").addEventListener("click", () => this.zoom(1 / 1.45));
    this.el("sky-recall").addEventListener("click", () => this.fitRecall());
    this.el("sky-toggle").addEventListener("click", () => {
      const hidden = !this.el("sky-body").hidden; this.el("sky-body").hidden = hidden;
      this.el("sky-toggle").textContent = hidden ? "展开星空" : "收起星空";
      this.el("sky-toggle").setAttribute("aria-expanded", String(!hidden)); if (!hidden) this.resize();
    });
    this.el("sky-chunk-form").addEventListener("submit", event => { event.preventDefault(); this.select(this.el("sky-chunk-id").value.trim(), true); });
    this.el("sky-selected-evidence").addEventListener("click", () => { const evidence = this.evidence.get(this.selected); if (evidence) this.focusEvidence(evidence.evidence_id); });
    this.canvas.addEventListener("wheel", event => { event.preventDefault(); const [x,y] = this.pointer(event); this.zoom(Math.exp(-Math.max(-120, Math.min(120, event.deltaY)) * .006), x, y); }, {passive:false});
    this.canvas.addEventListener("pointerdown", event => { if (event.button !== 0) return; const [x,y] = this.pointer(event); this.drag = {x,y, moved:false}; this.canvas.setPointerCapture(event.pointerId); this.canvas.classList.add("dragging"); this.el("sky-tooltip").hidden = true; });
    this.canvas.addEventListener("pointermove", event => {
      const [x,y] = this.pointer(event);
      if (this.drag) { const dx=x-this.drag.x, dy=y-this.drag.y; if (Math.abs(dx)+Math.abs(dy)>2) this.drag.moved=true; this.panX+=dx; this.panY+=dy; this.drag.x=x; this.drag.y=y; this.drawSoon(); return; }
      const point = this.nearest(x,y); const tooltip=this.el("sky-tooltip"); tooltip.hidden=!point;
      if (point) { tooltip.textContent=`${this.data.companies[point.bank]} · PDF ${point.page} · ${point.type === "table" ? "表格" : "正文"}`; tooltip.style.left=`${Math.min(x+12, Math.max(8,this.width-220))}px`; tooltip.style.top=`${Math.max(8,y-35)}px`; }
    });
    this.canvas.addEventListener("pointerup", event => { if (!this.drag) return; const moved=this.drag.moved; this.drag=null; this.canvas.classList.remove("dragging"); if (!moved) { const point=this.nearest(...this.pointer(event)); if (point) this.select(point.id); } });
    this.canvas.addEventListener("pointercancel", () => { this.drag=null; this.canvas.classList.remove("dragging"); });
    this.canvas.addEventListener("pointerleave", () => { this.el("sky-tooltip").hidden=true; });
    this.canvas.addEventListener("keydown", event => {
      const offsets={ArrowLeft:[35,0], ArrowRight:[-35,0], ArrowUp:[0,35], ArrowDown:[0,-35]};
      if (offsets[event.key]) { event.preventDefault(); this.panX+=offsets[event.key][0]; this.panY+=offsets[event.key][1]; this.drawSoon(); }
      if (["+","=","-"].includes(event.key)) { event.preventDefault(); this.zoom(event.key === "-" ? 1/1.45 : 1.45); }
      if (event.key === "Home") { event.preventDefault(); this.reset(); }
    });
    this.observer = new ResizeObserver(() => this.resize()); this.observer.observe(this.canvas.parentElement);
  }
  async load() {
    try {
      this.data = await this.api("/api/sky"); this.points=this.data.points; this.byId=new Map(this.points.map(p=>[p.id,p]));
      this.el("sky-total").textContent=`${this.points.length.toLocaleString("zh-CN")}颗星 · ${this.data.companies.length}家银行`;
      this.data.companies.forEach((company,index) => {
        const option=document.createElement("option"); option.value=String(index); option.textContent=company; this.el("sky-bank").append(option);
        const button=document.createElement("button"); button.type="button"; button.className="sky-bank-chip"; button.setAttribute("aria-pressed","false");
        const dot=document.createElement("span"); dot.style.backgroundColor=this.colors[index%this.colors.length]; dot.setAttribute("aria-hidden","true");
        button.append(dot,document.createTextNode(company)); button.dataset.bank=String(index);
        button.addEventListener("click",()=>{this.filter=this.filter===String(index)?"":String(index); this.el("sky-bank").value=this.filter; this.reset();}); this.el("sky-legend").append(button);
      });
      this.resize();
    } catch (error) { this.el("sky-view-status").textContent="星空图加载失败"; this.el("sky-selection-status").textContent=error.message; }
  }
  pointer(event) { const box=this.canvas.getBoundingClientRect(); return [event.clientX-box.left,event.clientY-box.top]; }
  resize() { const box=this.canvas.getBoundingClientRect(); if (!box.width || !box.height) return; this.width=box.width; this.height=box.height; const ratio=Math.min(window.devicePixelRatio||1,2); this.canvas.width=Math.round(box.width*ratio); this.canvas.height=Math.round(box.height*ratio); this.ctx.setTransform(ratio,0,0,ratio,0,0); this.drawSoon(); }
  factor() { return Math.min(this.width,this.height)*.43*this.scale; }
  position(point) { const factor=this.factor(); return [this.width/2+this.panX+point.x*factor,this.height/2+this.panY-point.y*factor]; }
  visible(point) { return this.filter==="" || point.bank===Number(this.filter); }
  drawSoon() { if (this.frame) return; this.frame=requestAnimationFrame(()=>{this.frame=null; this.draw();}); }
  draw() {
    const ctx=this.ctx; ctx.clearRect(0,0,this.width,this.height);
    const gradient=ctx.createRadialGradient(this.width*.5,this.height*.5,10,this.width*.5,this.height*.5,Math.max(this.width,this.height)*.7);
    gradient.addColorStop(0,"#14263f"); gradient.addColorStop(1,"#060e1d"); ctx.fillStyle=gradient; ctx.fillRect(0,0,this.width,this.height);
    // Only real corpus points are drawn; the background has no decorative fake stars.
    let visibleCount=0; const radius=Math.min(2.3,.85+Math.sqrt(this.scale)*.24);
    for (const point of this.points) {
      if (!this.visible(point)) continue; const [x,y]=this.position(point); if (x<0||y<0||x>this.width||y>this.height) continue; visibleCount++;
      ctx.globalAlpha=this.evidence.size ? .22 : .58; ctx.fillStyle=this.colors[point.bank%this.colors.length]; ctx.beginPath(); ctx.arc(x,y,radius,0,Math.PI*2); ctx.fill();
    }
    ctx.globalAlpha=1;
    for (const [id,evidence] of this.evidence) {
      const point=this.byId.get(id); if (!point || !this.visible(point)) continue; const [x,y]=this.position(point);
      ctx.shadowColor="#fff0a4"; ctx.shadowBlur=12; ctx.fillStyle=this.colors[point.bank%this.colors.length]; ctx.beginPath(); ctx.arc(x,y,4,0,Math.PI*2); ctx.fill(); ctx.shadowBlur=0;
      ctx.strokeStyle="#ffeda3"; ctx.lineWidth=1.5; ctx.beginPath(); ctx.arc(x,y,7,0,Math.PI*2); ctx.stroke();
      ctx.fillStyle="#fff3bf"; ctx.font="600 11px system-ui"; ctx.fillText(evidence.evidence_id,x+10,y-7);
    }
    const selected=this.byId.get(this.selected); if (selected && this.visible(selected)) { const [x,y]=this.position(selected); ctx.strokeStyle="#fff"; ctx.lineWidth=1.7; ctx.beginPath(); ctx.arc(x,y,11,0,Math.PI*2); ctx.stroke(); }
    this.el("sky-view-status").textContent=this.data?`${visibleCount.toLocaleString("zh-CN")}颗星在视野内 · ${this.scale.toFixed(1)}×`:"正在准备星空…";
    document.querySelectorAll(".sky-bank-chip").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.bank===this.filter)));
  }
  zoom(multiplier,x=this.width/2,y=this.height/2) { const next=Math.min(24,Math.max(.5,this.scale*multiplier)); const factor=next/this.scale; this.panX=(this.panX-(x-this.width/2))*factor+(x-this.width/2); this.panY=(this.panY-(y-this.height/2))*factor+(y-this.height/2); this.scale=next; this.drawSoon(); }
  reset() { this.scale=1; this.panX=0; this.panY=0; this.el("sky-tooltip").hidden=true; this.drawSoon(); }
  nearest(x,y) {
    let best=null, distance=100;
    // Recall points receive priority so overlapping text/table chunks remain usable.
    for (const id of this.evidence.keys()) { const p=this.byId.get(id); if (!p||!this.visible(p)) continue; const [px,py]=this.position(p); const d=(x-px)**2+(y-py)**2; if (d<distance) {best=p; distance=d;} }
    if (best) return best;
    for (const p of this.points) { if (!this.visible(p)) continue; const [px,py]=this.position(p); const d=(x-px)**2+(y-py)**2; if (d<distance) {best=p; distance=d;} }
    return best;
  }
  async select(id,center=false) {
    if (!this.byId.has(id)) {this.el("sky-selection-status").textContent="未找到此切块编号，请输入当前知识库的完整块ID。"; return;}
    this.selected=id; this.el("sky-chunk-id").value=id; this.el("sky-selected").hidden=true; this.el("sky-selection-status").textContent="正在加载原文…";
    const request=++this.sequence; const p=this.byId.get(id);
    if (center) {this.filter=""; this.el("sky-bank").value=""; this.panX=-p.x*this.factor(); this.panY=p.y*this.factor();} this.drawSoon();
    try {
      const row=await this.api(`/api/chunk?id=${encodeURIComponent(id)}`); if (request!==this.sequence) return;
      this.el("sky-selected-title").textContent=`${row.company} · PDF第${row.pdf_page_start}页`;
      this.el("sky-selected-meta").textContent=`${row.section} · ${this.qualityLabels[row.quality_status]||"待核对"}`;
      this.el("sky-selected-content").textContent=row.content; this.el("sky-selected-report").href=this.reportLink(row);
      this.el("sky-selection-status").textContent=`已选中：${id}`; this.el("sky-selected").hidden=false; this.el("sky-selected-evidence").hidden=!this.evidence.has(id);
    } catch (error) { if (request===this.sequence) this.el("sky-selection-status").textContent=error.message; }
  }
  setEvidence(rows,hash) {
    this.evidence=new Map(rows.filter(row=>this.byId.has(row.chunk_id)).map(row=>[row.chunk_id,row]));
    const banks=new Set([...this.evidence.keys()].map(id=>this.byId.get(id).bank));
    this.el("sky-recall-status").textContent=`当前记录召回${rows.length}块，图中对应${this.evidence.size}颗星，覆盖${banks.size}/${this.data?.companies.length||0}家银行。${hash && hash!==this.data?.corpus_sha256?"此记录使用较早语料，按相同块ID展示。":""}`;
    this.el("sky-recall").disabled=!this.evidence.size;
    this.el("sky-selected-evidence").hidden=!this.evidence.has(this.selected);
    this.filter=""; this.el("sky-bank").value=""; this.reset();
  }
  fitRecall() {
    const points=[...this.evidence.keys()].map(id=>this.byId.get(id)); if (!points.length) return;
    this.filter=""; this.el("sky-bank").value="";
    const xs=points.map(p=>p.x), ys=points.map(p=>p.y); const x=(Math.max(...xs)+Math.min(...xs))/2, y=(Math.max(...ys)+Math.min(...ys))/2;
    const base=Math.min(this.width,this.height)*.43; this.scale=Math.min(12,Math.max(1,Math.min(this.width*.65/(Math.max(...xs)-Math.min(...xs)||.15),this.height*.65/(Math.max(...ys)-Math.min(...ys)||.15))/base));
    this.panX=-x*this.factor(); this.panY=y*this.factor(); this.drawSoon();
  }
}
