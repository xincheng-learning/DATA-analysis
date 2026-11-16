import type { Data, Layout } from 'plotly.js';

const svgNS = 'http://www.w3.org/2000/svg';
let sequence = 0;

/** Plotly has native area gradients, but bar gradients need a small SVG finish. */
export function finishChart(root: Element, data: Data[], dark: boolean) {
  const series = data as (Data & {orientation?:string;visible?:boolean|string;showlegend?:boolean})[];
  root.querySelectorAll('[data-chart-effect]').forEach(node => node.remove());
  root.querySelectorAll<SVGElement>('.scatterlayer .js-line').forEach(path => { path.style.filter = ''; });
  if (!dark) return;
  if(series.some(trace=>['gantt','bullet'].includes(String((trace as unknown as {meta?:{family?:string}}).meta?.family))))return;
  const svg = root.matches('svg') ? root : root.querySelector('svg.main-svg');
  if (!svg) return;
  const element = (tag: string, attributes: Record<string, string | number>) => {
    const node = document.createElementNS(svgNS, tag);
    Object.entries(attributes).forEach(([name, value]) => node.setAttribute(name, String(value)));
    return node;
  };
  const defs = element('defs', { 'data-chart-effect': 'gradients' });
  svg.prepend(defs);
  const prefix = `luminous-${++sequence}`;
  const bars = series.filter(trace => trace.type === 'bar' && trace.visible !== false && trace.visible !== 'legendonly');
  const groups = Array.from(root.querySelectorAll<SVGGElement>('.barlayer .trace.bars'));
  groups.forEach((group, index) => {
    const trace = bars[index];
    if (!trace || groups.length !== bars.length) return;
    const color = String((trace as { marker?: { color?: unknown } }).marker?.color || '#10B981');
    const id = `${prefix}-${index}`;
    const gradient = element('linearGradient', { id, x1: 0, y1: 0, x2: 0, y2: 1 });
    gradient.append(element('stop', { offset: '0%', 'stop-color': color, 'stop-opacity': .85 }),
      element('stop', { offset: '100%', 'stop-color': color, 'stop-opacity': .25 }));
    defs.append(gradient);
    const horizontal = trace.orientation === 'h';
    const tracks = element('g', { 'data-chart-effect': 'tracks', 'pointer-events': 'none' });
    group.parentElement?.insertBefore(tracks, group.parentElement.firstChild);
    const plot = group.closest('.subplot')?.querySelector('.nsewdrag') || root.querySelector('.nsewdrag') || root.querySelector('.bglayer .bg');
    const fullSize = Number(plot?.getAttribute(horizontal ? 'width' : 'height'));
    // Keep decorations out of Plotly's g.points; it binds every path there to a data point.
    const highlights = element('g', { 'data-chart-effect': 'highlight-layer', 'pointer-events': 'none' });
    group.append(highlights);
    group.querySelectorAll<SVGPathElement>('.point > path').forEach(path => {
      const box = path.getBBox();
      const x = horizontal ? box.x : box.x + Math.max(0, box.width - 28) / 2;
      const y = horizontal ? box.y + Math.max(0, box.height - 28) / 2 : box.y;
      const w = horizontal ? box.width : Math.min(28, box.width);
      const h = horizontal ? Math.min(28, box.height) : box.height;
      if (w < .1 || h < .1) return;
      const radius = Math.min(index===groups.length-1 ? 6 : 4, w / 2, h / 2);
      const bottom = Math.min(index===groups.length-1 ? 2 : 4, w / 2, h / 2);
      path.setAttribute('d', `M${x + radius},${y}H${x + w - radius}Q${x + w},${y} ${x + w},${y + radius}V${y + h - bottom}Q${x + w},${y + h} ${x + w - bottom},${y + h}H${x + bottom}Q${x},${y + h} ${x},${y + h - bottom}V${y + radius}Q${x},${y} ${x + radius},${y}Z`);
      path.style.fill = `url(#${id})`;
      if (fullSize > 0 && index === 0) tracks.append(element('rect', {
        x: horizontal ? 0 : x, y: horizontal ? y : 0, width: horizontal ? fullSize : w,
        height: horizontal ? h : fullSize, rx: 6, fill: 'rgba(255,255,255,0.025)',
      }));
      highlights.append(element('path', { 'data-chart-effect': 'highlight',
        d: `M${x + radius},${y + .5}H${x + w - radius}`, stroke: color,
        'stroke-width': 1, 'stroke-opacity': .95, fill: 'none', 'pointer-events': 'none' }));
    });
  });
  const lines = series.filter(trace => trace.type === 'scatter' && String(trace.mode).includes('lines') && trace.visible !== false && trace.visible !== 'legendonly');
  root.querySelectorAll<SVGElement>('.scatterlayer .trace').forEach((group, index) => {
    const trace = lines[index] as { line?: { color?: string } } | undefined;
    group.querySelectorAll<SVGElement>('.js-line').forEach(path => {
      path.style.filter = `drop-shadow(0 0 5px ${trace?.line?.color || '#10B981'}66)`;
    });
  });
  const legends = series.filter(trace => trace.visible !== false && trace.showlegend !== false);
  if (!data.some(trace => trace.type === 'pie')) root.querySelectorAll<SVGGElement>('.legend .traces .layers').forEach((layers, index) => {
    // Stacked charts reverse legend order; match the label rather than its DOM position.
    const label = layers.parentElement?.querySelector('.legendtext')?.textContent;
    const trace = (legends.find(trace=>trace.name===label) || legends[index]) as { marker?: { color?: string }; line?: { color?: string } } | undefined;
    layers.replaceChildren(element('circle', { cx: 20, cy: 0, r: 4, fill: trace?.line?.color || trace?.marker?.color || '#10B981' }));
  });
}

/** Serialize Plotly's SVG, apply the same material finish, then rasterize it for PNG. */
export async function downloadChartPng(figure: { data: Data[]; layout: Partial<Layout> }, dark: boolean, filename: string) {
  const { default: Plotly } = await import('./plotly');
  const width = figure.layout.width || 1400, height = figure.layout.height || 850;
  const source = await Plotly.toImage(figure as unknown as Parameters<typeof Plotly.toImage>[0], { format: 'svg', width, height });
  const xml = new DOMParser().parseFromString(decodeURIComponent(source.slice(source.indexOf(',') + 1)), 'image/svg+xml');
  const svg = xml.documentElement;
  // getBBox is available only when the SVG is attached to the document.
  const host = document.createElement('div');
  host.style.cssText = 'position:fixed;left:-10000px;top:0;pointer-events:none';
  document.body.append(host); host.append(document.importNode(svg, true));
  let svgUrl = '';
  try {
    const attached = host.firstElementChild!;
    finishChart(attached, figure.data, dark);
    const serialized = new XMLSerializer().serializeToString(attached);
    svgUrl = URL.createObjectURL(new Blob([serialized], { type: 'image/svg+xml;charset=utf-8' }));
    const image = new Image(); image.src = svgUrl; await image.decode();
    const canvas = document.createElement('canvas'); canvas.width = width * 2; canvas.height = height * 2;
    const context = canvas.getContext('2d')!;
    context.scale(2, 2); context.drawImage(image, 0, 0, width, height);
    const png = await new Promise<Blob>((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('PNG encoding failed')), 'image/png'));
    const pngUrl = URL.createObjectURL(png);
    const link = document.createElement('a'); link.download = `${filename}.png`; link.href = pngUrl;
    document.body.append(link); link.click(); link.remove();
    setTimeout(()=>URL.revokeObjectURL(pngUrl),1000);
  } finally { host.remove(); if (svgUrl) URL.revokeObjectURL(svgUrl); }
}
