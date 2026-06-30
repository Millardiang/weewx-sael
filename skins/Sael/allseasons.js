// ===================== reimagine.js =====================
// index.html's main logic. instruments.js and forecast.js (loaded before
// this file) hold the instrument-gauge and forecast-data code that used
// to live here. records.html, charts.html, and climate.html each still
// have their own separate inline <script> blocks.

const metricsRaw = {
  windGustMs: 8.5, tempHiC:23, tempLoC:14, feelsHiC:22, feelsLoC:17,
  rainNextPct:40, rainNextTime:"2pm",
  humidityHi:86, humidityLo:63,
  uv: 7, uvLabel:"High", uvAdvice:"Seek shade during midday hours, cover up and wear sunscreen.",
  visHiKm:29, visLoKm:20, visHiLabel:"Very Good", visLoLabel:"Very Good",
  pressureHpa:1015,
  rainDayMm:2.4,
  lightningCount:0, lightningDistKm:null,
  radiationWm2:380, illuminance:45000,
  co2Ppm:480, co2_24hPpm:460,
  vpdKpa:0.6,
  dewpointC:13, no2:4.5, co:137, o3:78, so2:0.6, nh3:2.4,
  cloudCoverPct:40, cloudBaseM:1200,
};

// ===================== Unit conversion =====================
const C2F = c => c*9/5+32;
const ms2mph = ms => ms*2.23694;
const ms2kmh = ms => ms*3.6;
const ms2kt  = ms => ms*1.94384;
const km2mi  = km => km*0.621371;
const km2nm  = km => km*0.539957;
const hpa2inhg = hpa => hpa*0.0295301;
const hpa2mmhg = hpa => hpa*0.750062;
const hpa2kpa  = hpa => hpa/10;
const mm2in = mm => mm*0.0393701;

function beaufort(ms){
  const scale=[
    [0.5,0,"Calm"],[1.5,1,"Light air"],[3.3,2,"Light breeze"],[5.5,3,"Gentle breeze"],
    [7.9,4,"Moderate breeze"],[10.7,5,"Fresh breeze"],[13.8,6,"Strong breeze"],
    [17.1,7,"Near gale"],[20.7,8,"Gale"],[24.4,9,"Strong gale"],[28.4,10,"Storm"],
    [32.6,11,"Violent storm"],[999,12,"Hurricane"]
  ];
  for(const [max,f,label] of scale){ if(ms<max) return {force:f,label}; }
  return {force:12,label:"Hurricane"};
}

const SYSTEMS = {
  uk:       {temp:'C', wind:'mph', pressure:'hpa', rain:'mm', vis:'mi'},
  us:       {temp:'F', wind:'mph', pressure:'inhg', rain:'in', vis:'mi'},
  metric:   {temp:'C', wind:'kmh', pressure:'hpa', rain:'mm', vis:'km'},
  scandi:   {temp:'C', wind:'ms',  pressure:'hpa', rain:'mm', vis:'km'},
  canada:   {temp:'C', wind:'kmh', pressure:'kpa', rain:'mm', vis:'km'},
  aviation: {temp:'C', wind:'kt',  pressure:'hpa', rain:'mm', vis:'nm'},
  beaufort: {temp:'C', wind:'bf',  pressure:'hpa', rain:'mm', vis:'km'},
};

function fmtTemp(c, unit){ return unit==='F' ? Math.round(C2F(c))+'°F' : Math.round(c)+'°C'; }

// ===================== Temperature color coding =====================
const TEMP_COLOR_STOPS = [
  [-10, [91,127,227]],
  [0,   [79,168,208]],
  [10,  [79,184,155]],
  [18,  [111,184,90]],
  [24,  [201,178,62]],
  [30,  [224,138,61]],
  [36,  [216,90,61]],
  [42,  [184,48,42]],
];
function tempColor(c){
  if(c==null) return null;
  const stops = TEMP_COLOR_STOPS;
  if(c <= stops[0][0]) return `rgb(${stops[0][1].join(',')})`;
  if(c >= stops[stops.length-1][0]) return `rgb(${stops[stops.length-1][1].join(',')})`;
  for(let i=0;i<stops.length-1;i++){
    const [t0,c0] = stops[i], [t1,c1] = stops[i+1];
    if(c>=t0 && c<=t1){
      const f = (c-t0)/(t1-t0);
      const rgb = c0.map((v,idx)=>Math.round(v+(c1[idx]-v)*f));
      return `rgb(${rgb.join(',')})`;
    }
  }
}
function tempSpan(c, unit, extraStyle){
  return `<span style="color:${tempColor(c)};${extraStyle||''}">${fmtTemp(c, unit)}</span>`;
}
function fmtWind(ms, unit){
  switch(unit){
    case 'mph': return Math.round(ms2mph(ms))+' mph';
    case 'kmh': return Math.round(ms2kmh(ms))+' km/h';
    case 'kt':  return Math.round(ms2kt(ms))+' kt';
    case 'ms':  return (Math.round(ms*10)/10)+' m/s';
    case 'bf': { const b=beaufort(ms); return 'F'+b.force+' · '+b.label; }
    default: return Math.round(ms)+' m/s';
  }
}

// ===================== Wind color coding =====================
const WIND_COLOR_STOPS = [
  [0.0,  [125,150,165]],
  [0.5,  [110,168,189]],
  [1.5,  [97,184,178]],
  [3.3,  [99,184,114]],
  [5.5,  [156,182,80]],
  [7.9,  [205,171,62]],
  [10.7, [224,138,61]],
  [13.8, [217,98,61]],
  [17.1, [195,62,48]],
  [20.7, [170,45,60]],
  [24.4, [150,40,90]],
  [28.4, [120,40,120]],
  [32.6, [90,40,140]],
];
function windColor(ms){
  if(ms==null) return null;
  const stops = WIND_COLOR_STOPS;
  if(ms <= stops[0][0]) return `rgb(${stops[0][1].join(',')})`;
  if(ms >= stops[stops.length-1][0]) return `rgb(${stops[stops.length-1][1].join(',')})`;
  for(let i=0;i<stops.length-1;i++){
    const [s0,c0] = stops[i], [s1,c1] = stops[i+1];
    if(ms>=s0 && ms<=s1){
      const f = (ms-s0)/(s1-s0);
      const rgb = c0.map((v,idx)=>Math.round(v+(c1[idx]-v)*f));
      return `rgb(${rgb.join(',')})`;
    }
  }
}
function windSpan(ms, unit, extraStyle){
  return `<span style="color:${windColor(ms)};${extraStyle||''}">${fmtWind(ms, unit)}</span>`;
}

// ===================== Hourly wind-gust direction badge =====================
// Value-only formatter (no unit suffix) for compact display inside the badge.
function fmtWindValueOnly(ms, unit){
  if(ms==null) return '--';
  switch(unit){
    case 'mph': return Math.round(ms2mph(ms));
    case 'kmh': return Math.round(ms2kmh(ms));
    case 'kt':  return Math.round(ms2kt(ms));
    case 'ms':  return Math.round(ms*10)/10;
    case 'bf':  return beaufort(ms).force;
    default:    return Math.round(ms);
  }
}
function windUnitAbbr(unit){
  switch(unit){
    case 'mph': return 'mph';
    case 'kmh': return 'km/h';
    case 'kt':  return 'kt';
    case 'ms':  return 'm/s';
    case 'bf':  return 'Bf';
    default:    return 'm/s';
  }
}
// Wind run is a distance, not a speed — pairs with the page's chosen wind
// unit the way WeeWX's own unit systems pair them (mph<->miles,
// km/h or m/s<->km, knots<->nautical miles).
function windRunUnitLabel(unit){
  switch(unit){
    case 'mph': return 'mi';
    case 'kmh': return 'km';
    case 'ms':  return 'km';
    case 'kt':  return 'nmi';
    default:    return '';
  }
}
// Renders a small circular badge: the ring/arrow rotates to the compass
// bearing the wind is blowing FROM (0deg = arrow points up = wind from the
// north), with the gust value sitting in the centre. Falls back to a plain
// value-only badge when direction data isn't available for that hour.
// Rotates the small wind-direction compass to `deg`, always taking the
// shortest path (so it never visibly spins the long way round when crossing
// the 0/360 boundary) — the CSS transition on .wind-compass-mini-arrow then
// animates that rotation smoothly.
function setWindCompassMiniRotation(deg){
  const el = document.getElementById('windCompassMiniArrow');
  if(!el || deg==null) return;
  const currentMod = ((windCompassMiniRotation % 360) + 360) % 360;
  let delta = deg - currentMod;
  if(delta > 180) delta -= 360;
  if(delta < -180) delta += 360;
  windCompassMiniRotation += delta;
  el.style.transform = `rotate(${windCompassMiniRotation}deg)`;
}

function windDirBadge(dirDeg, gustMs, unit){
  const col = windColor(gustMs) || 'var(--bs-secondary-color)';
  const val = fmtWindValueOnly(gustMs, unit);
  const arrow = dirDeg!=null ? `
      <svg viewBox="0 0 44 44" class="wind-badge-ring" aria-hidden="true">
        <circle cx="22" cy="22" r="19" fill="none" stroke="${col}" stroke-width="2" opacity=".35"/>
        <g transform="rotate(${dirDeg} 22 22)">
          <path d="M22 5 L26.5 16.5 L22 13.5 L17.5 16.5 Z" fill="${col}"/>
        </g>
      </svg>` : '';
  return `<div class="wind-badge" style="--wind-col:${col}" title="${dirDeg!=null ? 'From '+degToCompass(dirDeg) : ''}">
      ${arrow}
      <span class="wind-badge-value">${val}</span>
    </div>`;
}

// ===================== UV Index color coding (official EPA/WHO bands) =====================
function uvColor(uv){
  if(uv==null) return null;
  if(uv <= 2)  return '#299501';
  if(uv <= 5)  return '#F7E400';
  if(uv <= 7)  return '#F85900';
  if(uv <= 10) return '#D8001D';
  return '#6B49C8';
}
function uvLabelOfficial(uv){
  if(uv==null) return '';
  if(uv <= 2) return 'Low';
  if(uv <= 5) return 'Moderate';
  if(uv <= 7) return 'High';
  if(uv <= 10) return 'Very High';
  return 'Extreme';
}
function uvSpan(uv, extraStyle){
  return `<span style="color:${uvColor(uv)};${extraStyle||''}">${uv}</span>`;
}
function fmtPressure(hpa, unit){
  switch(unit){
    case 'inhg': return (Math.round(hpa2inhg(hpa)*100)/100)+' inHg';
    case 'mmhg': return Math.round(hpa2mmhg(hpa))+' mmHg';
    case 'kpa':  return (Math.round(hpa2kpa(hpa)*10)/10)+' kPa';
    default: return Math.round(hpa)+' hPa';
  }
}
function fmtRain(mm, unit){ return unit==='in' ? (Math.round(mm2in(mm)*100)/100)+' in' : (Math.round(mm*10)/10)+' mm'; }
function fmtVis(km, unit){
  switch(unit){
    case 'mi': return (Math.round(km2mi(km)*10)/10)+' mi';
    case 'nm': return (Math.round(km2nm(km)*10)/10)+' nm';
    default: return Math.round(km)+' km';
  }
}

// ===================== Inbound conversions (raw loop units → SI base) =====================
const F2C    = f => (f-32)*5/9;
const mph2ms = mph => mph/2.23694;
const kmh2ms = kmh => kmh/3.6;
const inhg2hpa = inhg => inhg/0.0295301;
const in2mm  = inch => inch/0.0393701;
const mi2km  = mi => mi*1.609344;

// ===================== live.json wiring =====================
const LIVE_JSON_URL = './jsondata/loop.json';
const LIVE_POLL_MS  = 5000;

const FIELD_MAP = {
  temp:         'outTemp',
  feelsLike:    'feelslike',
  humidity:     'outHumidity',
  pressure:     'barometer',
  windSpeed:    'windSpeed',
  windGust:     'windGust',
  windDir:      'windDir',
  windDir10:    'windDir10',
  windCardinal: 'windCardinal',
  maxDailyGust: 'maxdailygust',
  rainRate:     'rainRate',
  rainDay:      'dayRain',
  piezoRain:    'hail',
  piezoRainRate:'hailRate',
  uv:           'UV',
  isDay:        'isDay',
  cloudCover:   'cloudcover',
  cloudBase:    'cloudBase',
  lightningCount: 'lightning_num',
  lightningDist:  'lightning_dist',
  radiation:    'radiation',
  illuminance:  'illuminance',
  co2:          'co2',
  co2_24h:      'co2_24h',
  vpd:          'vpd',
  dewpoint:     'dewpoint',
  no2:          'no2',
  co:           'co',
  o3:           'o3',
  so2:          'so2',
  nh3:          'nh3',
};

let liveUnits = 'us';
function detectUnits(meta){
  const p = (meta && meta.preferred_unit_system || '').toLowerCase();
  return (p==='us' || p==='metric' || p==='metricwx') ? p : 'us';
}
function convTemp(v){ if(v==null) return null; return liveUnits==='us' ? F2C(v) : v; }
function convSpeed(v){
  if(v==null) return null;
  if(liveUnits==='us') return mph2ms(v);
  if(liveUnits==='metric') return kmh2ms(v);
  return v;
}
function convPressure(v){ if(v==null) return null; return liveUnits==='us' ? inhg2hpa(v) : v; }
function convRain(v){ if(v==null) return null; return liveUnits==='us' ? in2mm(v) : v; }

function degToCompass(deg){
  if(deg==null) return '—';
  const dirs=['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];
  return dirs[Math.round(deg/22.5)%16];
}

let liveData = null;
let liveStatus = 'connecting';
let lastGoodAt = null;
let lastLiveDateTime = null;

let lightningLastCount = null;
let lightningActiveUntilMs = 0;
const LIGHTNING_ACTIVE_WINDOW_MS = 30*60*1000;

function noteLightningCount(count){
  if(count == null) return;
  if(lightningLastCount != null && count > lightningLastCount){
    lightningActiveUntilMs = Date.now() + LIGHTNING_ACTIVE_WINDOW_MS;
  }
  lightningLastCount = count;
}
function isLightningActive(){
  return Date.now() < lightningActiveUntilMs;
}
function resetLightningTrackingState(){
  lightningLastCount = null;
  lightningActiveUntilMs = 0;
}

function pingLiveDot(){
  const ripple = document.getElementById('liveStatusRipple');
  if(!ripple) return;
  ripple.classList.remove('ping');
  void ripple.offsetWidth; // force reflow so the animation can restart even if still mid-ripple
  ripple.classList.add('ping');
}

function setLiveStatus(status){
  liveStatus = status;
  const dot = document.getElementById('liveStatusDot');
  const label = document.getElementById('liveStatusLabel');
  if(status==='live'){
    dot.style.background = 'var(--bw-good)';
    dot.title = 'Connected to live.json';
    label.textContent = 'LIVE';
  } else if(status==='stale'){
    dot.style.background = 'var(--bw-accent)';
    dot.title = 'Latest poll failed — holding the most recent real reading';
    label.textContent = 'LIVE (stale)';
  } else if(status==='demo'){
    dot.style.background = 'var(--bw-accent)';
    dot.title = 'live.json unreachable — showing demo data';
    label.textContent = 'DEMO DATA';
  } else {
    dot.style.background = 'var(--bs-secondary-color)';
    dot.title = 'Connecting…';
    label.textContent = 'CONNECTING';
  }
}

async function pollLive(){
  try{
    const res = await fetch(LIVE_JSON_URL, {cache:'no-store'});
    if(!res.ok) throw new Error('HTTP '+res.status);
    const j = await res.json();

    const obs = j.observations || j;
    const get = key => obs[FIELD_MAP[key]];
    liveUnits = detectUnits(j.metadata);

    const feelsRaw = get('feelsLike') ?? get('temp');

    const pollenSpecies = {
      grass: obs.grass_pollen, birch: obs.birch_pollen, alder: obs.alder_pollen,
      mugwort: obs.mugwort_pollen, olive: obs.olive_pollen, ragweed: obs.ragweed_pollen,
    };
    let pollen = null;
    const pollenEntries = Object.entries(pollenSpecies).filter(([,v]) => v!=null);
    if(pollenEntries.length){
      pollenEntries.sort((a,b)=>b[1]-a[1]);
      const [topSpecies, topValue] = pollenEntries[0];
      pollen = {
        level: pollenLevel(topValue),
        species: topSpecies,
        detail: pollenEntries.filter(([,v])=>v>=10)
          .map(([k,v])=>`${k[0].toUpperCase()+k.slice(1)}: ${pollenLevel(v)}`).join(' · ') || 'All species low',
        allSpecies: pollenEntries.map(([k,v]) => ({ name: k[0].toUpperCase()+k.slice(1), value: v, level: pollenLevel(v) })),
      };
    }

    const aqParticles = [
      {name:'PM1',   aqi: obs.pm1_RealAQI_co2},
      {name:'PM2.5', aqi: obs.pm25_RealAQI_co2},
      {name:'PM4',   aqi: obs.pm4_RealAQI_co2},
      {name:'PM10',  aqi: obs.pm10_RealAQI_co2},
    ].filter(p => p.aqi!=null).map(p => ({...p, level: aqiLevel(p.aqi)}));
    const aqiCandidates = aqParticles.map(p=>p.aqi);
    let aq = null;
    if(aqiCandidates.length){
      const worst = Math.max(...aqiCandidates);
      aq = { aqi: worst, level: aqiLevel(worst), particles: aqParticles };
    }

    liveData = {
      dateTime:     j.updated ?? Math.floor(Date.now()/1000),
      tempC:        convTemp(get('temp')),
      feelsLikeC:   convTemp(feelsRaw),
      humidity:     get('humidity'),
      pressureHpa:  convPressure(get('pressure')),
      windMs:       convSpeed(get('windSpeed')),
      gustMs:       convSpeed(get('windGust')),
      windDirDeg:   get('windDir'),
      windDirDeg10: get('windDir10'),
      windCardinal: get('windCardinal') ?? null,
      maxDailyGustMs: convSpeed(get('maxDailyGust')),
      rainRateMm:   convRain(get('rainRate')),
      rainDayMm:    convRain(get('rainDay')),
      piezoRainMm:    convRain(get('piezoRain')),
      piezoRainRateMm: convRain(get('piezoRainRate')),
      uv:           get('uv'),
      isDay:        get('isDay')!=null ? get('isDay')===1 : null,
      cloudCoverPct: get('cloudCover'),
      cloudBaseM:   get('cloudBase'),
      lightningCount: get('lightningCount'),
      lightningDistKm: get('lightningDist'),
      radiationWm2: get('radiation'),
      illuminance:  get('illuminance'),
      co2Ppm:       get('co2'),
      co2_24hPpm:   get('co2_24h'),
      vpdKpa:       get('vpd'),
      dewpointC:    get('dewpoint'),
      no2:          get('no2'),
      co:           get('co'),
      o3:           get('o3'),
      so2:          get('so2'),
      nh3:          get('nh3'),
      pollen, aq,
    };
    noteLightningCount(liveData.lightningCount);
    lastGoodAt = Date.now();
    if(liveData.dateTime !== lastLiveDateTime){
      lastLiveDateTime = liveData.dateTime;
      pingLiveDot();
    }
    setLiveStatus('live');
  }catch(e){
    console.warn('BirchesWX: live data poll failed —', e.message);

    if(liveData){
      setLiveStatus('stale');
    } else {
      setLiveStatus('demo');
    }
  }
  render();
}

// ===================== Alerts wiring (OpenWeatherMap primary, Met Office RSS backup, UKHSA heat alert) =====================
const OWM_JSON_URL       = './jsondata/openweathermap.txt';
const METOFFICE_RSS_URL  = './jsondata/metofficerss.txt';
const HEAT_JSON_URL      = './jsondata/heat.txt';
const ALERTS_POLL_MS     = FORECAST_POLL_MS;

let weatherAlertItems = [];
let heatAlertItem = null;
let expandedAlertKeys = new Set();

const WEEKDAY_ABBR = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
const MONTH_ABBR = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function fmtAlertTime(ms){
  const d = new Date(ms);
  const hh = String(d.getHours()).padStart(2,'0');
  const mm = String(d.getMinutes()).padStart(2,'0');
  return `${WEEKDAY_ABBR[d.getDay()]} ${d.getDate()} ${MONTH_ABBR[d.getMonth()]} ${hh}:${mm}`;
}

function severityFromTitle(title){
  const t = (title||'').toLowerCase();
  if(t.includes('red')) return 'red';
  if(t.includes('amber')) return 'amber';
  if(t.includes('yellow')) return 'yellow';
  return 'amber';
}

function warningTriangleSvg(severity){
  const color = severity==='red' ? 'var(--bw-danger)' : severity==='yellow' ? 'var(--bw-yellow)' : 'var(--bw-accent)';
  return `<svg class="dwarn" viewBox="0 0 24 24" width="16" height="16" title="Warning in effect">
    <title>Warning in effect</title>
    <path d="M10.3 3.86l-8.2 14.2A1.5 1.5 0 0 0 3.4 20.4h17.2a1.5 1.5 0 0 0 1.3-2.34l-8.2-14.2a1.5 1.5 0 0 0-2.4 0z" fill="${color}" stroke="#fff" stroke-width="1"/>
    <path d="M12 9.2v4.3M12 16.6h.01" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/>
  </svg>`;
}

async function fetchOwmAlerts(){
  const res = await fetch(OWM_JSON_URL, {cache:'no-store'});
  if(!res.ok) throw new Error('HTTP '+res.status);
  const j = await res.json();
  const alerts = j.alerts || [];
  return alerts.map(a => ({
    title: a.event,
    detail: a.description,
    severity: severityFromTitle(a.event),
    startMs: a.start ? a.start*1000 : null,
    endMs: a.end ? a.end*1000 : null,
    sender: a.sender_name,
  }));
}

async function fetchMetOfficeAlerts(){
  const res = await fetch(METOFFICE_RSS_URL, {cache:'no-store'});
  if(!res.ok) throw new Error('HTTP '+res.status);
  const xmlText = await res.text();
  const xml = new DOMParser().parseFromString(xmlText, 'text/xml');
  const items = Array.from(xml.querySelectorAll('item'));
  return items.map(item => {
    const title = item.querySelector('title')?.textContent || 'Weather warning';
    return {
      title,
      detail: item.querySelector('description')?.textContent || '',
      severity: severityFromTitle(title),
      startMs: null, endMs: null,
      link: item.querySelector('link')?.textContent || null,
    };
  });
}

async function pollAlerts(){
  try{
    const owmItems = await fetchOwmAlerts();
    if(owmItems.length){
      weatherAlertItems = owmItems.map((a,i) => ({
        key: 'owm|'+i+'|'+a.title,
        title: linkifyUrls(a.title),
        severity: a.severity,
        detailHtml: a.detail ? linkifyUrls(a.detail) : null,
        startMs: a.startMs, endMs: a.endMs,
        sourceLabel: 'OpenWeatherMap',
        link: 'https://openweathermap.org/',
      }));
    } else {
      const moItems = await fetchMetOfficeAlerts();
      weatherAlertItems = moItems.map((a,i) => ({
        key: 'metoffice|'+i+'|'+a.title,
        title: linkifyUrls(a.title),
        severity: a.severity,
        detailHtml: a.detail ? linkifyUrls(a.detail) : null,
        startMs: a.startMs, endMs: a.endMs,
        sourceLabel: 'Met Office RSS',
      }));
    }
  }catch(e){
    console.warn('BirchesWX: alerts poll failed —', e.message);
  }
  applyAlertWarnFlags();
  render();
}

function sanitizeAlertHtml(html){
  if(!html) return '';
  let out = String(html).replace(/<(script|style)[^>]*>[\s\S]*?<\/\1>/gi, '');
  out = out.replace(/\s(on\w+)\s*=\s*"[^"]*"/gi, '').replace(/\s(on\w+)\s*=\s*'[^']*'/gi, '');
  out = out.replace(/javascript:/gi, '');
  const allowed = ['p','ul','ol','li','strong','em','b','i','br','a'];
  out = out.replace(/<\/?([a-zA-Z0-9]+)([^>]*)>/g, (match, tag, attrs) => {
    const t = tag.toLowerCase();
    if(!allowed.includes(t)) return '';
    if(match.startsWith('</')) return `</${t}>`;
    if(t==='a'){
      const hrefMatch = attrs.match(/href\s*=\s*"([^"]*)"/i) || attrs.match(/href\s*=\s*'([^']*)'/i);
      const href = hrefMatch ? hrefMatch[1] : '#';
      if(/^javascript:/i.test(href)) return '<a>';
      return `<a href="${href}" target="_blank" rel="noopener noreferrer">`;
    }
    return `<${t}>`;
  });
  return out;
}

async function fetchHeatAlert(){
  const res = await fetch(HEAT_JSON_URL, {cache:'no-store'});
  if(!res.ok) throw new Error('HTTP '+res.status);
  const j = await res.json();
  const status = (j.status || '').trim();
  if(!status || status.toLowerCase()==='green') return null;
  return {
    key: 'heat|'+(j.geography_code||'')+'|'+(j.period_start||''),
    title: escapeHtml(`${status} Heat-Health Alert — ${j.geography_name || 'your area'}`),
    severity: severityFromTitle(status),
    detailHtml: sanitizeAlertHtml(j.text),
    startMs: j.period_start ? new Date(j.period_start).getTime() : null,
    endMs: j.period_end ? new Date(j.period_end).getTime() : null,
    sourceLabel: 'UKHSA Heat-Health Alert',
    link: 'https://ukhsa-dashboard.data.gov.uk/weather-health-alerts/heat',
  };
}

async function pollHeatAlert(){
  try{
    heatAlertItem = await fetchHeatAlert();
  }catch(e){
    console.warn('BirchesWX: heat alert poll failed —', e.message);
  }
  applyAlertWarnFlags();
  render();
}

function combinedAlerts(){
  return [...weatherAlertItems, ...(heatAlertItem ? [heatAlertItem] : [])];
}

const SEVERITY_RANK = { red: 3, amber: 2, yellow: 1 };
function applyAlertWarnFlags(){
  if(!forecastData) return;
  const items = combinedAlerts();
  forecastData.days.forEach((day, i) => {
    if(!items.length){ day.warnSeverity = null; return; }
    const dayStart = new Date(day.dateStr+'T00:00:00').getTime();
    const dayEnd = dayStart + 24*60*60*1000;
    const overlapping = items.filter(a => (a.startMs!=null && a.endMs!=null) ? (a.startMs < dayEnd && a.endMs > dayStart) : (i === 0));
    day.warnSeverity = overlapping.reduce((worst, a) => {
      const s = a.severity || 'amber';
      return (!worst || SEVERITY_RANK[s] > SEVERITY_RANK[worst]) ? s : worst;
    }, null);
  });
}

// ===================== METAR visibility wiring =====================
const METAR_JSON_URL = './jsondata/me.txt';
const METAR_POLL_MS = FORECAST_POLL_MS;

let metarData = null;

function parseMetarVisib(v){
  if(v==null) return null;
  let s = String(v).trim();
  if(s.startsWith('M')) s = s.slice(1);
  s = s.replace('+', '');
  if(s.includes(' ')){
    const [whole, frac] = s.split(' ');
    const [n, d] = frac.split('/').map(Number);
    return Number(whole) + (d ? n/d : 0);
  }
  if(s.includes('/')){
    const [n, d] = s.split('/').map(Number);
    return d ? n/d : null;
  }
  const num = parseFloat(s);
  return isNaN(num) ? null : num;
}

async function pollMetar(){
  try{
    const res = await fetch(METAR_JSON_URL, {cache:'no-store'});
    if(!res.ok) throw new Error('HTTP '+res.status);
    const arr = await res.json();
    const ob = Array.isArray(arr) ? arr[0] : arr;
    const miles = parseMetarVisib(ob?.visib);
    metarData = miles!=null ? { visKm: mi2km(miles), raw: ob.visib, station: ob.icaoId, obsTime: ob.obsTime } : null;
  }catch(e){
    console.warn('BirchesWX: METAR poll failed —', e.message);
  }
  render();
}

// ===================== Hero panel: sunrise/sunset/moonrise/moonset =====================
const HERO_ASTRO_JSON_URL = './jsondata/astronomical.json';
const HERO_ASTRO_POLL_MS = 60*60*1000;

async function pollHeroAstro(){
  try{
    const res = await fetch(HERO_ASTRO_JSON_URL + '?_=' + Date.now(), {cache:'no-store'});
    if(!res.ok) throw new Error('HTTP '+res.status);
    renderHeroSunTimes(await res.json());
  }catch(e){
    console.warn('BirchesWX: hero sun/moon times poll failed —', e.message);
  }
}
function renderHeroSunTimes(d){
  const sun = d?.sun || {}, moon = d?.moon || {};
  const setText = (id, val) => { const el = document.getElementById(id); if(el) el.textContent = val || '--:--'; };
  setText('heroSunRise', sun.rise);
  setText('heroSunSet', sun.set);
  setText('heroMoonRise', moon.rise);
  setText('heroMoonSet', moon.set);
}

// ===================== Icon helper (Meteocons — filled style) =====================
const METEOCONS_BASE = 'https://cdn.meteocons.com/3.0.0-next.10/svg/fill/';
const ICON_MAP = {
  sun:'clear-day', moon:'clear-night',
  mostlyClear:'mostly-clear-day', mostlyClearNight:'mostly-clear-night',
  partly:'partly-cloudy-day', partlyNight:'partly-cloudy-night',
  cloud:'cloudy', overcast:'overcast-day', overcastNight:'overcast-night', overcastFull:'overcast',
  rain:'rain', heavyRain:'extreme-day-rain', heavyRainNight:'extreme-night-rain',
  drizzle:'drizzle', snow:'snow', sleet:'sleet', hail:'hail',
  thunder:'thunderstorms-day', thunderNight:'thunderstorms-night',
  fog:'fog-day', fogNight:'fog-night', wind:'wind'
};
function iconUrl(name){ return METEOCONS_BASE + (ICON_MAP[name] || ICON_MAP.partly) + '.svg'; }
function iconImg(name, cls, label){
  return `<img class="${cls}" src="${iconUrl(name)}" alt="${label||name}" loading="lazy">`;
}

const THERMOMETER_OUTLINE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 512 512"><defs><symbol id="bw-therm-b" viewBox="0 0 72 168"><circle cx="36" cy="132" r="36" fill="#ef4444"/><path fill="none" stroke="#ef4444" stroke-linecap="round" stroke-miterlimit="10" stroke-width="24" d="M36 12v120"/></symbol><symbol id="bw-therm-c" viewBox="0 0 118 278"><path fill="none" stroke="#cbd5e1" stroke-linecap="round" stroke-linejoin="round" stroke-width="6" d="M115 218.2c0 31.4-25 56.8-56 56.8S3 249.6 3 218.2a57 57 0 0124-46.6V35.5a32 32 0 1164 0v136a57 57 0 0124 46.7ZM63 83h28M63 51h28m-28 64h28"/></symbol><symbol id="bw-therm-a" viewBox="0 0 118 278"><use xlink:href="#bw-therm-b" width="72" height="168" transform="translate(23 87)"/><use xlink:href="#bw-therm-c" width="118" height="278"/></symbol></defs><use xlink:href="#bw-therm-a" width="118" height="278" transform="translate(197 117)"/></svg>';
function thermometerOutlineIcon(cls){
  return `<span class="${cls||''}" style="display:inline-block;">${THERMOMETER_OUTLINE_SVG}</span>`;
}

const RAINDROP_OUTLINE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 512 512"><defs><symbol id="bw-rain-a" viewBox="0 0 175 260.9"><path fill="none" stroke="#6FB6DD" stroke-miterlimit="10" stroke-width="15" d="M87.5 13.4c-48.7 72-80 117-80 160.7s35.8 79.3 80 79.3 80-35.5 80-79.3-31.3-88.8-80-160.7Z"/></symbol></defs><use xlink:href="#bw-rain-a" width="175" height="260.9" transform="translate(168.5 122.62)"/></svg>';
function raindropOutlineIcon(cls){
  return `<span class="${cls||''}" style="display:inline-block;">${RAINDROP_OUTLINE_SVG}</span>`;
}

const WIND_OUTLINE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 512 512"><defs><symbol id="bw-wind-a" viewBox="0 0 342 234"><path fill="none" stroke="#e2e8f0" stroke-linecap="round" stroke-miterlimit="10" stroke-width="18" d="M264.2 21.3A40 40 0 11293 89H9m139.2 123.7A40 40 0 10177 145H9"/></symbol></defs><use xlink:href="#bw-wind-a" width="342" height="234" transform="translate(85 139)"/></svg>';
function windOutlineIcon(cls){
  return `<span class="${cls||''}" style="display:inline-block;">${WIND_OUTLINE_SVG}</span>`;
}

const BAROMETER_OUTLINE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><circle cx="256" cy="256" r="144" fill="none" stroke="#e2e8f0" stroke-miterlimit="10" stroke-width="12"/><path fill="none" stroke="#e2e8f0" stroke-linecap="round" stroke-linejoin="round" stroke-width="6" d="M256 200v-48m108 104h-48m-116 0h-48m180-68-24 24m-104 0-24-24m128 112 24 24m-152 0 24-24"/><circle cx="256" cy="256" r="24" fill="#E8826F"/><path fill="none" stroke="#E8826F" stroke-linecap="round" stroke-miterlimit="10" stroke-width="12" d="M256 284V164"/></svg>';
function barometerOutlineIcon(cls){
  return `<span class="${cls||''}" style="display:inline-block;">${BAROMETER_OUTLINE_SVG}</span>`;
}

// ===================== Live-condition hero icon/text =====================
const MPH_TO_MS = 0.44704;
const WIND_RAIN_COMBO_IMAGE_MS = 15 * MPH_TO_MS;
const WINDY_MS = 7.5 * MPH_TO_MS;

function deriveLiveIcon(ld, isDay){
  const rainRate = ld.rainRateMm ?? 0;
  const windAvg = ld.windMs ?? 0;
  const tempC = ld.tempC, dewC = ld.dewpointC;
  const tdDiff = (tempC!=null && dewC!=null) ? (tempC - dewC) : null;
  const cc = ld.cloudCoverPct;
  const isSnow = rainRate > 0 && tempC != null && tempC <= 0;

  if(rainRate > 0 && windAvg > WIND_RAIN_COMBO_IMAGE_MS) return isSnow ? 'snow' : 'rain';
  if(rainRate > 10) return isSnow ? 'snow' : (isDay ? 'heavyRain' : 'heavyRainNight');
  if(rainRate > 0) return isSnow ? 'snow' : 'rain';
  if(tdDiff!=null && tdDiff < 0.5 && tempC!=null && tempC > 5) return isDay ? 'fog' : 'fogNight';
  if(tdDiff!=null && tdDiff < 0.8 && tempC!=null && tempC > 5) return isDay ? 'fog' : 'fogNight';
  if(windAvg >= WINDY_MS) return 'wind';
  if(cc == null) return isDay ? 'sun' : 'moon';
  if(cc < 7 && cc > 0) return isDay ? 'sun' : 'moon';
  if(cc < 32) return isDay ? 'mostlyClear' : 'mostlyClearNight';
  if(cc < 70) return isDay ? 'partly' : 'partlyNight';
  if(cc < 95) return isDay ? 'overcast' : 'overcastNight';
  return 'overcastFull';
}

function deriveLiveSummaryText(ld){
  const rainRate = ld.rainRateMm ?? 0;
  const windAvg = ld.windMs ?? 0;
  const tempC = ld.tempC, dewC = ld.dewpointC;
  const tdDiff = (tempC!=null && dewC!=null) ? (tempC - dewC) : null;
  const cc = ld.cloudCoverPct;
  const isSnow = rainRate > 0 && tempC != null && tempC <= 0;
  const isDay = ld.isDay != null ? ld.isDay : (new Date().getHours()>=6 && new Date().getHours()<20);

  if(rainRate > 0 && windAvg > WINDY_MS) return isSnow ? 'Snow Showers Windy Conditions' : 'Rain Showers Windy Conditions';
  if(rainRate >= 20) return isSnow ? 'Heavy Snow' : 'Flooding Possible';
  if(rainRate >= 10) return isSnow ? 'Heavy Snow' : 'Heavy Rain';
  if(rainRate >= 5)  return isSnow ? 'Moderate Snow' : 'Moderate Rain';
  if(rainRate >= 1)  return isSnow ? 'Light Snow' : 'Steady Rain';
  if(rainRate > 0)   return isSnow ? 'Light Snow' : 'Light Rain';
  if(tdDiff!=null && tdDiff < 0.5 && tempC!=null && tempC > 5) return 'Misty Conditions';
  if(tdDiff!=null && tdDiff < 0.8 && tempC!=null && tempC > 5) return 'Misty Hazy Conditions';
  if(windAvg >= 40*MPH_TO_MS) return 'Strong Wind Conditions';
  if(windAvg >= 30*MPH_TO_MS) return 'Very Windy Conditions';
  if(windAvg >= 22*MPH_TO_MS) return 'Moderate Wind Conditions';
  if(windAvg >= WINDY_MS)     return 'Breezy Conditions';
  if(cc == null) return isDay ? 'Clear sky' : 'Clear night';
  if(cc < 7 && cc > 0) return isDay ? 'Sunny' : 'Clear Sky';
  if(cc < 32) return isDay ? 'Mostly Sunny Conditions' : 'Mostly Clear Conditions';
  if(cc < 70) return 'Partly Cloudy Conditions';
  if(cc < 95) return 'Mostly Cloudy Conditions';
  return 'Overcast Conditions';
}

function deriveLiveCondition(ld){
  if(!ld) return null;
  const isDay = ld.isDay != null ? ld.isDay : (new Date().getHours()>=6 && new Date().getHours()<20);
  if(isLightningActive()){
    return { icon: isDay?'thunder':'thunderNight', text: 'Thunderstorm' };
  }
  return { icon: deriveLiveIcon(ld, isDay), text: deriveLiveSummaryText(ld) };
}

// ===================== Pollen / air-quality banding =====================
function pollenLevel(count){
  if(count >= 50) return 'Very High';
  if(count >= 30) return 'High';
  if(count >= 10) return 'Moderate';
  return 'Low';
}
function pollenDotClass(level){
  return level==='Very High' || level==='High' ? 'level-high' : level==='Moderate' ? 'level-mod' : 'level-low';
}
function aqDotClass(level){
  return level==='High' ? 'level-high' : level==='Moderate' ? 'level-mod' : 'level-low';
}
function aqiLevel(aqi){
  if(aqi > 100) return 'High';
  if(aqi > 50) return 'Moderate';
  return 'Low';
}

// ===================== Render =====================
let currentSystem = 'uk';
let activeDay = 0;
let windCompassMiniRotation = 0; // unbounded (not mod 360) so the arrow always takes the shortest path

function visLabel(km){
  if(km < 1) return 'Very Poor';
  if(km < 4) return 'Poor';
  if(km < 10) return 'Moderate';
  if(km < 20) return 'Good';
  if(km < 40) return 'Very Good';
  return 'Excellent';
}

function escapeHtml(s){
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function linkifyUrls(s){
  return escapeHtml(s).replace(/https?:\/\/[^\s<>"']+/g, url =>
    `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`
  );
}

// ===================== Today's Observations: enriched cards =====================
const TODAY_OBS_JSON_URL = './jsondata/archive.json';
const TODAY_OBS_POLL_MS = 60*1000;
let todayObsArchive = null;

async function fetchTodayObsArchive(){
  const res = await fetch(TODAY_OBS_JSON_URL, {cache:'no-store'});
  if(!res.ok) throw new Error('HTTP '+res.status);
  const json = await res.json();
  if(!json || typeof json !== 'object') throw new Error('Unexpected archive.json shape — not an object');
  return json;
}
async function pollTodayObs(){
  try{
    todayObsArchive = await fetchTodayObsArchive();
  }catch(e){
    console.warn('BirchesWX: today-observations (archive.json) poll failed —', e.message);
  }
  render();
}

function trendFromDelta(delta, deadband){
  if(delta==null) return 'steady';
  if(delta > deadband) return 'rising';
  if(delta < -deadband) return 'falling';
  return 'steady';
}
function baromTrendFromCode(code){
  if(code==null) return 'steady';
  if(code > 0) return 'rising';
  if(code < 0) return 'falling';
  return 'steady';
}
function trendArrowHtml(trend, labels){
  const L = labels || {rising:'Rising', falling:'Falling', steady:'Steady'};
  const arrow = trend==='rising' ? '▲' : trend==='falling' ? '▼' : '→';
  return `<span class="obs-trend-${trend}">${arrow} ${L[trend]}</span>`;
}
function fmtHourTime(ts){
  return ts==null ? '' : new Date(ts).toLocaleTimeString('en-GB', {hour:'numeric', minute:'2-digit'});
}
function tempSpanOrDash(c, unit){ return c==null ? '--' : tempSpan(c, unit); }
function fmtRainOrDash(mm, unit){ return mm==null ? '--' : fmtRain(mm, unit); }
function fmtPressureOrDash(hpa, unit){ return hpa==null ? '--' : fmtPressure(hpa, unit); }
function fmtRawNumber(v, decimals){ return v==null ? '--' : v.toFixed(decimals==null?1:decimals); }

function render(){
  applyTheme();
  const sys = SYSTEMS[currentSystem];
  const days = getDays();
  const hrs = getHourly(activeDay);

  const warnEl = document.getElementById('warningContainer');
  const allAlerts = combinedAlerts();
  if(allAlerts.length){
    warnEl.innerHTML = `<div class="d-flex flex-column gap-1">${allAlerts.map(a => {
      const validText = (a.startMs!=null && a.endMs!=null) ? `From ${fmtAlertTime(a.startMs)} to ${fmtAlertTime(a.endMs)}.` : null;
      const expanded = expandedAlertKeys.has(a.key);
      return `
        <div class="warning-banner warning-banner--${a.severity || 'amber'} p-2">
          <div class="d-flex align-items-start gap-2 flex-wrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--alert-color)" stroke-width="2" style="flex-shrink:0;margin-top:1px;"><path d="M12 9v4m0 4h.01M10.3 3.86l-8.2 14.2A1.5 1.5 0 0 0 3.4 20.4h17.2a1.5 1.5 0 0 0 1.3-2.34l-8.2-14.2a1.5 1.5 0 0 0-2.4 0z"/></svg>
            <div class="flex-grow-1">
              <span><strong>${a.title}.</strong>${validText ? ' '+validText : ''}</span>
            </div>
            ${a.detailHtml ? `<button class="btn btn-sm btn-outline-secondary alert-more-btn" data-alert-key="${a.key}">${expanded ? 'Less' : 'More'}</button>` : ''}
            ${a.link
              ? `<a href="${a.link}" target="_blank" rel="noopener noreferrer" class="font-mono small">Source: ${a.sourceLabel}</a>`
              : `<span class="font-mono small" style="color:var(--bs-secondary-color);">Source: ${a.sourceLabel}</span>`}
          </div>
          ${a.detailHtml ? `<div class="small mt-2" style="color:var(--bs-body-color);display:${expanded?'block':'none'};">${a.detailHtml}</div>` : ''}
        </div>`;
    }).join('')}</div>`;

    warnEl.querySelectorAll('.alert-more-btn').forEach(btn=>{
      btn.addEventListener('click', ()=>{
        const k = btn.dataset.alertKey;
        if(expandedAlertKeys.has(k)) expandedAlertKeys.delete(k); else expandedAlertKeys.add(k);
        render();
      });
    });
  } else {
    warnEl.innerHTML = '';
  }

  const heroTempC   = liveData?.tempC      ?? forecastData?.current?.tempC ?? 20;
  const heroFeelsC  = liveData?.feelsLikeC ?? 19;
  const heroGustMs  = liveData?.gustMs     ?? forecastData?.current?.gustMs ?? 8.5;
  const heroWindMs  = liveData?.windMs     ?? forecastData?.current?.windMs ?? heroGustMs;
  const heroDir     = liveData?.windCardinal ?? (liveData?.windDirDeg != null ? degToCompass(liveData.windDirDeg) : 'SW');
  const heroHumid   = liveData?.humidity   ?? 70;
  const heroPressHpa= liveData?.pressureHpa?? 1015;

  const stationLocation = todayObsArchive?.meta?.station_location;
  if(stationLocation){
    document.getElementById('pageLocationTitle').textContent = stationLocation;
    document.title = `${stationLocation}`;
  }

  document.getElementById('heroTemp').textContent = fmtTemp(heroTempC, sys.temp);
  document.getElementById('heroTemp').style.color = tempColor(heroTempC);
  document.getElementById('feelsLike').textContent = fmtTemp(heroFeelsC, sys.temp);
  document.getElementById('feelsLike').style.color = tempColor(heroFeelsC);
  const thermoDayMinC = todayObsArchive?.temp?.day_min;
  const thermoDayMaxC = todayObsArchive?.temp?.day_max;
  const dynDomain = thermoDynamicDomain(thermoDayMinC, thermoDayMaxC, sys.temp);
  const thermoRange = dynDomain
    ? { minC: dynDomain.minC, maxC: dynDomain.maxC, stepDisp: dynDomain.stepDisp }
    : thermoFallbackRange(heroTempC);
  if(thermoRange.minC !== THERMO_MIN_C || thermoRange.maxC !== THERMO_MAX_C || thermoRange.stepDisp !== THERMO_STEP_DISP
     || thermoDayMinC !== THERMO_DAY_MIN_C || thermoDayMaxC !== THERMO_DAY_MAX_C){
    THERMO_MIN_C = thermoRange.minC; THERMO_MAX_C = thermoRange.maxC; THERMO_STEP_DISP = thermoRange.stepDisp;
    THERMO_DAY_MIN_C = thermoDayMinC ?? null; THERMO_DAY_MAX_C = thermoDayMaxC ?? null;
    rebuildThermometer(sys.temp);
  }
  setThermometerValue(heroTempC, sys.temp, heroFeelsC);
  document.getElementById('gustHero').textContent = fmtWind(heroGustMs, sys.wind);
  document.getElementById('gustHero').style.color = windColor(heroGustMs);
  document.getElementById('gustDirHero').textContent = heroDir;
  document.getElementById('humidityHero').textContent = Math.round(heroHumid)+'%';
  document.getElementById('pressureHero').textContent = fmtPressure(heroPressHpa, sys.pressure);
  const heroAirDensity = todayObsArchive?.air_density?.current;
  document.getElementById('ccAirDensity').textContent = heroAirDensity!=null ? fmtRawNumber(heroAirDensity, 2)+' kg/m³' : '--';

  const heroRainDayMm = liveData?.rainDayMm ?? metricsRaw.rainDayMm;
  const heroRainRateMm = liveData?.rainRateMm ?? 0;
  const piezoPresent = liveData?.piezoRainMm != null;
  const candidateMax = Math.max(rainNiceMax(heroRainDayMm), piezoPresent ? rainNiceMax(liveData?.piezoRainMm) : 0);
  if(piezoPresent !== rainGaugeHasPiezo || candidateMax !== RAIN_MAX_MM){
    rainGaugeHasPiezo = piezoPresent;
    RAIN_MAX_MM = candidateMax;
    rebuildRainGauge(sys.rain, rainGaugeHasPiezo);
  }
  setRainGaugeValue(heroRainDayMm, sys.rain, heroRainRateMm, liveData?.piezoRainMm, liveData?.piezoRainRateMm);

  const heroDirDeg = liveData?.windDirDeg;
  const heroDirDeg10 = liveData?.windDirDeg10;

  lastWindDirDeg = heroDirDeg; lastWindDirDeg10 = heroDirDeg10;
  setWindCompassGaugeEchartsValue(heroDirDeg, heroDirDeg10);
  setWindCompassMiniRotation(heroDirDeg);
  document.getElementById('ccWindDirDeg').textContent = heroDirDeg!=null ? Math.round(heroDirDeg)+'°' : '';
  document.getElementById('ccGustDirDeg').textContent = heroDirDeg!=null ? Math.round(heroDirDeg)+'°' : '';

  lastWindSpeedMs = heroWindMs;
  lastWindGustMs = heroGustMs;
  setCombinedWindGaugeValue(heroWindMs, heroGustMs, sys);

  {
    const baroMinHpa = todayObsArchive?.barom?.day_min ?? null;
    const baroMaxHpa = todayObsArchive?.barom?.day_max ?? null;
    lastBaroHpa = heroPressHpa; lastBaroMinHpa = baroMinHpa; lastBaroMaxHpa = baroMaxHpa;
    setBarometerGaugeEchartsValue(heroPressHpa, baroMinHpa, baroMaxHpa, sys);
  }

  const cur = forecastData?.current;
  const liveCond = deriveLiveCondition(liveData);
  const heroIconKey = liveCond ? liveCond.icon : (cur ? wmoToIconKey(cur.weatherCode, cur.isDay) : days[activeDay].icon);
  const heroCondText = liveCond ? liveCond.text : (cur ? wmoText(cur.weatherCode) : days[activeDay].cond);
  document.getElementById('heroIcon').src = iconUrl(heroIconKey);
  document.getElementById('heroIcon').alt = heroCondText;
  document.getElementById('heroCond').textContent = `${heroCondText}, ${beaufort(heroGustMs).label.toLowerCase()} from the ${heroDir}`;

  const nextRainMm = cur ? cur.precipMm : 0;
  document.getElementById('rainNextRow').firstChild.textContent = nextRainMm > 0 ? 'Raining now ' : 'Rain next hour ';
  document.getElementById('rainNextHero').textContent = nextRainMm > 0 ? fmtRain(nextRainMm, sys.rain) : (days[activeDay].rainProbPct!=null ? days[activeDay].rainProbPct+'%' : '—');

  const pollenPillEl = document.getElementById('pollenPillHero');
  pollenPillEl.textContent = liveData?.pollen ? `🌿 Pollen: ${liveData.pollen.level}` : '🌿 Pollen: —';

  // ---- Current-conditions card grid: High/Low, Dew Point, Rain total, Wind speed, AQI ----
  const heroHighC = thermoDayMaxC ?? metricsRaw.tempHiC;
  const heroLowC  = thermoDayMinC ?? metricsRaw.tempLoC;
  document.getElementById('ccHigh').textContent = fmtTemp(heroHighC, sys.temp);
  document.getElementById('ccLow').textContent  = fmtTemp(heroLowC, sys.temp);

  const heroDewC = liveData?.dewpointC ?? metricsRaw.dewpointC;
  document.getElementById('ccDewPoint').textContent = fmtTemp(heroDewC, sys.temp);
  document.getElementById('ccEt').textContent = fmtRainOrDash(todayObsArchive?.et?.day, sys.rain);
  const heroVpdKpa = liveData?.vpdKpa ?? metricsRaw.vpdKpa;
  document.getElementById('ccVpd').textContent = heroVpdKpa!=null ? heroVpdKpa.toFixed(2)+' kPa' : '--';

  document.getElementById('ccRainTotal').textContent = fmtRain(heroRainDayMm, sys.rain);
  document.getElementById('ccRainRate').textContent = fmtRain(heroRainRateMm, sys.rain)+'/h';
  document.getElementById('ccWindSpeed').textContent = fmtWind(heroWindMs, sys.wind);

  const aqiPillEl = document.getElementById('aqiPillHero');
  if(liveData?.aq){
    const cls = aqDotClass(liveData.aq.level);
    aqiPillEl.style.display = 'inline-flex';
    aqiPillEl.className = `aqi-pill aqi-pill--${cls==='level-low'?'low':cls==='level-mod'?'mod':'high'}`;
    aqiPillEl.textContent = `AQI: ${Math.round(liveData.aq.aqi)} (${liveData.aq.level})`;
  } else {
    aqiPillEl.style.display = 'none';
  }

  const heroUv = liveData?.uv ?? metricsRaw.uv;
  const uvPillEl = document.getElementById('uvPillHero');
  if(heroUv!=null){
    const uvLabel = uvLabelOfficial(heroUv);
    const tier = uvLabel==='Low' ? 'low' : uvLabel==='Moderate' ? 'mod' : 'high';
    uvPillEl.style.display = 'inline-flex';
    uvPillEl.className = `uv-pill uv-pill--${tier}`;
    uvPillEl.textContent = `UV: ${Math.round(heroUv)} (${uvLabel})`;
  } else {
    uvPillEl.style.display = 'none';
  }

  const strip = document.getElementById('dayStrip');
  strip.innerHTML = days.map((d,i)=>`
    <div class="day-card card ${i===activeDay?'active':''}" data-i="${i}">
      ${d.warnSeverity ? warningTriangleSvg(d.warnSeverity) : ''}
      <div class="dname">${d.name}</div>
      ${iconImg(d.icon,'dicon',d.cond)}
      <div class="dcond">${d.cond}</div>
      <div class="dtemps">${tempSpan(d.hi,sys.temp)} <span class="lo">${tempSpan(d.lo,sys.temp)}</span></div>
    </div>
  `).join('');
  strip.querySelectorAll('.day-card').forEach(el=>{
    el.addEventListener('click', ()=>{ activeDay=parseInt(el.dataset.i); render(); });
  });

  const grid = document.getElementById('metricGrid');
  const gustForCard = liveData?.gustMs ?? metricsRaw.windGustMs;
  const uvForCard = liveData?.uv ?? metricsRaw.uv;
  const feelsForCard = liveData?.feelsLikeC ?? metricsRaw.feelsHiC;
  const humidityForCard = liveData?.humidity ?? metricsRaw.humidityHi;
  const dewpointForCard = liveData?.dewpointC ?? metricsRaw.dewpointC;
  const pressureForCard = liveData?.pressureHpa ?? metricsRaw.pressureHpa;
  const rainRateForCard = liveData?.rainRateMm ?? 0;

  const cloudCoverForCard = liveData?.cloudCoverPct ?? metricsRaw.cloudCoverPct;
  const cloudBaseForCard = liveData?.cloudBaseM ?? metricsRaw.cloudBaseM;
  const fmtCloudBase = (m) => sys.vis==='nm' ? Math.round(m*3.28084).toLocaleString()+' ft' : Math.round(m)+' m';

  const nowHour = new Date().getHours();
  const nearestHourEntry = hrs.reduce((best, h) => (best==null || Math.abs(h.h-nowHour) < Math.abs(best.h-nowHour)) ? h : best, null);
  const visKmForCard = metarData?.visKm ?? nearestHourEntry?.visKm ?? metricsRaw.visHiKm;

  const mc = (inner)=>`<div class="col-6 col-md-3"><div class="card metric-card p-3 h-100">${inner}</div></div>`;

  const tArc = todayObsArchive?.temp, dArc = todayObsArchive?.dew, hArc = todayObsArchive?.humid;
  const tempTrend  = trendFromDelta(tArc?.outside_trend, 0.3);
  const dewTrend   = trendFromDelta(dArc?.trend,          0.3);
  const humidTrend = trendFromDelta(hArc?.trend,          2);
  const currentTempC = liveData?.tempC ?? metricsRaw.tempHiC;
  const temperatureFeatureCard = `<div class="col-12"><div class="card metric-card metric-card--feature p-3 p-md-4 h-100">
      <div class="d-flex align-items-center gap-2 mb-2">
        ${thermometerOutlineIcon('feature-icon')}
        <div class="feature-title">Temperature</div>
      </div>
      <div class="row g-3 g-md-0">
        <div class="col-12 col-md-4 obs-subcol">
          <div class="obs-subcol-title">Temperature</div>
          <div class="obs-stat-row"><span class="obs-stat-label">High</span><span class="obs-stat-value">${tempSpanOrDash(tArc?.day_max, sys.temp)}<span class="obs-stat-time">${fmtHourTime(tArc?.day_maxtime)}</span></span></div>
          <div class="obs-stat-row"><span class="obs-stat-label">Low</span><span class="obs-stat-value">${tempSpanOrDash(tArc?.day_min, sys.temp)}<span class="obs-stat-time">${fmtHourTime(tArc?.day_mintime)}</span></span></div>
          <div class="obs-stat-row"><span class="obs-stat-label">Average</span><span class="obs-stat-value">${tempSpanOrDash(tArc?.day_avg, sys.temp)}</span></div>
          <div class="obs-stat-row"><span class="obs-stat-label">Trend</span><span class="obs-stat-value">${trendArrowHtml(tempTrend)}</span></div>
        </div>
        <div class="col-12 col-md-4 obs-subcol">
          <div class="obs-subcol-title">Dew Point</div>
          <div class="obs-stat-row"><span class="obs-stat-label">High</span><span class="obs-stat-value">${tempSpanOrDash(dArc?.day_max, sys.temp)}<span class="obs-stat-time">${fmtHourTime(dArc?.day_maxtime)}</span></span></div>
          <div class="obs-stat-row"><span class="obs-stat-label">Low</span><span class="obs-stat-value">${tempSpanOrDash(dArc?.day_min, sys.temp)}<span class="obs-stat-time">${fmtHourTime(dArc?.day_mintime)}</span></span></div>
          <div class="obs-stat-row"><span class="obs-stat-label">Trend</span><span class="obs-stat-value">${trendArrowHtml(dewTrend)}</span></div>
        </div>
        <div class="col-12 col-md-4 obs-subcol">
          <div class="obs-subcol-title">Humidity</div>
          <div class="obs-stat-row"><span class="obs-stat-label">High</span><span class="obs-stat-value">${hArc?.day_max!=null ? Math.round(hArc.day_max)+'%' : '--'}<span class="obs-stat-time">${fmtHourTime(hArc?.day_maxtime)}</span></span></div>
          <div class="obs-stat-row"><span class="obs-stat-label">Low</span><span class="obs-stat-value">${hArc?.day_min!=null ? Math.round(hArc.day_min)+'%' : '--'}<span class="obs-stat-time">${fmtHourTime(hArc?.day_mintime)}</span></span></div>
          <div class="obs-stat-row"><span class="obs-stat-label">Average</span><span class="obs-stat-value">${hArc?.day_avg!=null ? Math.round(hArc.day_avg)+'%' : '--'}</span></div>
          <div class="obs-stat-row"><span class="obs-stat-label">Trend</span><span class="obs-stat-value">${trendArrowHtml(humidTrend)}</span></div>
        </div>
      </div>
    </div></div>`;

  const rArc = todayObsArchive?.rain, etArc = todayObsArchive?.et;
  const vpdForCard = liveData?.vpdKpa ?? metricsRaw.vpdKpa;
  const etDay = etArc?.day;
  const rainFeatureCard = `<div class="col-12 col-md-6"><div class="card metric-card metric-card--feature metric-card--feature-half p-3 p-md-4 h-100">
      <div class="d-flex align-items-center gap-2 mb-2">
        ${raindropOutlineIcon('feature-icon')}
        <div class="feature-title">Rain</div>
      </div>
      <div class="obs-stat-row"><span class="obs-stat-label">Rain sum</span><span class="obs-stat-value">${fmtRainOrDash(rArc?.day, sys.rain)}</span></div>
      <div class="obs-stat-row"><span class="obs-stat-label">Highest rate</span><span class="obs-stat-value">${fmtRainOrDash(rArc?.maxRate, sys.rain)}${rArc?.maxRate!=null ? '<small>/hr</small>' : ''}</span></div>
      <div class="obs-stat-row"><span class="obs-stat-label">Vapour pressure deficit</span><span class="obs-stat-value">${vpdForCard!=null ? vpdForCard.toFixed(2)+' kPa' : '--'}</span></div>
      <div class="obs-stat-row"><span class="obs-stat-label">Evapotranspiration</span><span class="obs-stat-value">${fmtRainOrDash(etDay, sys.rain)}</span></div>
    </div></div>`;

  const wArc = todayObsArchive?.wind;
  const windHighestGustMs = wArc?.gust_max!=null ? mph2ms(wArc.gust_max) : null;
  const windHighestSpeedMs = wArc?.speed_max!=null ? mph2ms(wArc.speed_max) : null;
  const windAvgMs = wArc?.speed_avg!=null ? mph2ms(wArc.speed_avg) : null;
  const windFeatureCard = `<div class="col-12 col-md-6"><div class="card metric-card metric-card--feature metric-card--feature-half p-3 p-md-4 h-100">
      <div class="d-flex align-items-center gap-2 mb-2">
        ${windOutlineIcon('feature-icon')}
        <div class="feature-title">Wind</div>
      </div>
      <div class="obs-stat-row"><span class="obs-stat-label">Highest gust</span><span class="obs-stat-value">${windHighestGustMs!=null ? windSpan(windHighestGustMs, sys.wind) : '--'}<span class="obs-stat-time">${fmtHourTime(wArc?.gust_maxtime)}</span></span></div>
      <div class="obs-stat-row"><span class="obs-stat-label">Highest wind</span><span class="obs-stat-value">${windHighestSpeedMs!=null ? windSpan(windHighestSpeedMs, sys.wind) : '--'}<span class="obs-stat-time">${fmtHourTime(wArc?.speed_maxtime)}</span></span></div>
      <div class="obs-stat-row"><span class="obs-stat-label">Average</span><span class="obs-stat-value">${windAvgMs!=null ? windSpan(windAvgMs, sys.wind) : '--'}</span></div>
      <div class="obs-stat-row"><span class="obs-stat-label">Wind run</span><span class="obs-stat-value">${wArc?.wind_run!=null ? fmtRawNumber(wArc.wind_run, 1)+(windRunUnitLabel(sys.wind) ? ' '+windRunUnitLabel(sys.wind) : '') : '--'}</span></div>
    </div></div>`;

  const bArc = todayObsArchive?.barom, adArc = todayObsArchive?.air_density;
  const baromTrend = baromTrendFromCode(bArc?.trend_code);
  const barometerFeatureCard = `<div class="col-12"><div class="card metric-card metric-card--feature p-3 p-md-4 h-100">
      <div class="d-flex align-items-center gap-2 mb-2">
        ${barometerOutlineIcon('feature-icon')}
        <div class="feature-title">Barometer</div>
      </div>
      <div class="row g-3 g-md-0">
        <div class="col-12 col-md-6 obs-subcol">
          <div class="obs-subcol-title">Pressure</div>
          <div class="obs-stat-row"><span class="obs-stat-label">High</span><span class="obs-stat-value">${fmtPressureOrDash(bArc?.day_max, sys.pressure)}<span class="obs-stat-time">${fmtHourTime(bArc?.day_maxtime)}</span></span></div>
          <div class="obs-stat-row"><span class="obs-stat-label">Low</span><span class="obs-stat-value">${fmtPressureOrDash(bArc?.day_min, sys.pressure)}<span class="obs-stat-time">${fmtHourTime(bArc?.day_mintime)}</span></span></div>
          <div class="obs-stat-row"><span class="obs-stat-label">Average</span><span class="obs-stat-value">${fmtPressureOrDash(bArc?.day_avg, sys.pressure)}</span></div>
          <div class="obs-stat-row"><span class="obs-stat-label">Trend</span><span class="obs-stat-value">${trendArrowHtml(baromTrend)}</span></div>
        </div>
        <div class="col-12 col-md-6 obs-subcol">
          <div class="obs-subcol-title">Air Density</div>
          <div class="obs-stat-row"><span class="obs-stat-label">Current</span><span class="obs-stat-value">${adArc?.current!=null ? fmtRawNumber(adArc.current, 2)+' kg/m³' : '--'}</span></div>
          <div class="obs-stat-row"><span class="obs-stat-label">High</span><span class="obs-stat-value">${adArc?.max!=null ? fmtRawNumber(adArc.max, 2)+' kg/m³' : '--'}</span></div>
          <div class="obs-stat-row"><span class="obs-stat-label">Low</span><span class="obs-stat-value">${adArc?.min!=null ? fmtRawNumber(adArc.min, 2)+' kg/m³' : '--'}</span></div>
          <div class="obs-stat-row"><span class="obs-stat-label">Average</span><span class="obs-stat-value">${adArc?.avg!=null ? fmtRawNumber(adArc.avg, 2)+' kg/m³' : '--'}</span></div>
        </div>
      </div>
    </div></div>`;

  grid.innerHTML = [
    temperatureFeatureCard,
    rainFeatureCard,
    windFeatureCard,
    barometerFeatureCard,
  ].join('');

  const skyGrid = document.getElementById('skyGrid');
  skyGrid.innerHTML = [
    mc(`<div class="mlabel">UV index</div>
        <div class="mvalue">${uvSpan(uvForCard)} <small>${uvLabelOfficial(uvForCard)}</small></div>
        <div class="msub">${liveData?.uv!=null ? 'Live reading from station' : metricsRaw.uvAdvice}</div>`),
    mc(`<div class="mlabel">Cloud</div>
        <div class="mvalue">${Math.round(cloudCoverForCard)}% <small>cover</small></div>
        <div class="msub">${cloudCoverForCard < 5 ? 'Clear skies' : ''}</div>
        <div class="msub">Cloud base: ${fmtCloudBase(cloudBaseForCard)}</div>
        <div class="msub">Visibility: ${fmtVis(visKmForCard, sys.vis)} (${visLabel(visKmForCard)})</div>`),
    (()=>{
      const rad = liveData?.radiationWm2 ?? metricsRaw.radiationWm2;
      const lux = liveData?.illuminance ?? metricsRaw.illuminance;
      return mc(`<div class="mlabel">Solar radiation</div>
        <div class="mvalue">${Math.round(rad)} <small>W/m²</small></div>
        <div class="msub">Illuminance: ${Math.round(lux).toLocaleString()} lux</div>`);
    })(),
    (()=>{
      const count = liveData?.lightningCount ?? metricsRaw.lightningCount;
      const distKm = liveData?.lightningDistKm ?? metricsRaw.lightningDistKm;
      return mc(`<div class="mlabel">Lightning</div>
        <div class="mvalue">${count} <small>strike${count===1?'':'s'} today</small></div>
        <div class="msub">${distKm!=null ? 'Last strike '+fmtVis(distKm, sys.vis)+' away' : 'No strikes detected'}</div>`);
    })(),
  ].join('');

  (()=>{
    const co2 = liveData?.co2Ppm ?? metricsRaw.co2Ppm;
    const no2 = liveData?.no2 ?? metricsRaw.no2;
    const co = liveData?.co ?? metricsRaw.co;
    const o3 = liveData?.o3 ?? metricsRaw.o3;
    const so2 = liveData?.so2 ?? metricsRaw.so2;
    const nh3 = liveData?.nh3 ?? metricsRaw.nh3;
    const ghgRow = (name, value, unit) => `<div class="species-row"><span class="species-name">${name}</span><span class="species-value">${value} ${unit}</span></div>`;
    document.getElementById('ghgCardSlot').innerHTML = `<div class="card metric-card p-3 h-100">
        <div class="mlabel">Greenhouse gases</div>
        <div class="mvalue">${Math.round(co2)} <small>ppm CO₂</small></div>
        <div class="species-breakdown">
          ${ghgRow('CO₂', Math.round(co2), 'ppm')}
          ${ghgRow('NO₂', no2, 'µg/m³')}
          ${ghgRow('CO', co, 'µg/m³')}
          ${ghgRow('O₃', o3, 'µg/m³')}
          ${ghgRow('SO₂', so2, 'µg/m³')}
          ${ghgRow('NH₃', nh3, 'µg/m³')}
        </div>
      </div>`;
  })();

  if(liveData?.pollen){
    document.getElementById('pollenLevel').textContent = liveData.pollen.level;
    document.getElementById('pollenDot').className = 'level-dot ' + pollenDotClass(liveData.pollen.level);
    document.getElementById('pollenDetail').textContent = liveData.pollen.detail;
    if(liveData.pollen.allSpecies?.length){
      document.getElementById('pollenBreakdown').innerHTML = liveData.pollen.allSpecies.map(s => `
        <div class="species-row"><span class="species-name"><span class="level-dot ${pollenDotClass(s.level)}"></span>${s.name}</span><span class="species-value">${s.level} (${Math.round(s.value)})</span></div>
      `).join('');
    }
  }
  if(liveData?.aq){
    document.getElementById('aqLevel').textContent = `${liveData.aq.level} (AQI ${liveData.aq.aqi})`;
    document.getElementById('aqDot').className = 'level-dot ' + aqDotClass(liveData.aq.level);
    document.getElementById('aqDetail').textContent = liveData.aq.level==='Low' ? 'Enjoy your usual outdoor activities' : liveData.aq.level==='Moderate' ? 'Sensitive groups should take care outdoors' : 'Consider reducing time outdoors';
    if(liveData.aq.particles?.length){
      document.getElementById('aqBreakdown').innerHTML = liveData.aq.particles.map(p => `
        <div class="species-row"><span class="species-name"><span class="level-dot ${aqDotClass(p.level)}"></span>${p.name}</span><span class="species-value">AQI ${Math.round(p.aqi)} (${p.level})</span></div>
      `).join('');
    }
  }

  document.getElementById('hourlyDayTitle').textContent = days[activeDay].name + ' — hourly';
  const table = document.getElementById('hourlyTable');
  const fmtHour = h => h===0?'12am':h===12?'12pm':h>12?(h-12)+'pm':h+'am';

  // ---- Temperature track: icon+temp markers are positioned along an
  // invisible plot line — each marker's vertical offset is its temperature
  // normalised against the day's min/max, so the row's natural layout traces
  // the temperature curve without ever drawing the line itself. ----
  const TRACK_RANGE = 56; // px of vertical travel between the hottest and coldest hour
  const trackTemps = hrs.map(h=>h.tempC).filter(t=>t!=null);
  const tMin = trackTemps.length ? Math.min(...trackTemps) : 0;
  const tMax = trackTemps.length ? Math.max(...trackTemps) : 1;
  const tempOffset = t => {
    if(t==null || tMax===tMin) return TRACK_RANGE/2;
    return Math.round((1 - (t - tMin)/(tMax - tMin)) * TRACK_RANGE);
  };

  table.innerHTML = `
    <tr>
      <th class="rowlabel">Time</th>
      ${hrs.map(h=>`<th>${fmtHour(h.h)}</th>`).join('')}
    </tr>
    <tr class="track-row">
      <td class="rowlabel">Weather</td>
      ${hrs.map(h=>`<td class="track-cell"><div class="track-marker" style="top:${tempOffset(h.tempC)}px">${iconImg(h.icon,'hicon')}<span class="temp-cell">${tempSpan(h.tempC, sys.temp)}</span></div></td>`).join('')}
    </tr>
    <tr>
      <td class="rowlabel">Rain</td>
      ${hrs.map(h=>`<td class="rain-cell">${h.rainMm>0 ? fmtRain(h.rainMm, sys.rain) : '—'}</td>`).join('')}
    </tr>
    <tr>
      <td class="rowlabel">Gust (${windUnitAbbr(sys.wind)})</td>
      ${hrs.map(h=>`<td>${windDirBadge(h.windDirDeg, h.gustMs ?? h.windMs, sys.wind)}</td>`).join('')}
    </tr>
  `;
}

document.getElementById('unitSystem').addEventListener('change', e=>{
  currentSystem = e.target.value;
  initInstrumentGauges();
  render();
});

// ===================== Theme: Auto / Light / Dark =====================
const root = document.documentElement;
let themeMode = localStorage.getItem('bircheswx-theme-mode') || 'auto';

function resolveAutoIsDay(){
  if(liveData?.isDay != null) return liveData.isDay;
  if(forecastData?.current?.isDay != null) return forecastData.current.isDay;
  return !window.matchMedia('(prefers-color-scheme: dark)').matches;
}

let lastGaugeTheme = null;
function applyTheme(){
  const resolved = themeMode==='light' ? 'light' : themeMode==='dark' ? 'dark' : (resolveAutoIsDay() ? 'light' : 'dark');
  root.setAttribute('data-bs-theme', resolved);

  document.getElementById('themeIconSun').style.display = resolved==='light' ? '' : 'none';
  document.getElementById('themeIconMoon').style.display = resolved==='dark' ? '' : 'none';

  document.querySelectorAll('#themeMenu .dropdown-item').forEach(el=>{
    el.classList.toggle('active-theme', el.dataset.themeChoice===themeMode);
  });

  if(resolved !== lastGaugeTheme){
    lastGaugeTheme = resolved;
    initInstrumentGauges();
  }
}

document.querySelectorAll('#themeMenu .dropdown-item').forEach(el=>{
  el.addEventListener('click', ()=>{
    themeMode = el.dataset.themeChoice;
    localStorage.setItem('bircheswx-theme-mode', themeMode);
    applyTheme();
  });
});

window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', ()=>{
  if(themeMode==='auto') applyTheme();
});

applyTheme();

// ===================== Clock + live.json polling =====================
function tick(){
  const d = liveData ? new Date(liveData.dateTime*1000) : new Date();
  document.getElementById('liveClock').textContent = d.toLocaleTimeString('en-GB');
}
setInterval(tick, 1000); tick();

setLiveStatus('connecting');
pollLive();
setInterval(pollLive, LIVE_POLL_MS);

pollForecast();
setInterval(pollForecast, FORECAST_POLL_MS);

pollAlerts();
setInterval(pollAlerts, ALERTS_POLL_MS);

pollHeatAlert();
setInterval(pollHeatAlert, ALERTS_POLL_MS);

pollMetar();
setInterval(pollMetar, METAR_POLL_MS);

pollHeroAstro();
setInterval(pollHeroAstro, HERO_ASTRO_POLL_MS);

pollTodayObs();
setInterval(pollTodayObs, TODAY_OBS_POLL_MS);

render();
