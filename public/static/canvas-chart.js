/* canvas-chart.js — d17
 * All logic lives inside init() closure:
 *   - saveSnapshot persists across draws (key for zero-lag mousemove)
 *   - drawChart slices OHLC by window BEFORE using it → slotW always set → x-axis dates render
 *   - drawOverlay blits snapshot + crosshair only (no full redraw on mousemove)
 *   - initial draw called after data load, window toggle calls drawChart directly
 */
(function () {
  'use strict';
  function init() {
    console.log('[canvas-chart] init() called');
    var container = document.getElementById('chart-host');
    if (!container) { console.log('[canvas-chart] chart-host not found'); return; }
    console.log('[canvas-chart] chart-host found, ticker=' + container.dataset.ticker + ', viewport w=' + window.innerWidth + ' → canvas (responsive)');

    var ticker    = container.dataset.ticker;
    var chartJson = container.dataset.chartJson;
    var pngUrl    = container.dataset.png;

    /* ── Canvas setup (DOM API, not innerHTML — avoids aborting adjacent defer script) ── */
    var skeleton = document.getElementById('chartSkel_' + ticker.replace('.HK', '_HK').replace(/\./g, '_'));
    var oldImg   = container.querySelector('img');
    /* Clear onerror BEFORE removing — prevents stale onerror from wiping our canvas later */
    if (oldImg) { oldImg.onerror = null; oldImg.onload = null; oldImg.remove(); }
    if (skeleton) skeleton.remove();
    var cv       = document.createElement('canvas');
    cv.id        = 'cv';
    cv.style.display    = 'block';
    cv.style.width      = '100%';
    cv.style.height     = '540px';
    cv.style.borderRadius = '8px';
    cv.style.background = '#0d1220';
    container.appendChild(cv);
    var ctx  = cv.getContext('2d');
    console.log('[canvas-chart] canvas created, container children:', container.children.length);
    var ohlcUrl = chartJson.replace('/charts/', '/ohlc/').replace('.json', '_ohlc.json');
    var windowControls = document.getElementById('chart-window-controls');

    /* ── State ── */
    var currentWindow = 90;          // 30 or 90
    var cachedChartJson = null;       // raw chart JSON
    var cachedOhlcAll   = null;       // full OHLC array
    var drawPending     = false;

    /* ── Snapshot: persists across all draws (init scope, not draw scope) ── */
    var snap = null;
    function saveSnap() {
      if (!cv || cv.width === 0) return;
      snap = document.createElement('canvas');
      snap.width  = cv.width;
      snap.height = cv.height;
      snap.getContext('2d').drawImage(cv, 0, 0);
    }

    /* ── drawChart: all rendering + snapshot save (inner fn, has closure access) ── */
    function drawChart(winSize) {
      var dpr = window.devicePixelRatio || 1;
      var W = cv.clientWidth;
      var H = cv.clientHeight;
      // Safari fallback: if clientWidth/Height is 0 (flex layout not computed),
      // fall back to parent's actual width
      if (!W || !H) {
        var pRect = container.getBoundingClientRect();
        W = W || Math.floor(pRect.width) || 800;
        H = H || 540;
        console.log('[canvas-chart] using fallback W=' + W + ' H=' + H);
      }
      cv.width  = W * dpr;
      cv.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);

      /* Slice OHLC to the window BEFORE using */
      var ohlc = cachedOhlcAll;
      if (winSize && cachedOhlcAll && cachedOhlcAll.length > winSize) {
        ohlc = cachedOhlcAll.slice(-winSize);
      }

      var lb   = cachedChartJson && cachedChartJson.last_bar ? cachedChartJson.last_bar : {};
      var sup  = ((cachedChartJson && cachedChartJson.supports)  || []).map(function(s){ return s.level; }).filter(Boolean);
      var res  = ((cachedChartJson && cachedChartJson.resistances) || []).map(function(r){ return r.level; }).filter(Boolean);
      var ap   = (cachedChartJson && cachedChartJson.action_plan) || {};
      var cur  = lb.C;
      // 2026-09-12: fallback to last OHLC close when metadata JSON missing
      // (e.g. /charts/{ticker}.json 404s → cachedChartJson = {} → lb.C undefined).
      if (!cur && ohlc && ohlc.length) {
        cur = ohlc[ohlc.length - 1].close;
      }

      var T = {
        bg:'#0a0e1a', grid:'rgba(255,255,255,0.04)',
        text:'#8892a4', strong:'#e8eaf0',
        bull:'#10b981', bear:'#f43f5e',
        blue:'#818cf8', amber:'#f59e0b'
      };

      var padL=60, padR=60, padT=50, padB=40;
      var priceH = H - padT - padB;

      if (!cur) {
        ctx.fillStyle = '#8892a4';
        ctx.font = '13px JetBrains Mono,monospace';
        ctx.fillText('No data', 20, 30);
        saveSnap();
        return;
      }

      /* Price range */
      var prices = [];
      if (ohlc && ohlc.length) {
        ohlc.forEach(function(k){ if(k.high) prices.push(k.high); if(k.low) prices.push(k.low); });
      }
      prices = prices.concat([cur,ap.trigger_price,ap.target_price,ap.stop_price].filter(Boolean)).concat(sup).concat(res);
      var min = Math.min.apply(null, prices);
      var max = Math.max.apply(null, prices);
      var padP = (max - min) * 0.08;
      min -= padP; max += padP;
      var decimals = (max - min) < 5 ? 3 : 2;
      var pRange = max - min;
      var yOf = function(p){ return padT + ((max - p) / pRange) * priceH; };

      /* Phase tint */
      var phase = (cachedChartJson && cachedChartJson.phase || '').toLowerCase();
      if (phase.indexOf('down') !== -1) ctx.fillStyle = 'rgba(244,63,94,0.04)';
      else if (phase.indexOf('up')   !== -1) ctx.fillStyle = 'rgba(16,185,129,0.04)';
      else if (phase.indexOf('base') !== -1) ctx.fillStyle = 'rgba(245,158,11,0.03)';
      else ctx.fillStyle = T.bg;
      ctx.fillRect(padL, padT, W - padL - padR, priceH);

      /* Grid + price labels */
      ctx.strokeStyle = T.grid;
      ctx.lineWidth = 1;
      ctx.font = '10px JetBrains Mono,monospace';
      ctx.fillStyle = T.text;
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      for (var i = 0; i <= 6; i++) {
        var yy = padT + (priceH * i / 6);
        var pp = max - (pRange * i / 6);
        ctx.beginPath(); ctx.moveTo(padL,yy); ctx.lineTo(W-padR,yy); ctx.stroke();
        ctx.fillText(pp.toFixed(decimals), padL - 6, yy);
      }

      /* Candles — slotW ALWAYS set (key fix for x-axis) */
      var slotW = (W - padL - padR) / Math.max(1, ohlc ? ohlc.length : 1);
      if (ohlc && ohlc.length) {
        var candleW = Math.max(2, slotW * 0.7);
        ohlc.forEach(function(k, idx) {
          if (!k.open || !k.high || !k.low || !k.close) return;
          var x  = padL + idx * slotW + slotW / 2;
          var bull = k.close >= k.open;
          ctx.strokeStyle = bull ? T.bull : T.bear;
          ctx.fillStyle   = bull ? T.bull : T.bear;
          ctx.lineWidth   = 1;
          ctx.beginPath(); ctx.moveTo(x, yOf(k.high)); ctx.lineTo(x, yOf(k.low)); ctx.stroke();
          var top = Math.min(yOf(k.open), yOf(k.close));
          var bh  = Math.max(1, Math.abs(yOf(k.close) - yOf(k.open)));
          ctx.fillRect(x - candleW/2, top, candleW, bh);
          if (!bull) {
            ctx.fillStyle = T.bg;
            ctx.fillRect(x - candleW/2, top, candleW, bh);
            ctx.strokeStyle = T.bear;
            ctx.strokeRect(x - candleW/2, top, candleW, bh);
          }
        });
      }

      /* MA lines */
      if (ohlc && ohlc.length > 20) {
        var closes = ohlc.map(function(k){ return k.close; });
        var maCfg = {5:T.bull, 10:T.blue, 20:T.amber};
        [5,10,20].forEach(function(per) {
          var maVals = closes.map(function(_, i) {
            if (i < per - 1) return null;
            var s = 0; for (var j2=i-per+1; j2<=i; j2++) s += closes[j2];
            return s / per;
          });
          ctx.strokeStyle = maCfg[per];
          ctx.lineWidth  = per === 5 ? 1 : per === 10 ? 1.2 : 1.5;
          ctx.beginPath();
          var started = false;
          maVals.forEach(function(v, i) {
            if (v == null) { started = false; return; }
            var x = padL + i * slotW + slotW / 2;
            if (!started) { ctx.moveTo(x, yOf(v)); started = true; }
            else ctx.lineTo(x, yOf(v));
          });
          ctx.stroke();
          var last = maVals[maVals.length-1];
          if (last != null) {
            // Stagger MA labels vertically to avoid overlap (MA5 at line, MA10 offset up, MA20 offset down)
            var yOff = per === 5 ? 0 : per === 10 ? -10 : 10;
            var lx = padL + (maVals.length-1) * slotW + slotW/2;
            var ly = yOf(last) + yOff;
            var lbl = 'MA' + per + ' ' + last.toFixed(decimals);
            ctx.font = 'bold 8px JetBrains Mono,monospace';
            var tw = ctx.measureText(lbl).width + 6;
            var th = 11;
            var by = ly - th/2;
            // Clamp to chart area
            if (by < padT) by = padT;
            if (by + th > padT + priceH) by = padT + priceH - th;
            ctx.fillStyle = 'rgba(13,18,32,0.92)';
            ctx.fillRect(lx+4, by, tw, th);
            ctx.strokeStyle = maCfg[per];
            ctx.lineWidth = 1;
            ctx.strokeRect(lx+4, by, tw, th);
            ctx.fillStyle = maCfg[per];
            ctx.textAlign = 'left';
            ctx.textBaseline = 'middle';
            ctx.fillText(lbl, lx+7, by + th/2);
          }
        });
      }

      /* X-axis dates — auto-fit, no overlap. M/D format. */
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.fillStyle = T.text;
      ctx.font = '9px JetBrains Mono,monospace';
      var fmtDate = function(d) {
        if (!d) return '';
        var parts = d.split('-');
        if (parts.length === 3) return (parseInt(parts[1],10)) + '/' + (parseInt(parts[2],10));
        return d.slice(5);
      };
      if (ohlc && ohlc.length) {
        var n = ohlc.length;
        var labelW = 40;
        var maxLabels = Math.max(2, Math.floor((W - padL - padR) / labelW));
        var step = Math.max(1, Math.ceil(n / maxLabels));
        ctx.textAlign = 'left';
        ctx.fillText(fmtDate(ohlc[0].date), padL + 2, H - 22);
        ctx.textAlign = 'center';
        for (var di = step; di < n - 1; di += step) {
          var dx = padL + di * slotW + slotW / 2;
          ctx.fillText(fmtDate(ohlc[di].date), dx, H - 22);
        }
        var lastX = padL + (n-1) * slotW + slotW/2;
        var prevIdx = Math.floor((n-1) / step) * step;
        var prevX = padL + prevIdx * slotW + slotW/2;
        if (lastX - prevX > labelW) {
          ctx.textAlign = 'right';
          ctx.fillText(fmtDate(ohlc[n-1].date), lastX, H - 22);
        }
        ctx.textAlign = 'center';
      } else {
        var rng = ((cachedChartJson && cachedChartJson.data_range)||'').split('→').map(function(s){ return s.trim(); });
        ctx.textAlign = 'left';
        ctx.fillText(rng[0]||'', padL+4, H-22);
        ctx.textAlign = 'right';
        ctx.fillText((rng[1]||'').slice(0,7), W-padR-4, H-22);
        ctx.textAlign = 'center';
      }

      /* S/R + plan lines */
      function hline(price, color, label, side) {
        if (!price) return;
        var yy = yOf(price);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.setLineDash([4,4]);
        ctx.beginPath(); ctx.moveTo(padL,yy); ctx.lineTo(W-padR,yy); ctx.stroke();
        ctx.setLineDash([]);
        var txt = label + ' ' + price.toFixed(decimals);
        ctx.font = 'bold 9px JetBrains Mono,monospace';
        ctx.textBaseline = 'middle';
        var tw = ctx.measureText(txt).width + 8;
        var th = 13;
        var bx, by;
        if (side === 'right') {
          ctx.textAlign = 'right';
          bx = W - padR - tw;
          by = yy - th/2;
        } else {
          ctx.textAlign = 'left';
          bx = padL;
          by = yy - th/2;
        }
        // Clamp inside chart
        if (by < padT) by = padT;
        if (by + th > padT + priceH) by = padT + priceH - th;
        ctx.fillStyle = 'rgba(13,18,32,0.92)';
        ctx.fillRect(bx, by, tw, th);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.strokeRect(bx, by, tw, th);
        ctx.fillStyle = color;
        ctx.fillText(txt, side === 'right' ? bx + tw - 4 : bx + 4, by + th/2);
      }
      sup.slice(0,3).forEach(function(s,i){ hline(s,T.bull,'S'+(i+1),'left'); });
      res.slice(0,3).forEach(function(r,i){ hline(r,T.bear,'R'+(i+1),'right'); });
      hline(ap.trigger_price, T.blue,  'Trig','right');
      hline(ap.target_price, T.bull,  'Tgt', 'left');
      hline(ap.stop_price,   T.bear,  'Stop','right');

      /* Current price line + label */
      var yCur = yOf(cur);
      ctx.strokeStyle = T.strong;
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(padL,yCur); ctx.lineTo(W-padR,yCur); ctx.stroke();
      var lblTxt = cur.toFixed(decimals) + ' (' + (lb.chg_pct >= 0 ? '+' : '') + (lb.chg_pct||0).toFixed(2) + '%)';
      ctx.font = 'bold 12px JetBrains Mono,monospace';
      var lblW = ctx.measureText(lblTxt).width + 16;
      // 2026-09-18: always pin label to TOP-RIGHT corner (above chart area, at y=20).
      // Previous logic positioned at yCur which made it cover candles when cur
      // was in upper portion of price range. Top-right is always safe + visible.
      var lblColor = lb.chg_pct >= 0 ? T.bull : T.bear;
      var lblX = W - padR - lblW;
      var lblY = padT + 10;  // 10px below top of chart
      ctx.fillStyle = lblColor;
      ctx.fillRect(lblX, lblY, lblW, 20);
      ctx.fillStyle = '#0a0e1a';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(lblTxt, lblX+8, lblY + 10);

      /* Plan annotation box */
      if (ap.verdict && ap.verdict !== 'WAIT') {
        var pLines = [
          (ap.verdict==='BUY'?'\u25b2':'\u25bc')+' '+(ap.strategy||'')+'  \u00b7 '+((ap.rr_ratio)||0).toFixed(2)+' R:R',
          'Trigger '+((ap.trigger_price)||0).toFixed(decimals)+'  \u00b7  Target '+((ap.target_price)||0).toFixed(decimals),
          'Risk '+((ap.risk_pct)||0).toFixed(2)+'%  \u00b7  Reward '+((ap.reward_pct)||0).toFixed(2)+'%'
        ];
        ctx.font = '11px JetBrains Mono,monospace';
        var maxPW = Math.max.apply(null, pLines.map(function(l){ return ctx.measureText(l).width; })) + 24;
        var boxH = pLines.length * 16 + 16;
        var bx = padL+12, by = padT+12;
        ctx.fillStyle = 'rgba(13,18,32,0.92)';
        ctx.strokeStyle = ap.verdict==='BUY' ? T.bull : T.bear;
        ctx.lineWidth = 1;
        ctx.fillRect(bx,by,maxPW,boxH);
        ctx.strokeRect(bx,by,maxPW,boxH);
        ctx.fillStyle = ap.verdict==='BUY' ? T.bull : T.bear;
        ctx.font = 'bold 11px JetBrains Mono,monospace';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';
        ctx.fillText(pLines[0],bx+12,by+8);
        ctx.fillStyle = T.strong;
        ctx.font = '10px JetBrains Mono,monospace';
        ctx.fillText(pLines[1],bx+12,by+26);
        ctx.fillText(pLines[2],bx+12,by+42);
      }

      /* Phase badge — bottom-left, away from S/R/Trig labels at edges */
      if (cachedChartJson && cachedChartJson.phase) {
        var phTxt = cachedChartJson.phase.replace(/_/g,' ').toUpperCase();
        ctx.font = 'bold 9px JetBrains Mono,monospace';
        var phW = ctx.measureText(phTxt).width + 12;
        var phH = 16;
        var phX = padL + 12;
        var phY = padT + priceH - phH - 12;
        ctx.fillStyle = 'rgba(13,18,32,0.92)';
        ctx.strokeStyle = T.amber;
        ctx.lineWidth = 1;
        ctx.fillRect(phX, phY, phW, phH);
        ctx.strokeRect(phX, phY, phW, phH);
        ctx.fillStyle = T.amber;
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(phTxt, phX + 6, phY + phH/2);
      }

      /* Save snapshot — called after every full draw, uses init scope snap */
      saveSnap();
    }

    /* ── Mousemove overlay: blit snapshot + crosshair (NO full redraw) ── */
    var overlayBusy = false;
    cv.addEventListener('mousemove', function(e) {
      if (!snap || overlayBusy) return;
      overlayBusy = true;
      requestAnimationFrame(function() {
        overlayBusy = false;
        var r  = cv.getBoundingClientRect();
        var x  = e.clientX - r.left;
        var y  = e.clientY - r.top;
        var W  = cv.clientWidth;
        var H  = cv.clientHeight;
        var padL=60, padR=60, padT=50, padB=40;

        /* Re-derive price scale from cached data (same as drawChart) */
        var lb = (cachedChartJson && cachedChartJson.last_bar) || {};
        var sup = ((cachedChartJson && cachedChartJson.supports) || []).map(function(s){ return s.level; });
        var res = ((cachedChartJson && cachedChartJson.resistances) || []).map(function(r){ return r.level; });
        var cur = lb.C || 0;
        var prices = [cur].concat(sup).concat(res).filter(Boolean);
        if (!prices.length) prices = [100];
        var min2 = Math.min.apply(null, prices)*0.95;
        var max2 = Math.max.apply(null, prices)*1.05;
        var pRange2 = max2 - min2;
        var priceH2 = H - padT - padB;
        var dec2 = (max2-min2) < 5 ? 3 : 2;

        /* Fast blit: restore base chart */
        ctx.clearRect(0, 0, W, H);
        ctx.drawImage(snap, 0, 0, W, H);

        /* Crosshair */
        ctx.strokeStyle = 'rgba(129,140,248,0.4)';
        ctx.lineWidth = 1;
        ctx.setLineDash([3,3]);
        ctx.beginPath(); ctx.moveTo(x,padT); ctx.lineTo(x,H-padB); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(padL,y); ctx.lineTo(W-padR,y); ctx.stroke();
        ctx.setLineDash([]);

        /* Price label */
        var p = max2 - ((y-padT)/priceH2)*pRange2;
        ctx.fillStyle = 'rgba(129,140,248,0.9)';
        ctx.font = '10px JetBrains Mono,monospace';
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        ctx.fillRect(W-padR, y-8, 52, 16);
        // 2026-09-17: light text on light bg (was dark on light = readable, but
        // bg sometimes blended with edge). Use white text for max contrast.
        ctx.fillStyle = '#ffffff';
        ctx.fillText(p.toFixed(dec2), W-padR-4, y);

        /* OHLC tooltip */
        if (cachedOhlcAll && cachedOhlcAll.length > 5) {
          var oAll = cachedOhlcAll;
          var oView = currentWindow && oAll.length > currentWindow ? oAll.slice(-currentWindow) : oAll;
          var n = oView.length;
          var sw = (W-padL-padR)/Math.max(1,n);
          var idx = Math.floor((x-padL)/sw);
          if (idx >= 0 && idx < oView.length) {
            var k = oView[idx];
            if (k && k.open && k.high && k.low && k.close) {
              var lines = [k.date,
                           'O '+k.open.toFixed(dec2)+'  H '+k.high.toFixed(dec2),
                           'L '+k.low.toFixed(dec2)+'  C '+k.close.toFixed(dec2),
                           'Vol '+((k.volume||0)/1e6).toFixed(1)+'M'];
              var tw=110, th=52;
              var tx=x+8, ty2=padT+8;
              if (tx+tw > W-padR) tx = x-tw-8;
              ctx.fillStyle = 'rgba(13,18,32,0.95)';
              ctx.strokeStyle = '#818cf8';
              ctx.lineWidth = 1;
              ctx.fillRect(tx,ty2,tw,th);
              ctx.strokeRect(tx,ty2,tw,th);
              ctx.fillStyle = '#e8eaf0';
              ctx.font = '10px JetBrains Mono,monospace';
              ctx.textAlign = 'left';
              ctx.textBaseline = 'top';
              lines.forEach(function(l,ii){ ctx.fillText(l,tx+5,ty2+5+ii*11); });
            }
          }
        }
      });
    });

    /* ── Data loading ── */
    function loadData() {
      console.log('[canvas-chart] loadData fetching', chartJson, ohlcUrl);
      Promise.all([
        fetch(chartJson).then(function(r){ return r.json(); }).catch(function(){ return {}; }),
        fetch(ohlcUrl).then(function(r){ return r.json(); }).catch(function(){ return []; })
      ]).then(function(results) {
        cachedChartJson = results[0];
        cachedOhlcAll   = results[1];
        // Wait for fonts to load (Safari: canvas text needs font to be ready)
        if (document.fonts && document.fonts.ready) {
          document.fonts.ready.then(function() { drawChart(currentWindow); });
        } else {
          drawChart(currentWindow);
        }
      }).catch(function(err) {
        container.innerHTML = '<img src="'+pngUrl+'" alt="'+ticker+' daily chart" loading="eager" style="width:100%;height:auto;border-radius:8px"><div style="color:#f43f5e;padding:8px;font-size:12px">Chart load failed: '+err+'</div>';
      });
    }

    /* ── Window toggle (30D / 90D) ── */
    if (windowControls) {
      windowControls.querySelectorAll('button[data-window]').forEach(function(b) {
        b.addEventListener('click', function() {
          windowControls.querySelectorAll('button').forEach(function(x) {
            x.classList.remove('active');
            x.style.background = 'transparent';
            x.style.borderColor = 'var(--border)';
            x.style.color = 'var(--fg-2)';
          });
          b.classList.add('active');
          b.style.background = 'var(--blue-dim)';
          b.style.borderColor = 'var(--blue)';
          b.style.color = 'var(--blue)';
          currentWindow = parseInt(b.dataset.window, 10);
          var windowLabel = document.getElementById('chart-window-label');
          if (windowLabel) windowLabel.textContent = 'Daily \u00b7 ' + currentWindow + 'D';
          if (cachedChartJson) {
            if (document.fonts && document.fonts.ready) {
              document.fonts.ready.then(function() { drawChart(currentWindow); });
            } else {
              drawChart(currentWindow);
            }
          }
        });
      });
    }

    /* ── Resize ── */
    var resizeTimer = null;
    window.addEventListener('resize', function() {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function() {
        if (cachedChartJson) drawChart(currentWindow);
      }, 250);
    });

    loadData();
  }

  /* ── Bootstrap ── */
  console.log('[canvas-chart] bootstrap readyState=' + document.readyState);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
