/* Shared compact formatting for visible counts and chart tooltips. */
function usageTokens(value) {
 if (!Number.isFinite(value) || value < 0) return '—';
 const units=['','K','M','B','T']; let unit=0;
 while(value>=1000 && unit<units.length-1){value/=1000;unit++;}
 value=Math.round(value*10)/10;
 if(value>=1000 && unit<units.length-1){value/=1000;unit++;}
 return value.toLocaleString('en-US',{maximumFractionDigits:1})+units[unit];
}
function usageCell(value){return `<span class="usage-value" title="${Number.isFinite(value)?usageTokens(value)+' tokens':'用量暂不可读'}">${usageTokens(value)}</span>`;}
function usagePie(title, rows) {
 const colors=['#ef873e','#769f99','#8b91b7','#d7b36f','#ba8c9d','#7fa3bf','#a2ae83','#b6aaa0'];
 const slices=rows.filter(row=>Number.isFinite(row.tokens)&&row.tokens>0),total=slices.reduce((sum,row)=>sum+row.tokens,0);
 let offset=0;
 const segments=slices.map((row,index)=>{
  const ratio=row.tokens/total,percent=(ratio*100).toLocaleString('zh-CN',{maximumFractionDigits:1})+'%',start=offset;offset+=ratio*100;
  const tip=`${esc(row.name)} · ${percent} · ${usageTokens(row.tokens)} tokens`;
  return {row,index,percent,tip,svg:`<circle class="usage-sector" cx="70" cy="70" r="52" pathLength="100" fill="none" stroke="${colors[index%colors.length]}" stroke-width="22" stroke-dasharray="${ratio*100} ${100-ratio*100}" stroke-dashoffset="${-start}" transform="rotate(-90 70 70)" tabindex="0" role="img" aria-label="${tip}" data-usage-tip="${tip}" data-usage-index="${index}"/>`};
 });
 return `<section class="usage-breakdown" aria-label="${title}"><h3>${title}</h3><div class="usage-ring"><svg viewBox="0 0 140 140" aria-label="${title}占比"><circle cx="70" cy="70" r="52" fill="none" stroke="#f1eee9" stroke-width="22"/>${segments.map(s=>s.svg).join('')}</svg><div class="usage-ring-center"><strong>${usageTokens(total)}</strong><span>${total?'tokens':'暂无用量'}</span></div><div class="usage-pie-tip" role="tooltip" hidden></div></div><div class="usage-pie-legend">${segments.map(s=>`<button type="button" class="usage-legend-item" data-usage-tip="${s.tip}" data-usage-index="${s.index}" aria-label="${s.tip}"><i style="background:${colors[s.index%colors.length]}"></i><span>${esc(s.row.name)}</span><strong>${s.percent}</strong></button>`).join('')}</div></section>`;
}
function usageDashboard() {
 const days=state.usage||[],total=days.reduce((n,u)=>n+u.input+u.output,0),max=Math.max(...days.map(u=>u.input+u.output),1),breakdown=state.usage_breakdown||{};
 return `<section class="panel usage-dashboard"><div class="panel-head"><h2>模型用量</h2></div><div class="usage-dashboard-body"><div class="usage-trend"><div class="chart-top"><span class="chart-total">${usageTokens(total)} <span class="subtle">tokens</span></span></div><div class="chart monthly-chart">${days.map((u,index)=>`<div class="chart-col" tabindex="0" aria-describedby="usage-tip-${index}"><div class="chart-bar" style="height:${(u.input+u.output)/max*90}%"></div><div class="usage-tooltip" role="tooltip" id="usage-tip-${index}"><span>${esc(u.day)}</span><strong>${usageTokens(u.input+u.output)} <small>tokens</small></strong></div><label>${[0,7,14,21,29].includes(index)?esc(u.day):''}</label></div>`).join('')}</div>${days.length?'':'<div class="usage-empty">暂无调用记录</div>'}</div>${usagePie('按供应商',breakdown.providers||[])}${usagePie('按类型',breakdown.types||[])}</div></section>`;
}
// Delegated once: renders and navigation do not accumulate subscriptions.
function showUsageTip(target) {
 const section=target?.closest('.usage-breakdown');if(!section)return;
 const tip=section.querySelector('.usage-pie-tip');tip.textContent=target.dataset.usageTip;tip.hidden=false;
 section.querySelectorAll('.usage-sector').forEach(s=>s.classList.toggle('is-highlighted',s.dataset.usageIndex===target.dataset.usageIndex));
}
function hideUsageTip(target){const section=target?.closest('.usage-breakdown');if(!section)return;section.querySelector('.usage-pie-tip').hidden=true;section.querySelectorAll('.is-highlighted').forEach(s=>s.classList.remove('is-highlighted'));}
for(const event of ['pointerover','focusin'])document.addEventListener(event,e=>{const target=e.target.closest('[data-usage-tip]');if(target)showUsageTip(target);});
for(const event of ['pointerout','focusout'])document.addEventListener(event,e=>{const target=e.target.closest('[data-usage-tip]');if(target&&!target.contains(e.relatedTarget))hideUsageTip(target);});
