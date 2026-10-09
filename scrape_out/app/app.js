/* ===== Estado y utilidades ===== */
const $ = s => document.querySelector(s);
let ET = [], E = {}, TP, DT, M, J, SD;
const S = { fam: null, cat: null, sub: null, q: '', search: false, m: null, tf: new Set(), ef: null, pin: null };
const nz = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const pdfHref = f => 'data/pdfs/' + f;
const root = document.documentElement;
const dark = () => root.dataset.theme ? root.dataset.theme === 'dark' : false;
const col = a => a[dark() ? 1 : 0];
const fc = f => col(J[f].c), cc = (f, c) => col(J[f].cats[c].c);
const sc = (f, c, s) => { const k = J[f]?.cats[c]; return k ? col(k.subs[s] || k.c) : '#8267e2'; };
const ICO = { moon: '<svg class="i" viewBox="0 0 24 24"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z"/></svg>', sun: '<svg class="i" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>' };
const NET = '<svg class="i" style="width:2.4rem;height:2.4rem" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><circle cx="5" cy="6" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="5" cy="18" r="2"/><circle cx="19" cy="18" r="2"/><path d="M7 7l3 3M17 7l-3 3M7 17l3-3M17 17l-3-3"/></svg>';

function motif(id) {
  let h = 0; for (const c of id) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const r = n => { h = (h * 1103515245 + 12345) >>> 0; return (h >>> 8) % n; }; let s = '';
  for (let i = 0; i < 6; i++) { const x = 15 + r(70), y = 15 + r(70), R = 6 + r(16); s += i % 3 ? `<circle cx="${x}" cy="${y}" r="${R}" style="fill:none;stroke:var(--pri);stroke-width:2"/>` : `<circle cx="${x}" cy="${y}" r="${R / 2.2}" style="fill:var(--pri);opacity:.55"/>`; }
  return `<svg viewBox="0 0 100 100">${s}</svg>`;
}
const pic = m => m.img ? `<img src="${m.img}" alt="" decoding="async">` : motif(m.id);
const bar = (r, cls = '') => `<div class="bar ${cls}">${ET.map(e => r[e.id] ? `<i style="width:${100 * r[e.id] / r.total}%;background:${e.c}" title="${e.n}: ${r[e.id]}"></i>` : '').join('')}</div>`;

/* ===== Navegación jerárquica: landing > categoría > subcategoría > métodos ===== */
function pills(f, c, s, click) {
  const p = (t, color, go) => click ? `<button class="pl" style="--c:${color}" data-go="${go}">${esc(t)}</button>` : `<span class="pl" style="--c:${color}">${esc(t)}</span>`;
  let h = click ? '<button class="pl" data-go="0">Inicio</button>' : '';
  if (f) h += (h ? '<i>›</i>' : '') + p(J[f].nombre, fc(f), 1);
  if (c) h += '<i>›</i>' + p(c, cc(f, c), 2);
  if (s) h += '<i>›</i>' + p(s, sc(f, c, s), 3);
  return `<div class="pills">${h}</div>`;
}
function card(m) {
  const r = m.res;
  return `<button class="mc" data-id="${m.id}" style="--c:${sc(m.familia, m.categoria, m.subcategoria)}"><div class="top"><div class="th">${pic(m)}</div><div><h3>${esc(m.nombre)}</h3><small>${esc(m.subcategoria)}</small></div></div>${r.total ? bar(r) : ''}<div class="meta"><span>${r.total || '—'} tareas</span><span>confianza ${m.estudio.confianza}</span></div></button>`;
}
function grouped(ms) {
  const byFam = {};
  ms.forEach(m => (byFam[m.familia] = byFam[m.familia] || []).push(m));
  let h = '';
  for (const f of Object.keys(J)) {
    const fam = byFam[f];
    if (!fam) continue;
    h += `<section class="fam" style="--c:${fc(f)}"><h2 class="fam-t">${J[f].nombre}</h2>`;
    const bySub = {};
    fam.forEach(m => (bySub[m.subcategoria] = bySub[m.subcategoria] || []).push(m));
    for (const s of Object.keys(bySub)) {
      const c = bySub[s][0].categoria;
      h += `<h3 class="sub-t" style="--c:${sc(f, c, s)}">${esc(s)}</h3><div class="grid">${bySub[s].map(card).join('')}</div>`;
    }
    h += `</section>`;
  }
  return h;
}
function view() {
  if (S.search) {
    const q = nz(S.q);
    const ms = q
      ? M.filter(m => nz([m.nombre, m.ideal_para, m.estudio.titulo, m.subcategoria, m.categoria].join(' ')).includes(q))
      : M;
    let h = q ? `<div class="pills"><span class="pl">${ms.length} resultados</span></div>` : '';
    h += ms.length ? grouped(ms) : '<p class="none">Sin resultados.</p>';
    $('#view').innerHTML = h;
    return;
  }
  let h = '';
  if (!S.fam) {
    h = `<div class="grid">${Object.keys(J).map(f => `<button class="lv" data-fam="${f}" style="--c:${fc(f)}"><div class="tile">${NET}</div><h2>${J[f].nombre}</h2><p>${J[f].d}</p><span class="bdg">${M.filter(m => m.familia == f).length} Métodos</span></button>`).join('')}</div>`;
  } else {
    h = pills(S.fam, S.cat, S.sub, true);
    const inF = M.filter(m => m.familia == S.fam);
    if (!S.cat) {
      h += `<div class="grid">${Object.keys(J[S.fam].cats).map(c => { const ms = inF.filter(m => m.categoria == c); return `<button class="lv" data-cat="${esc(c)}" style="--c:${cc(S.fam, c)}"><h2>${esc(c)}</h2><p>${J[S.fam].cats[c].d}</p><span class="bdg">${ms.length} métodos en ${new Set(ms.map(m => m.subcategoria)).size} categorías</span></button>`; }).join('')}</div>`;
    } else if (!S.sub) {
      const ms = inF.filter(m => m.categoria == S.cat), subs = [...new Set(ms.map(m => m.subcategoria))];
      h += `<div class="grid">${subs.map(s => `<button class="lv" data-sub="${esc(s)}" style="--c:${sc(S.fam, S.cat, s)}"><h2>${esc(s)}</h2><p>${SD[s] || ''}</p><span class="bdg">${ms.filter(m => m.subcategoria == s).length} Métodos</span></button>`).join('')}</div>`;
    } else {
      h += `<div class="grid">${inF.filter(m => m.categoria == S.cat && m.subcategoria == S.sub).map(card).join('')}</div>`;
    }
  }
  $('#view').innerHTML = h;
}

/* ===== Overlay de método ===== */
function legend(m) {
  const r = m.res, cnt = {}; m.etapas.forEach(e => e.t.forEach(t => cnt[t[2]] = (cnt[t[2]] || 0) + 1));
  return (m.src === 'heuristica' ? '<div class="warn">Etiquetas provisionales por reglas de palabras clave. Ejecuta classify_tasks.py (DeepSeek) para clasificarlas.</div>' : '') +
    `<div class="lab">Etapas del análisis (tareas por etapa)</div><div class="chips">${ET.filter(e => r[e.id]).map(e => `<span class="sg" style="--c:${e.c}"><i></i>${e.n} <b>${r[e.id]}</b></span>`).join('')}</div>${bar(r, 'big')}` +
    '<p class="cap">Pasa el cursor (o toca) un círculo para ver la tarea. Un punto de color en el borde indica recolección de datos.</p>';
}

function footer(m) {
  const r = m.res, cnt = {}; m.etapas.forEach(e => e.t.forEach(t => cnt[t[2]] = (cnt[t[2]] || 0) + 1));
  return  `<div class="lab">Tipo de tarea (filtro)</div><div class="chips">${Object.keys(TP).filter(k => cnt[k]).map(k => `<button class="chip" data-tf="${k}" style="--c:${TP[k].c}" title="${esc(TP[k].d)}"><i></i>${TP[k].n} <b>${cnt[k]}</b></button>`).join('')}</div>`;
}
function flow(m) {
  if (!m.etapas.length) return '<p class="none">El texto del estudio no permitió reconstruir los pasos del procedimiento.</p>';
  const AR = '<svg class="arr" viewBox="0 0 40 12"><path d="M0 6H34M28 1L35 6L28 11"/></svg>'; let i = 0;
  const nodo = `<div class="node" style="--i:0"><h4>Datos de entrada</h4><p>Datos recolectados (filtro)</p><div class="chips">${m.entradas.length ? m.entradas.map(e => `<button class="chip" data-ef="${e.id}" style="--c:${DT[e.id].c}"><span><i></i> ${DT[e.id].n}</span><b>${e.n}</b></button>`).join('') : '<small>Sin recolección explícita.</small>'}</div></div>`;
  const cl = {};
  m.etapas.forEach((et, si) => {
    i++; const g = E[et.id];
    cl[et.id] = `<div class="col" style="--i:${i}"><div class="ch" style="--c:${g.c}"><b>${g.n}</b><small>${et.t.length}</small></div><div class="dots">${et.t.map((t, k) => `<button class="dot${t[3].length ? ' hd' : ''}" data-s="${si}" data-k="${k}" style="--c:${TP[t[2]].c}${t[3].length ? ';--d:' + DT[t[3][0]].c : ''}">${t[0]}</button>`).join('')}</div></div>`;
  });
  const p = [nodo]; ['preparacion', 'exploracion'].forEach(k => cl[k] && p.push(cl[k]));
  const a = cl.analisis, s = cl.sintesis;
  if (a && s) p.push(`<div class="loop" style="--i:${i}"><span class="ll">↻ Retroalimentación continua</span>${a}<svg class="lo" viewBox="0 0 40 90"><path d="M4 28C12 8 30 8 36 24M30 19L36 25L38 17"/><path d="M36 62C28 82 10 82 4 66M10 71L4 65L2 73"/></svg>${s}</div>`);
  else [a, s].forEach(x => x && p.push(x));
  cl.adicional && p.push(cl.adicional);
  return p.join(AR);
}
function openM(id) {
  const m = S.m = M.find(x => x.id == id); S.tf = new Set(); S.ef = null; S.pin = null;
  const e = m.estudio, au = e.autores.length > 2 ? e.autores[0] + ' et al.' : e.autores.join(', ');
  $('#sheet').innerHTML = `<div class="sh-top"><div>${pills(m.familia, m.categoria, m.subcategoria, false)}<div class="ttl"><div class="th">${pic(m)}</div><h2>${esc(m.nombre)}</h2></div></div><button class="ib" id="x" aria-label="Cerrar"><svg class="i" viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12"/></svg></button></div>
<div class="two"><section class="ideal"><h3>Ideal para</h3><p>${esc(m.ideal_para)}</p></section>
<section class="ideal"><h3>Estudio de referencia</h3><p class="st-t">${esc(e.titulo)}</p><p class="st-a">${esc(au)}${e.anio ? ' · ' + e.anio : ''}${e.tipo ? ' · ' + esc(e.tipo) : ''}<span class="cf ${e.confianza}">confianza ${e.confianza}</span></p>${e.patrones ? `<p class="pat">${esc(e.patrones)}</p>` : ''}${e.url ? `<a class="lnk" href="${esc(e.url)}" target="_blank" rel="noopener">Abrir estudio ↗</a>` : '<small>Sin enlace disponible</small>'}  ${e.pdf ? `<a class="lnk" href="${esc(pdfHref(e.pdf))}" target="_blank" rel="noopener">Abrir PDF ↗</a>` : ''}<p class="note" title="${esc(e.notas)}">${e.confianza != 'alta' && e.notas ? ' ' + esc(e.notas.slice(0, 160)) + (e.notas.length > 160 ? '…' : '') : ''}</p></section></div>
<section class="fb"><h3>Flujo del método</h3>${legend(m)}<div class="track go" id="fl">${flow(m)}</div>${footer(m)}</section>`;
  $('#ov').classList.add('open'); $('#ov').scrollTop = 0; document.body.style.overflow = 'hidden'; $('#x').focus();
}
function closeM() { $('#ov').classList.remove('open'); document.body.style.overflow = ''; hideTip(); S.pin = null; }

/* ===== Filtros (sin re-render) y tooltip único ===== */
function applyF() {
  document.querySelectorAll('[data-tf]').forEach(b => b.classList.toggle('on', S.tf.has(b.dataset.tf)));
  document.querySelectorAll('[data-ef]').forEach(b => b.classList.toggle('on', S.ef === b.dataset.ef));
  document.querySelectorAll('.dot').forEach(b => { const t = S.m.etapas[b.dataset.s].t[b.dataset.k]; b.classList.toggle('off', !((!S.tf.size || S.tf.has(t[2])) && (!S.ef || t[3].includes(S.ef)))); });
}
const tip = $('#tip'), canHover = matchMedia('(hover:hover)').matches;
function showTip(b) {
  const et = S.m.etapas[b.dataset.s], t = et.t[b.dataset.k], tp = TP[t[2]];
  const ch = (c, n) => `<span class="chip on" style="--c:${c}">${esc(n)}</span>`;
  tip.innerHTML = `<div class="tt"><b>Paso ${t[0]}</b> · ${E[et.id].n}</div><p>${esc(t[1])}</p><div class="chips">${ch(tp.c, tp.n)}${t[4] != 'NA' ? ch('#ec4899', 'Narrativa ' + S.D.narr[t[4]].toLowerCase()) : ''}${t[5] != 'NA' ? ch('#f59e0b', 'Tono ' + S.D.tonos[t[5]].toLowerCase()) : ''}${t[3].map(d => ch(DT[d].c, DT[d].n)).join('')}${t[6] == 'B' && t[2] != 'NC' ? ch('#6b7280', 'confianza baja') : ''}</div>`;
  tip.style.display = 'block';
  const r = b.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
  let x = Math.max(8, Math.min(r.left + r.width / 2 - w / 2, innerWidth - w - 8)), y = r.bottom + 8;
  if (y + h > innerHeight - 8) y = Math.max(8, r.top - h - 8);
  tip.style.left = x + 'px'; tip.style.top = y + 'px';
}
function hideTip() { tip.style.display = 'none'; }

/* ===== Eventos (delegación) ===== */
document.addEventListener('click', e => {
  const t = e.target.closest('[data-id],[data-fam],[data-cat],[data-sub],[data-go],#x,[data-tf],[data-ef],.dot');
  if (!t) { if (e.target.id == 'ov') closeM(); else if (S.pin) { hideTip(); S.pin = null; } return; }
  const d = t.dataset;
  if (d.id) openM(d.id);
  else if (d.fam) { S.fam = d.fam; S.cat = S.sub = null; view(); scrollTo(0, 0); }
  else if (d.cat) { S.cat = d.cat; S.sub = null; view(); }
  else if (d.sub) { S.sub = d.sub; view(); }
  else if (d.go) { const n = +d.go; if (n < 1) S.fam = null; if (n < 2) S.cat = null; if (n < 3) S.sub = null; view(); }
  else if (t.id == 'x') closeM();
  else if (d.tf) { S.tf.has(d.tf) ? S.tf.delete(d.tf) : S.tf.add(d.tf); applyF(); }
  else if (d.ef) { S.ef = S.ef == d.ef ? null : d.ef; applyF(); }
  else if (t.classList.contains('dot') && !canHover) { if (S.pin === t) { hideTip(); S.pin = null; } else { S.pin = t; showTip(t); } }
});
document.addEventListener('pointerover', e => { const b = canHover && e.target.closest?.('.dot'); if (b) showTip(b); });
document.addEventListener('pointerout', e => { if (canHover && e.target.closest?.('.dot')) hideTip(); });
document.addEventListener('focusin', e => { const b = e.target.closest?.('.dot'); if (b) showTip(b); });
document.addEventListener('focusout', e => { if (e.target.closest?.('.dot')) hideTip(); });
document.addEventListener('scroll', hideTip, true);
document.addEventListener('keydown', e => { if (e.key == 'Escape' && $('#ov').classList.contains('open')) closeM(); });
let tm; const qin = $('#q');
qin.addEventListener('input', e => { clearTimeout(tm); tm = setTimeout(() => { S.q = e.target.value; if (S.q) S.search = true; view(); }, 150); });
qin.addEventListener('keydown', e => { if (e.key === 'Enter') { S.search = true; view(); } });
const tb = $('#th'), sync = () => tb.innerHTML = dark() ? ICO.sun : ICO.moon;
tb.onclick = () => { root.dataset.theme = dark() ? 'light' : 'dark'; sync(); if (M) { view(); if ($('#ov').classList.contains('open')) openM(S.m.id); } };
sync();

/* ===== Arranque: datos embebidos; mensaje claro si no hay ===== */
function boot(d) { S.D = d; ET = d.etapas; E = Object.fromEntries(ET.map(e => [e.id, e])); TP = d.tipos; DT = d.datos; M = d.metodos; J = d.jer; SD = d.subdesc; const n = $('#nmet'); if (n) n.textContent = M.length; view(); }
(async () => {
  const d = typeof D !== 'undefined' ? D : null;
  if (!d) { $('#view').innerHTML = '<div class="err"><b>No hay datos cargados.</b><br>Abre <code>metodologias_app.html</code> (generado con <code>python build_app.py</code>), no <code>template.html</code>.</div>'; return; }
  boot(d);
})();
