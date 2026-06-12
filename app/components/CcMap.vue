<template>
  <div class="relative h-full w-full">
    <div ref="el" class="h-full w-full" role="application" aria-label="Collection completeness map"></div>
    <!-- Legend: always visible, bottom-left -->
    <div class="absolute bottom-4 left-4 bg-white/95 rounded-lg shadow p-3 text-xs text-slate-700 space-y-1" aria-hidden="false">
      <div class="font-semibold text-slate-800 mb-1">Last collected</div>
      <div class="flex items-center gap-2"><span class="inline-block w-4 h-4 rounded-sm" style="background:#08519C"></span> 0–12 months</div>
      <div class="flex items-center gap-2"><span class="inline-block w-4 h-4 rounded-sm" style="background:#6BAED6"></span> 12–24 months</div>
      <div class="flex items-center gap-2"><span class="inline-block w-4 h-4 rounded-sm border border-slate-300" style="background:#C6DBEF"></span> &gt;24 months</div>
      <div class="flex items-center gap-2"><span class="inline-block w-4 h-4 rounded-sm hatch-swatch" style="background:#D55E00"></span> Never collected</div>
      <div v-if="mode!=='council'" class="pt-1 border-t border-slate-200">
        <div class="font-semibold text-slate-800 mb-1">% collected (12m)</div>
        <div class="flex">
          <span v-for="(c,i) in ramp" :key="i" class="inline-block w-6 h-3" :style="{background:c}"></span>
        </div>
        <div class="flex justify-between"><span>0%</span><span>100%</span></div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import maplibregl from 'maplibre-gl'

const props = defineProps<{
  mode: 'council'|'wards'|'parishes'
  selected: string | null
  trunk: boolean
  showDemand: boolean
  visibleBands: string[]
  hoverRow: string | null
}>()
const emit = defineEmits<{
  (e:'drill', code:string, name:string): void
  (e:'hoverBoundary', code:string|null): void
}>()

const { dataUrl, demand, wardBbox } = useCc()
const el = ref<HTMLElement>()
let map: maplibregl.Map
const ramp = ['#F7FBFF','#C9DDF0','#6BAED6','#2171B5','#08306B']
const BAND_COLOR: any = ['match', ['get','b'],
  '0-12', '#08519C', '12-24', '#6BAED6', '>24', '#C6DBEF', 'never', '#D55E00', '#999999']

const loadedWards = new Set<string>()
const r10Features: any[] = []
let popup: maplibregl.Popup

function hexFilter () {
  const f: any[] = ['all', ['in', ['get','b'], ['literal', props.visibleBands]]]
  if (props.trunk) f.push(['==', ['get','k'], true])
  if (props.selected && props.mode === 'wards') f.push(['==', ['get','w'], props.selected])
  if (props.selected && props.mode === 'parishes') f.push(['==', ['get','p'], props.selected])
  return f
}

async function ensureWard (w: string) {
  if (loadedWards.has(w)) return
  loadedWards.add(w)
  try {
    const gj: any = await $fetch(dataUrl(`wards_r10/${w}.json`))
    r10Features.push(...gj.features)
    ;(map.getSource('r10') as maplibregl.GeoJSONSource)?.setData({ type:'FeatureCollection', features: r10Features })
  } catch { /* ward file missing */ }
}

async function loadVisibleWards () {
  if (!map) return
  const z = map.getZoom()
  const needAll = props.selected != null
  if (z < 12 && !needAll) return
  const b = map.getBounds()
  for (const [w, bb] of Object.entries(wardBbox.value)) {
    const [minx,miny,maxx,maxy] = bb as number[]
    if (maxx < b.getWest() || minx > b.getEast() || maxy < b.getSouth() || miny > b.getNorth()) continue
    await ensureWard(w)
  }
}

function hatchImage (): ImageData {
  const c = document.createElement('canvas'); c.width = c.height = 12
  const x = c.getContext('2d')!
  x.strokeStyle = 'rgba(60,25,0,0.85)'; x.lineWidth = 2
  for (let i = -12; i <= 24; i += 6) { x.beginPath(); x.moveTo(i, 12); x.lineTo(i + 12, 0); x.stroke() }
  return x.getImageData(0, 0, 12, 12)
}

function applyState () {
  if (!map || !map.getLayer('r10-fill')) return
  const f = hexFilter()
  map.setFilter('r10-fill', f as any)
  map.setFilter('r10-hatch', ['all', ['==',['get','b'],'never'], ...f.slice(1)] as any)
  map.setFilter('r10-line', f as any)
  const inBound = props.mode !== 'council'
  map.setLayoutProperty('choro-fill', 'visibility', inBound ? 'visible' : 'none')
  map.setLayoutProperty('choro-line', 'visibility', inBound ? 'visible' : 'none')
  const drilled = props.selected != null
  map.setPaintProperty('choro-fill', 'fill-opacity', drilled ? 0.08 : 0.85)
  // hexes visible in council mode (zoom-gated) or when drilled
  const hexVis = (props.mode === 'council' || drilled) ? 'visible' : 'none'
  for (const l of ['r10-fill','r10-hatch','r10-line']) map.setLayoutProperty(l, 'visibility', hexVis)
  map.setLayoutProperty('r8-fill', 'visibility', props.mode === 'council' && !drilled ? 'visible' : 'none')
  for (const l of ['demand-cluster','demand-count','demand-out','demand-clear','demand-check'])
    if (map.getLayer(l)) map.setLayoutProperty(l, 'visibility', props.showDemand ? 'visible' : 'none')
  if (drilled) loadVisibleWards()
}

function boundarySrc () { return props.mode === 'parishes' ? 'parishes' : 'wards' }

watch(() => [props.mode, props.trunk, props.showDemand, props.visibleBands, props.selected], async () => {
  if (!map || !map.getLayer('choro-fill')) return
  const src = props.mode === 'parishes' ? dataUrl('boundaries_parishes.geojson') : dataUrl('boundaries_wards.geojson')
  ;(map.getSource('boundaries') as maplibregl.GeoJSONSource)?.setData(await $fetch(src) as any)
  applyState()
  if (props.selected) {
    const gj: any = await $fetch(src)
    const ft = gj.features.find((f: any) => f.properties.code === props.selected)
    if (ft) {
      const bb = bboxOf(ft.geometry)
      map.fitBounds(bb as any, { padding: 40 })
      if (props.mode === 'parishes') {
        for (const [w, wb] of Object.entries(wardBbox.value)) {
          const [minx,miny,maxx,maxy] = wb as number[]
          if (!(maxx < bb[0][0] || minx > bb[1][0] || maxy < bb[0][1] || miny > bb[1][1])) await ensureWard(w)
        }
      } else { await ensureWard(props.selected) }
    }
  }
}, { deep: true })

watch(() => props.hoverRow, (code, old) => {
  if (!map?.getSource('boundaries')) return
  if (old) map.setFeatureState({ source:'boundaries', id: old }, { hover: false })
  if (code) map.setFeatureState({ source:'boundaries', id: code }, { hover: true })
})

function bboxOf (geom: any): [[number,number],[number,number]] {
  let minx=180,miny=90,maxx=-180,maxy=-90
  const eat=(c:any)=>{ if(typeof c[0]==='number'){minx=Math.min(minx,c[0]);maxx=Math.max(maxx,c[0]);miny=Math.min(miny,c[1]);maxy=Math.max(maxy,c[1])} else c.forEach(eat) }
  eat(geom.coordinates)
  return [[minx,miny],[maxx,maxy]]
}

defineExpose({
  flyTo: (lat:number, lon:number) => map?.flyTo({ center:[lon,lat], zoom: 14.5 })
})

onMounted(async () => {
  map = new maplibregl.Map({
    container: el.value!,
    style: {
      version: 8,
      glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
      sources: { carto: { type:'raster', tiles: ['https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png','https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png'], tileSize: 256, attribution: '© OpenStreetMap contributors © CARTO' } },
      layers: [{ id:'base', type:'raster', source:'carto' }]
    },
    center: [-2.33, 50.75], zoom: 9.3, attributionControl: { compact: false }
  })
  map.addControl(new maplibregl.NavigationControl(), 'top-right')
  await new Promise(res => map.on('load', res))
  map.addImage('hatch', hatchImage(), { pixelRatio: 2 })

  map.addSource('r8', { type:'geojson', data: dataUrl('cells_r8.geojson') })
  map.addSource('r10', { type:'geojson', data: { type:'FeatureCollection', features: [] } })
  map.addSource('boundaries', { type:'geojson', data: dataUrl('boundaries_wards.geojson'), promoteId: 'code' })
  map.addSource('council', { type:'geojson', data: dataUrl('boundary_council.geojson') })
  map.addSource('demand', { type:'geojson', cluster: true, clusterRadius: 40,
    data: { type:'FeatureCollection', features: demand.value.map((p:any) => ({
      type:'Feature', geometry:{ type:'Point', coordinates:[p.lon,p.lat] },
      properties:{ status:p.status, kind:p.kind, created:p.created_at, ward:p.ward } })) } })

  map.addLayer({ id:'r8-fill', type:'fill', source:'r8', maxzoom: 12,
    paint: { 'fill-color': BAND_COLOR, 'fill-opacity': 0.55 } })
  map.addLayer({ id:'r10-fill', type:'fill', source:'r10', minzoom: 11.5,
    paint: { 'fill-color': BAND_COLOR, 'fill-opacity': 0.6 } })
  map.addLayer({ id:'r10-hatch', type:'fill', source:'r10', minzoom: 11.5,
    filter: ['==',['get','b'],'never'], paint: { 'fill-pattern': 'hatch', 'fill-opacity': 0.7 } })
  map.addLayer({ id:'r10-line', type:'line', source:'r10', minzoom: 12.5,
    paint: { 'line-color': '#94A3B8', 'line-width': 0.4 } })
  map.addLayer({ id:'choro-fill', type:'fill', source:'boundaries', layout: { visibility:'none' },
    paint: { 'fill-color': ['step', ['get','pct'], ramp[0], 20, ramp[1], 40, ramp[2], 60, ramp[3], 80, ramp[4]],
             'fill-opacity': ['case', ['boolean',['feature-state','hover'],false], 1, 0.85] } })
  map.addLayer({ id:'choro-line', type:'line', source:'boundaries', layout: { visibility:'none' },
    paint: { 'line-color': '#1B3A66', 'line-width': ['case', ['boolean',['feature-state','hover'],false], 2.5, 0.8] } })
  map.addLayer({ id:'council-line', type:'line', source:'council',
    paint: { 'line-color': '#1B3A66', 'line-width': 2 } })
  map.addLayer({ id:'demand-cluster', type:'circle', source:'demand', filter: ['has','point_count'],
    paint: { 'circle-color':'#1B3A66', 'circle-radius': ['step',['get','point_count'],14,5,18,10,24], 'circle-opacity':0.9 } })
  map.addLayer({ id:'demand-count', type:'symbol', source:'demand', filter: ['has','point_count'],
    layout: { 'text-field': ['get','point_count_abbreviated'], 'text-size': 12, 'text-font': ['Noto Sans Regular'] },
    paint: { 'text-color': '#ffffff' } })
  map.addLayer({ id:'demand-out', type:'circle', source:'demand',
    filter: ['all', ['!',['has','point_count']], ['==',['get','status'],'outstanding']],
    paint: { 'circle-color':'#D55E00', 'circle-radius': 8, 'circle-stroke-color':'#7a3000', 'circle-stroke-width': 1.5 } })
  map.addLayer({ id:'demand-clear', type:'circle', source:'demand',
    filter: ['all', ['!',['has','point_count']], ['==',['get','status'],'cleared']],
    paint: { 'circle-color':'#ffffff', 'circle-radius': 8, 'circle-stroke-color':'#1B3A66', 'circle-stroke-width': 2.5 } })
  map.addLayer({ id:'demand-check', type:'symbol', source:'demand',
    filter: ['all', ['!',['has','point_count']], ['==',['get','status'],'cleared']],
    layout: { 'text-field': '✓', 'text-size': 11, 'text-font': ['Noto Sans Regular'], 'text-allow-overlap': true },
    paint: { 'text-color': '#1B3A66' } })

  popup = new maplibregl.Popup({ closeButton:false, closeOnClick:false, maxWidth:'280px' })
  map.on('mousemove', 'r10-fill', (e) => {
    const p: any = e.features?.[0]?.properties; if (!p) return
    map.getCanvas().style.cursor = 'crosshair'
    popup.setLngLat(e.lngLat).setHTML(
      `<div class="text-xs text-slate-700">
         <div><b>Last collected:</b> ${p.d || 'never'}</div>
         <div><b>Age band:</b> ${p.b === 'never' ? 'never collected' : p.b + ' months'}</div>
         <div><b>Tests:</b> ${p.t}</div>
         <div><b>Class:</b> ${p.c || '—'} ${p.r ? '· ' + p.r : ''}</div>
       </div>`).addTo(map)
  })
  map.on('mouseleave', 'r10-fill', () => { map.getCanvas().style.cursor = ''; popup.remove() })

  map.on('mousemove', 'choro-fill', (e) => {
    const p: any = e.features?.[0]?.properties; if (!p) return
    map.getCanvas().style.cursor = 'pointer'
    emit('hoverBoundary', p.code)
    popup.setLngLat(e.lngLat).setHTML(
      `<div class="text-xs text-slate-700"><b>${p.name}</b> — ${p.pct}% collected (12m)</div>`).addTo(map)
  })
  map.on('mouseleave', 'choro-fill', () => { map.getCanvas().style.cursor=''; emit('hoverBoundary', null); popup.remove() })
  map.on('click', 'choro-fill', (e) => {
    const p: any = e.features?.[0]?.properties
    if (p) emit('drill', p.code, p.name)
  })
  map.on('click', 'demand-cluster', async (e) => {
    const f = map.queryRenderedFeatures(e.point, { layers:['demand-cluster'] })[0] as any
    const z = await (map.getSource('demand') as any).getClusterExpansionZoom(f.properties.cluster_id)
    map.easeTo({ center: f.geometry.coordinates, zoom: z + 0.5 })
  })
  for (const lyr of ['demand-out','demand-clear']) {
    map.on('mousemove', lyr, (e:any) => {
      const p = e.features?.[0]?.properties; if (!p) return
      popup.setLngLat(e.lngLat).setHTML(
        `<div class="text-xs text-slate-700"><b>${p.kind === 'map_the_gap' ? 'Map-the-gap report' : 'Checker request'}</b><br>${p.created} · ${p.status === 'cleared' ? 'cleared this period ✓' : 'outstanding'}</div>`).addTo(map)
    })
    map.on('mouseleave', lyr, () => popup.remove())
  }
  map.on('moveend', () => { if (props.mode === 'council') loadVisibleWards() })
  applyState()
})
</script>

<style scoped>
.hatch-swatch { background-image: repeating-linear-gradient(45deg, rgba(60,25,0,.85) 0 2px, transparent 2px 6px) !important; background-color: #D55E00 !important; }
</style>
