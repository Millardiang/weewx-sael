// ===================== instruments.js =====================
// Instrument gauges (wind speed/gust, wind compass, barometer — ECharts;
// thermometer, rain gauge — D3/hand-rolled SVG) — split out of reimagine.js.
// Load this before reimagine.js, and load echarts.min.js and d3.js before this.

const SPEED_GAUGE_MAX_MS = 32.6;

function polarXY(cx, cy, r, angleDeg){
  const rad = angleDeg * Math.PI / 180;
  return { x: cx + r*Math.sin(rad), y: cy - r*Math.cos(rad) };
}

const GAUGE_UNIT_CONFIG = {
  mph: {max:70, step:10, toDisplay:v=>ms2mph(v), unitsLabel:'mph'},
  kmh: {max:120, step:20, toDisplay:v=>ms2kmh(v), unitsLabel:'km/h'},
  kt:  {max:60, step:10, toDisplay:v=>ms2kt(v), unitsLabel:'kt'},
  ms:  {max:32, step:4, toDisplay:v=>v, unitsLabel:'m/s'},
  bf:  {max:12, step:2, toDisplay:v=>beaufort(v).force, unitsLabel:'Bft'},
};

let rainGaugeHasPiezo = false;

// ===================== ECharts init helper (sizing-safe) =====================
// echarts.init() measures the container's pixel size at the instant it's
// called. If that happens before the flex/aspect-ratio layout has settled
// (entirely possible during initial page load), it locks in a wrong size
// and never re-measures — stretching the ring and shrinking thin pointer
// needles down to invisible. The SVG renderer avoids canvas pixel
// stretching (same robustness as the D3 gauges, which are
// resolution-independent and don't measure anything), and the
// ResizeObserver re-measures once the container's real size is known.
function initEchartsResizable(wrap){
  const chart = echarts.init(wrap, null, {renderer: 'svg'});
  if(typeof ResizeObserver !== 'undefined'){
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(wrap);
  }
  requestAnimationFrame(() => chart.resize());
  return chart;
}

// ===================== Combined wind speed/gust gauge (ECharts) =====================
const WIND_ANEMO_BEAUFORT_RANGES_MS = [0,0.3,1.5,3.3,5.4,7.9,10.7,13.8,17.1,20.7,24.4,28.4,32.6,36.0];
const WIND_ANEMO_BEAUFORT_COLORS = ['#85a3aa','#7e98bb','#6e90d0','#0f94a7','#39a239','#c2863e','#c8420d','#d20032','#af5088','#754a92','#45698d','#c1fc77','#f1ff6c'];
const WIND_ECHARTS_RING_COLOR = WIND_ANEMO_BEAUFORT_RANGES_MS.slice(1).map((t,i) => [t/36, WIND_ANEMO_BEAUFORT_COLORS[i]]);
let lastWindSpeedMs = null, lastWindGustMs = null;
let windCombinedChart = null;

function windTickLabel(ms, unit){
  if(unit==='bf') return String(beaufort(ms).force);
  return String(Math.round(GAUGE_UNIT_CONFIG[unit] ? GAUGE_UNIT_CONFIG[unit].toDisplay(ms) : ms));
}

function buildCombinedWindGaugeEchartsOption(speedMs, gustMs, sys){
  const unit = sys.wind;
  const hiddenAxis = { axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false } };
  return {
    series: [
      {
        type: 'gauge',
        name: 'Speed',
        startAngle: 225,
        endAngle: -45,
        min: 0,
        max: 36,
        splitNumber: 6,
        radius: '52%',
        center: ['50%', '46%'],
        axisLine: { lineStyle: { width: 14, color: WIND_ECHARTS_RING_COLOR } },
        axisTick: { distance: -14, length: 5, lineStyle: { color: '#8c9a95', width: 1 } },
        splitLine: { distance: -14, length: 14, lineStyle: { color: '#8c9a95', width: 2 } },
        axisLabel: {
          distance: -34, color: '#8c9a95', fontSize: 9, fontFamily: "'JetBrains Mono',monospace",
          formatter: v => windTickLabel(v, unit)
        },
        pointer: { show: true, length: '75%', width: 8, itemStyle: { color: '#5EE6F5' } },
        anchor: { show: true, showAbove: true, size: 8, itemStyle: { color: '#5EE6F5', borderWidth: 0 } },
        title: { show: true, offsetCenter: ['-42%', '140%'], color: '#5EE6F5', fontSize: 11 },
        detail: { show: true, offsetCenter: ['-42%', '160%'], color: '#5EE6F5', fontSize: 15, fontWeight: 700,
          formatter: () => fmtWind(speedMs, unit) },
        data: [{ value: speedMs, name: 'Speed' }]
      },
      {
        type: 'gauge',
        name: 'Gust',
        startAngle: 225,
        endAngle: -45,
        min: 0,
        max: 36,
        radius: '52%',
        center: ['50%', '46%'],
        ...hiddenAxis,
        pointer: { show: true, length: '52%', width: 7, itemStyle: { color: '#FFB347' } },
        anchor: { show: true, showAbove: true, size: 7, itemStyle: { color: '#FFB347', borderWidth: 0 } },
        title: { show: true, offsetCenter: ['42%', '140%'], color: '#FFB347', fontSize: 11 },
        detail: { show: true, offsetCenter: ['42%', '160%'], color: '#FFB347', fontSize: 15, fontWeight: 700,
          formatter: () => fmtWind(gustMs, unit) },
        data: [{ value: gustMs, name: 'Gust' }]
      }
    ]
  };
}

function buildCombinedWindGaugeEcharts(sys){
  const wrap = document.getElementById('windCombinedEchartsWrap');
  if(!wrap || typeof echarts === 'undefined') return;
  if(!windCombinedChart) windCombinedChart = initEchartsResizable(wrap);
  windCombinedChart.setOption(
    buildCombinedWindGaugeEchartsOption(lastWindSpeedMs ?? 0, lastWindGustMs ?? 0, sys),
    true
  );
}

function setCombinedWindGaugeValue(speedMs, gustMs, sys){
  if(!windCombinedChart) return;
  windCombinedChart.setOption(buildCombinedWindGaugeEchartsOption(speedMs ?? 0, gustMs ?? 0, sys), true);
}

// ===================== Wind direction compass (ECharts) =====================
const COMPASS_OCTANTS = ['N','NE','E','SE','S','SW','W','NW'];
let lastWindDirDeg = null, lastWindDirDeg10 = null;
let windCompassChart = null;

function compassTickLabel(deg){
  const idx = Math.round(deg/45) % 8;
  return COMPASS_OCTANTS[idx];
}

function buildWindCompassGaugeEchartsOption(dirDeg, dirDeg10){
  const cur = dirDeg ?? 0, avg = dirDeg10 ?? 0;
  const ARROW_PATH = 'path://M10,30 L20,2 L10,10 L0,2 Z';
  const hiddenAxis = { axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false } };
  return {
    series: [
      {
        type: 'gauge',
        name: 'Now',
        startAngle: 90,
        endAngle: -270,
        min: 0,
        max: 360,
        splitNumber: 8,
        radius: '52%',
        center: ['50%', '46%'],
        axisLine: { lineStyle: { width: 2, color: [[1, '#343e43']] } },
        axisTick: { splitNumber: 3, distance: -14, length: 5, lineStyle: { color: '#8c9a95', width: 1 } },
        splitLine: { distance: -14, length: 14, lineStyle: { color: '#8c9a95', width: 2 } },
        axisLabel: {
          distance: -34, color: '#8c9a95', fontSize: 10, fontWeight: 700,
          fontFamily: "'JetBrains Mono',monospace",
          formatter: v => compassTickLabel(v)
        },
        pointer: { icon: ARROW_PATH, length: '78%', width: 16, offsetCenter: [0, 0], itemStyle: { color: '#5EE6F5' } },
        anchor: { show: false },
        title: { show: true, offsetCenter: ['-42%', '140%'], color: '#5EE6F5', fontSize: 10 },
        detail: { show: true, offsetCenter: ['-42%', '160%'], color: '#5EE6F5', fontSize: 14, fontWeight: 700,
          formatter: () => `${Math.round(cur)}\u00b0 ${degToCompass(cur)}` },
        data: [{ value: cur, name: 'Now' }]
      },
      {
        type: 'gauge',
        name: '10-min avg',
        startAngle: 90,
        endAngle: -270,
        min: 0,
        max: 360,
        radius: '52%',
        center: ['50%', '46%'],
        ...hiddenAxis,
        pointer: { icon: ARROW_PATH, length: '52%', width: 12, offsetCenter: [0, 0], itemStyle: { color: '#7DEB87' } },
        anchor: { show: false },
        title: { show: true, offsetCenter: ['42%', '140%'], color: '#7DEB87', fontSize: 10 },
        detail: { show: true, offsetCenter: ['42%', '160%'], color: '#7DEB87', fontSize: 14, fontWeight: 700,
          formatter: () => `${Math.round(avg)}\u00b0` },
        data: [{ value: avg, name: '10-min avg' }]
      }
    ]
  };
}

function buildWindCompassGaugeEcharts(){
  const wrap = document.getElementById('windCompassEchartsWrap');
  if(!wrap || typeof echarts === 'undefined') return;
  if(!windCompassChart) windCompassChart = initEchartsResizable(wrap);
  windCompassChart.setOption(
    buildWindCompassGaugeEchartsOption(lastWindDirDeg, lastWindDirDeg10),
    true
  );
}

function setWindCompassGaugeEchartsValue(dirDeg, dirDeg10){
  if(!windCompassChart) return;
  windCompassChart.setOption(buildWindCompassGaugeEchartsOption(dirDeg, dirDeg10), true);
}

// ===================== Barometer gauge (ECharts) =====================
const BARO_ZONE_BANDS_HPA = [[940,970,'#ff00ff'],[970,990,'#f8d747'],[990,1010,'#007fff'],[1010,1030,'#2e8b57'],[1030,1060,'#ff6347']];
const BARO_ECHARTS_RING_COLOR = BARO_ZONE_BANDS_HPA.map(b => [(b[1]-940)/120, b[2]]);
let lastBaroHpa = null, lastBaroMinHpa = null, lastBaroMaxHpa = null;
let baroEchartsChart = null;

function baroTickLabel(hpa, unit){
  switch(unit){
    case 'inhg': return String(Math.round(hpa2inhg(hpa)*10)/10);
    case 'mmhg': return String(Math.round(hpa2mmhg(hpa)));
    case 'kpa':  return String(Math.round(hpa2kpa(hpa)*10)/10);
    default: return String(Math.round(hpa));
  }
}

function buildBarometerGaugeEchartsOption(currentHpa, minHpa, maxHpa, sys){
  const unit = sys.pressure;
  const hiddenAxis = { axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false } };
  return {
    series: [
      {
        type: 'gauge',
        name: 'Pressure',
        startAngle: 225,
        endAngle: -45,
        min: 940,
        max: 1060,
        splitNumber: 6,
        radius: '52%',
        center: ['50%', '46%'],
        axisLine: { lineStyle: { width: 14, color: BARO_ECHARTS_RING_COLOR } },
        axisTick: { distance: -14, length: 5, lineStyle: { color: '#8c9a95', width: 1 } },
        splitLine: { distance: -14, length: 14, lineStyle: { color: '#8c9a95', width: 2 } },
        axisLabel: {
          distance: -34, color: '#8c9a95', fontSize: 9, fontFamily: "'JetBrains Mono',monospace",
          formatter: v => baroTickLabel(v, unit)
        },
        pointer: { show: true, length: '75%', width: 8, itemStyle: { color: '#5EE6F5' } },
        anchor: { show: true, showAbove: true, size: 8, itemStyle: { color: '#5EE6F5', borderWidth: 0 } },
        title: { show: true, offsetCenter: ['0%', '135%'], color: '#8c9a95', fontSize: 10,
          formatter: () => `Low ${fmtPressure(minHpa, unit)} \u00b7 High ${fmtPressure(maxHpa, unit)}` },
        detail: { show: true, offsetCenter: ['0%', '155%'], color: '#5EE6F5', fontSize: 16, fontWeight: 700,
          formatter: () => fmtPressure(currentHpa, unit) },
        data: [{ value: currentHpa, name: 'Pressure' }]
      },
      {
        type: 'gauge',
        name: 'Low',
        startAngle: 225,
        endAngle: -45,
        min: 940,
        max: 1060,
        radius: '52%',
        center: ['50%', '46%'],
        ...hiddenAxis,
        pointer: { icon: 'rect', length: '88%', width: 8, offsetCenter: [0, 0], itemStyle: { color: '#7DEB87' } },
        anchor: { show: false },
        title: { show: false },
        detail: { show: false },
        data: [{ value: minHpa, name: 'Low' }]
      },
      {
        type: 'gauge',
        name: 'High',
        startAngle: 225,
        endAngle: -45,
        min: 940,
        max: 1060,
        radius: '52%',
        center: ['50%', '46%'],
        ...hiddenAxis,
        pointer: { icon: 'rect', length: '88%', width: 8, offsetCenter: [0, 0], itemStyle: { color: '#FFB347' } },
        anchor: { show: false },
        title: { show: false },
        detail: { show: false },
        data: [{ value: maxHpa, name: 'High' }]
      }
    ]
  };
}

function buildBarometerGaugeEcharts(sys){
  const wrap = document.getElementById('baroEchartsWrap');
  if(!wrap || typeof echarts === 'undefined') return;
  if(!baroEchartsChart) baroEchartsChart = initEchartsResizable(wrap);
  baroEchartsChart.setOption(
    buildBarometerGaugeEchartsOption(lastBaroHpa ?? 1013, lastBaroMinHpa ?? 1013, lastBaroMaxHpa ?? 1013, sys),
    true
  );
}

function setBarometerGaugeEchartsValue(currentHpa, minHpa, maxHpa, sys){
  if(!baroEchartsChart) return;
  baroEchartsChart.setOption(
    buildBarometerGaugeEchartsOption(currentHpa ?? 1013, minHpa ?? currentHpa ?? 1013, maxHpa ?? currentHpa ?? 1013, sys),
    true
  );
}

// ===================== Thermometer instrument =====================
const THERMO_TUBE_TOP = 6;
const THERMO_BULB_CX = 65, THERMO_BULB_CY = 172, THERMO_BULB_R = 27;
const THERMO_TUBE_HALF_W = 14;
const THERMO_JUNCTION_Y = THERMO_BULB_CY - Math.sqrt(THERMO_BULB_R*THERMO_BULB_R - THERMO_TUBE_HALF_W*THERMO_TUBE_HALF_W);
const THERMO_FILL_BOTTOM = 165;

let THERMO_MIN_C = -10, THERMO_MAX_C = 40;
let THERMO_STEP_DISP = 10;

function thermoFallbackRange(c){
  if(c==null || isNaN(c)) return {minC:THERMO_MIN_C, maxC:THERMO_MAX_C, stepDisp:10};
  const span = 50;
  const minC = Math.floor((c - span/2)/10)*10;
  return {minC, maxC: minC+span, stepDisp:10};
}

function thermoDynamicDomain(dayMinC, dayMaxC, unit){
  if(dayMinC==null || dayMaxC==null || isNaN(dayMinC) || isNaN(dayMaxC)) return null;
  const toDisp = unit==='F' ? C2F : (c=>c);
  const toC    = unit==='F' ? F2C : (f=>f);
  const step = unit==='F' ? 8 : 5;
  const minDisp = toDisp(dayMinC), maxDisp = toDisp(dayMaxC);
  let lo = step * Math.floor(minDisp/step);
  let hi = step * Math.ceil(maxDisp/step);
  if(minDisp - lo < 0.66*step) lo -= step;
  if(hi  - maxDisp < 0.66*step) hi += step;
  return { minC: toC(lo), maxC: toC(hi), stepDisp: step };
}

let thermoYScale = null;

function thermoY(c){
  const t = Math.max(THERMO_MIN_C, Math.min(THERMO_MAX_C, c));
  if(thermoYScale) return thermoYScale(t);
  const frac = (t - THERMO_MIN_C) / (THERMO_MAX_C - THERMO_MIN_C);
  return THERMO_JUNCTION_Y - frac * (THERMO_JUNCTION_Y - THERMO_TUBE_TOP);
}

let THERMO_DAY_MIN_C = null, THERMO_DAY_MAX_C = null;

function rebuildThermometerSvgD3(unit){
  const svgEl = document.getElementById('thermoSvg');
  if(!svgEl || typeof d3 === 'undefined') return;

  const cs = getComputedStyle(document.documentElement);
  const v = name => cs.getPropertyValue(name).trim();
  const glass = v('--bw-instrument-headspace') || '#222a2e';
  const border = v('--bw-instrument-border') || '#343e43';
  const tickColor = v('--bs-secondary-color') || '#8c9a95';
  const minColor = v('--bw-sky') || '#6FB6DD';
  const maxColor = v('--bw-danger') || '#E36B57';

  const svg = d3.select(svgEl);
  svg.selectAll('*').remove();

  thermoYScale = d3.scaleLinear()
    .domain([THERMO_MIN_C, THERMO_MAX_C])
    .range([THERMO_JUNCTION_Y, THERMO_TUBE_TOP]);

  svg.append('rect')
    .attr('x', 51).attr('y', THERMO_TUBE_TOP)
    .attr('width', 28).attr('height', THERMO_FILL_BOTTOM - THERMO_TUBE_TOP)
    .attr('rx', 14)
    .style('fill', glass).style('stroke', border).style('stroke-width', '2px');

  svg.append('circle')
    .attr('cx', THERMO_BULB_CX).attr('cy', THERMO_BULB_CY).attr('r', THERMO_BULB_R)
    .style('fill', glass).style('stroke', border).style('stroke-width', '2px');

  svg.append('rect').attr('id', 'thermoMercury')
    .attr('x', 52).attr('y', THERMO_FILL_BOTTOM).attr('width', 26).attr('height', 0).attr('rx', 3)
    .attr('fill', 'var(--bw-good)');
  svg.append('circle').attr('id', 'thermoBulb')
    .attr('cx', THERMO_BULB_CX).attr('cy', THERMO_BULB_CY).attr('r', THERMO_BULB_R - 1)
    .attr('fill', 'var(--bw-good)');

  const toDisp = unit==='F' ? C2F : (c=>c);
  const toC    = unit==='F' ? F2C : (f=>f);
  const minDisp = toDisp(THERMO_MIN_C), maxDisp = toDisp(THERMO_MAX_C);
  const step = THERMO_STEP_DISP || 10;
  const tickValuesC = d3.range(Math.round(minDisp), maxDisp + 0.001, step).map(toC);

  const axis = d3.axisRight(thermoYScale)
    .tickValues(tickValuesC)
    .tickSize(8)
    .tickPadding(4)
    .tickFormat(c => Math.round(toDisp(c)) + '°');

  const tAxis = svg.append('g')
    .attr('class', 'y-axis')
    .attr('transform', 'translate(79, 0)')
    .call(axis);

  tAxis.selectAll('.tick text')
    .style('fill', tickColor)
    .style('font-family', "'JetBrains Mono',monospace")
    .style('font-size', '12px');
  tAxis.select('path').style('stroke', 'none').style('fill', 'none');
  tAxis.selectAll('.tick line')
    .style('stroke', tickColor)
    .style('stroke-linecap', 'round')
    .style('stroke-width', '2px');

  if(THERMO_DAY_MIN_C!=null && THERMO_DAY_MAX_C!=null){
    const yMin = thermoYScale(THERMO_DAY_MIN_C), yMax = thermoYScale(THERMO_DAY_MAX_C);
    svg.append('line')
      .attr('x1', 51).attr('x2', 79).attr('y1', yMin).attr('y2', yMin)
      .style('stroke', minColor).style('stroke-width', '1.25px').style('stroke-dasharray', '2,2');
    svg.append('text')
      .attr('x', 65).attr('y', yMin + 11).attr('text-anchor', 'middle')
      .style('font-family', "'JetBrains Mono',monospace").style('font-size', '10px').style('font-weight', '700')
      .style('fill', minColor).text('Min');
    svg.append('line')
      .attr('x1', 51).attr('x2', 79).attr('y1', yMax).attr('y2', yMax)
      .style('stroke', maxColor).style('stroke-width', '1.25px').style('stroke-dasharray', '2,2');
    svg.append('text')
      .attr('x', 65).attr('y', yMax - 6).attr('text-anchor', 'middle')
      .style('font-family', "'JetBrains Mono',monospace").style('font-size', '10px').style('font-weight', '700')
      .style('fill', maxColor).text('Max');
  }

  svg.append('text').attr('id', 'thermoValueText')
    .attr('x', THERMO_BULB_CX).attr('y', THERMO_BULB_CY + 4)
    .attr('text-anchor', 'middle')
    .style('font-family', "'JetBrains Mono',monospace").style('font-weight', '700').style('font-size', '13px')
    .style('fill', '#fff').style('stroke', 'rgba(0,0,0,.6)').style('stroke-width', '3.5px').style('stroke-linejoin', 'round')
    .attr('paint-order', 'stroke')
    .text('--°');
}

function setThermometerValue(c, unit, feelsC){
  if(c==null || isNaN(c)) return;
  const color = tempColor(c);
  const y = thermoY(c);
  const mercury = document.getElementById('thermoMercury');
  const bulb = document.getElementById('thermoBulb');
  if(mercury){
    mercury.setAttribute('y', y.toFixed(1));
    mercury.setAttribute('height', (THERMO_FILL_BOTTOM - y).toFixed(1));
    mercury.setAttribute('fill', color);
  }
  if(bulb) bulb.setAttribute('fill', color);

  const valueTextEl = document.getElementById('thermoValueText');
  if(valueTextEl) valueTextEl.textContent = fmtTemp(c, unit);
  const captionEl = document.getElementById('thermoCaption');
  if(captionEl) captionEl.textContent = feelsC!=null ? `Feels like ${fmtTemp(feelsC, unit)}` : '';
}

// ===================== Rain gauge instrument =====================
const RAIN_TUBE_TOP = 8;
const RAIN_SCALE_TOP = 42;
const RAIN_TUBE_BOTTOM = 182;
let RAIN_MAX_MM = 50;

function rainNiceMax(mm){
  const base = 50;
  if(mm==null || isNaN(mm) || mm <= base*0.8) return base;
  return Math.ceil((mm+10)/10)*10;
}

function rainY(mm){
  const t = Math.max(0, Math.min(RAIN_MAX_MM, mm));
  const frac = t / RAIN_MAX_MM;
  return RAIN_TUBE_BOTTOM - frac * (RAIN_TUBE_BOTTOM - RAIN_SCALE_TOP);
}

function rainTickStops(){
  const stops = [];
  for(let v=0; v<=RAIN_MAX_MM; v+=10) stops.push(v);
  return stops;
}

function rainCylinderSvg(cx, idPrefix, tickColor, tickSide){
  const cs = getComputedStyle(document.documentElement);
  const v = name => cs.getPropertyValue(name).trim();
  const glass = v('--bw-instrument-headspace') || '#222a2e';
  const border = v('--bw-instrument-border') || '#343e43';
  let ticks = '';
  if(tickSide){
    const x1 = tickSide==='left' ? cx-24 : cx+16;
    const x2 = tickSide==='left' ? cx-16 : cx+24;
    ticks = rainTickStops().map(mm=>{
      const y = rainY(mm);
      return `<line x1="${x1}" y1="${y.toFixed(1)}" x2="${x2}" y2="${y.toFixed(1)}" stroke="${tickColor}" stroke-width="2"/>`;
    }).join('');
  }
  return `
    <rect x="${cx-32}" y="${RAIN_TUBE_BOTTOM}" width="64" height="8" rx="2" fill="${glass}" stroke="${border}" stroke-width="2"/>
    <rect x="${cx-16}" y="${RAIN_TUBE_TOP}" width="32" height="${(RAIN_TUBE_BOTTOM-RAIN_TUBE_TOP).toFixed(1)}" rx="4" fill="${glass}" stroke="${border}" stroke-width="2"/>
    ${ticks}
    <rect id="${idPrefix}Fill" x="${cx-8}" y="${RAIN_TUBE_BOTTOM}" width="16" height="0" fill="var(--bw-sky)"/>
    <ellipse id="${idPrefix}Meniscus" cx="${cx}" cy="${RAIN_TUBE_BOTTOM}" rx="10" ry="3" fill="var(--bw-sky)" opacity=".7"/>
    <text id="${idPrefix}ValueText" x="${cx}" y="28" text-anchor="middle" font-size="13" font-weight="700" font-family="'JetBrains Mono',monospace" fill="#fff" stroke="rgba(0,0,0,.6)" stroke-width="3.5" stroke-linejoin="round" paint-order="stroke">--</text>
  `;
}

function buildRainGaugeSvg(unit, showPiezo){
  const cs = getComputedStyle(document.documentElement);
  const v = name => cs.getPropertyValue(name).trim();
  const tickColor = v('--bs-secondary-color') || '#8c9a95';
  const stopsMm = rainTickStops();

  if(!showPiezo){
    const ticks = stopsMm.map(mm=>{
      const y = rainY(mm);
      const label = unit==='in' ? (Math.round(mm2in(mm)*10)/10) : mm;
      return `<g>
        <line x1="81" y1="${y.toFixed(1)}" x2="89" y2="${y.toFixed(1)}" stroke="${tickColor}" stroke-width="2"/>
        <text x="93" y="${(y+4).toFixed(1)}" font-size="11" font-family="'JetBrains Mono',monospace" fill="${tickColor}">${label}</text>
      </g>`;
    }).join('');
    return rainCylinderSvg(65, 'rain', tickColor, null) + ticks;
  }

  const numbers = stopsMm.map(mm=>{
    const y = rainY(mm);
    const label = unit==='in' ? (Math.round(mm2in(mm)*10)/10) : mm;
    return `<text x="110" y="${(y+4).toFixed(1)}" text-anchor="middle" font-size="10" font-family="'JetBrains Mono',monospace" fill="${tickColor}">${label}</text>`;
  }).join('');
  const labelY = RAIN_TUBE_BOTTOM + 8 + 8;
  return `
    ${rainCylinderSvg(50, 'rain', tickColor, 'right')}
    ${numbers}
    ${rainCylinderSvg(170, 'piezo', tickColor, 'left')}
    <text x="50" y="${labelY}" text-anchor="middle" font-size="9" letter-spacing=".05em" font-family="'JetBrains Mono',monospace" fill="${tickColor}">TIPPING</text>
    <text x="170" y="${labelY}" text-anchor="middle" font-size="9" letter-spacing=".05em" font-family="'JetBrains Mono',monospace" fill="${tickColor}">PIEZO</text>
  `;
}

function setRainCylinderValue(idPrefix, mm, unit){
  if(mm==null || isNaN(mm)) return;
  const y = rainY(mm);
  const fill = document.getElementById(idPrefix+'Fill');
  const meniscus = document.getElementById(idPrefix+'Meniscus');
  if(fill){
    fill.setAttribute('y', y.toFixed(1));
    fill.setAttribute('height', (RAIN_TUBE_BOTTOM - y).toFixed(1));
  }
  if(meniscus) meniscus.setAttribute('cy', y.toFixed(1));
  const valueTextEl = document.getElementById(idPrefix+'ValueText');
  if(valueTextEl) valueTextEl.textContent = unit==='in' ? (Math.round(mm2in(mm)*100)/100) : (Math.round(mm*10)/10);
}

function setRainGaugeValue(mm, unit, rateMm, piezoMm, piezoRateMm){
  setRainCylinderValue('rain', mm, unit);
  const dual = !!document.getElementById('piezoFill');
  if(dual && piezoMm!=null) setRainCylinderValue('piezo', piezoMm, unit);

  const captionEl = document.getElementById('rainGaugeCaption');
  if(!captionEl) return;
  if(dual){
    captionEl.textContent = `Rain rate: ${fmtRain(rateMm ?? 0, unit)}/hr · ${fmtRain(piezoRateMm ?? 0, unit)}/hr`;
  } else {
    captionEl.textContent = `Rain rate: ${fmtRain(rateMm ?? 0, unit)}/hr`;
  }
}

function rebuildThermometer(unit){
  rebuildThermometerSvgD3(unit);
}

function rebuildRainGauge(unit, showPiezo){
  const svgEl = document.getElementById('rainGaugeSvg');
  if(!svgEl) return;
  svgEl.setAttribute('viewBox', showPiezo ? '0 0 220 200' : '0 0 130 200');
  svgEl.classList.toggle('dual', showPiezo);
  svgEl.innerHTML = buildRainGaugeSvg(unit, showPiezo);
  const colEl = document.getElementById('rainGaugeCol');
  if(colEl) colEl.classList.toggle('dual', showPiezo);
}

function initInstrumentGauges(){
  const tempUnit = SYSTEMS[currentSystem].temp;
  const rainUnit = SYSTEMS[currentSystem].rain;
  rebuildThermometer(tempUnit);
  rebuildRainGauge(rainUnit, rainGaugeHasPiezo);
  buildWindCompassGaugeEcharts();
  buildCombinedWindGaugeEcharts(SYSTEMS[currentSystem]);
  buildBarometerGaugeEcharts(SYSTEMS[currentSystem]);
}