<template>
  <div class="h-screen flex flex-col bg-slate-50 text-slate-800" @keyup.esc="goUp">
    <!-- Header -->
    <header class="bg-white border-b border-slate-200 px-4 py-2 flex items-center gap-4 flex-wrap">
      <div class="flex items-center gap-2">
        <span class="text-lg font-bold" style="color:#1B3A66">Collection completeness</span>
        <span class="text-sm text-slate-500">{{ meta?.council }}</span>
      </div>
      <nav aria-label="Breadcrumb" class="text-sm text-slate-600">
        <button class="hover:underline focus:ring-2 focus:ring-blue-600 rounded px-1" @click="reset">{{ meta?.council }}</button>
        <template v-if="mode!=='council'">
          <span aria-hidden="true"> → </span>
          <button class="hover:underline focus:ring-2 focus:ring-blue-600 rounded px-1" @click="selected=null">{{ mode==='wards'?'Wards':'Parishes' }}</button>
        </template>
        <template v-if="selectedName"><span aria-hidden="true"> → </span><span class="font-semibold">{{ selectedName }}</span></template>
      </nav>
      <label class="text-sm">Boundaries
        <select v-model="mode" class="ml-1 border border-slate-300 rounded px-2 py-1 text-sm focus:ring-2 focus:ring-blue-600" aria-label="Boundary mode">
          <option value="council">Council</option>
          <option value="wards">Wards</option>
          <option value="parishes">Parishes</option>
        </select>
      </label>
      <div class="flex items-center gap-3 text-sm" role="group" aria-label="Layer controls">
        <label class="flex items-center gap-1"><input type="checkbox" v-model="trunk" class="accent-blue-800"> Trunk roads only</label>
        <label class="flex items-center gap-1"><input type="checkbox" v-model="showDemand" class="accent-blue-800"> Demand</label>
        <label class="flex items-center gap-1 text-slate-400" title="No non-road collected cells exist in this build"><input type="checkbox" disabled> Non-road cells</label>
      </div>
      <span class="ml-auto text-xs bg-slate-100 border border-slate-200 rounded-full px-3 py-1">Data as of {{ meta?.build }} <span class="text-amber-700 font-semibold ml-1">SYNTHETIC DEMO DATA</span></span>
    </header>

    <!-- text alternative summary (accessibility) -->
    <p class="sr-only" role="status">{{ summaryLine }}</p>

    <div class="flex-1 flex min-h-0">
      <!-- Map -->
      <main class="flex-1 min-w-0">
        <CcMap ref="mapRef" :mode="mode" :selected="selected" :trunk="trunk" :show-demand="showDemand"
               :visible-bands="visibleBands" :hover-row="hoverRow"
               @drill="onDrill" @hover-boundary="hoverRow = $event" />
      </main>

      <!-- Stats panel -->
      <aside class="w-[380px] shrink-0 overflow-y-auto bg-white border-l border-slate-200 p-4 space-y-4" aria-label="Statistics panel">
        <!-- 1. Headline -->
        <section class="rounded-xl border border-slate-200 p-4">
          <div class="flex items-start justify-between">
            <div>
              <div class="text-4xl font-bold" style="color:#1B3A66">{{ scope?.pct }}%</div>
              <div class="text-sm text-slate-600">of road network collected in the last 12 months</div>
              <div class="text-xs text-slate-500 mt-1">{{ fmt(scope?.collected_cells) }} of {{ fmt(scope?.total_cells) }} road cells
                <button class="underline decoration-dotted" :title="'Includes motorways, which refuse vehicles do not travel.'" aria-label="Includes motorways, which refuse vehicles do not travel.">ⓘ</button>
              </div>
            </div>
            <button class="text-slate-400 hover:text-slate-600 focus:ring-2 focus:ring-blue-600 rounded" @click="windowInfo=!windowInfo" aria-expanded="windowInfo" aria-label="About the rolling window">ⓘ</button>
          </div>
          <p v-if="windowInfo" class="mt-2 text-xs bg-blue-50 border border-blue-100 rounded p-2 text-slate-700">
            This figure uses a rolling 12-month window — it falls as data ages. That's intentional: it shows where a re-survey is due.
          </p>
        </section>

        <!-- 2. Age profile -->
        <section class="rounded-xl border border-slate-200 p-4">
          <h2 class="text-sm font-semibold mb-2">Age profile <span class="text-xs font-normal text-slate-400">(click a band to filter the map)</span></h2>
          <div class="flex h-7 rounded overflow-hidden" role="group" aria-label="Age bands, click to toggle map visibility">
            <button v-for="b in bandDefs" :key="b.key" :style="{ width: bandPct(b.key)+'%', background: b.color }"
                    class="h-full focus:ring-2 focus:ring-offset-1 focus:ring-blue-600 transition-opacity"
                    :class="{ 'opacity-30': !visibleBands.includes(b.key), 'hatch-bg': b.key==='never' }"
                    :aria-pressed="visibleBands.includes(b.key)"
                    :aria-label="`${b.label}: ${fmt(scope?.bands[b.key])} cells (${bandPct(b.key)}%)`"
                    :title="`${b.label}: ${fmt(scope?.bands[b.key])} cells`"
                    @click="toggleBand(b.key)"></button>
          </div>
          <ul class="mt-2 grid grid-cols-2 gap-1 text-xs">
            <li v-for="b in bandDefs" :key="b.key" class="flex items-center gap-1.5">
              <span class="w-3 h-3 rounded-sm inline-block" :class="{'hatch-bg': b.key==='never'}" :style="{background:b.color}"></span>
              {{ b.label }}: <b>{{ fmt(scope?.bands[b.key]) }}</b> ({{ bandPct(b.key) }}%)
            </li>
          </ul>
        </section>

        <!-- 3. Road classification -->
        <section class="rounded-xl border border-slate-200 p-4">
          <h2 class="text-sm font-semibold mb-2">Road classification</h2>
          <table class="w-full text-xs">
            <thead><tr class="text-slate-500 text-left"><th class="py-1">Class</th><th class="text-right">Cells</th><th class="text-right">% collected (12m)</th><th class="w-20"></th></tr></thead>
            <tbody>
              <tr v-for="(v, k) in scope?.classes" :key="k" class="border-t border-slate-100">
                <td class="py-1">{{ k }}</td>
                <td class="text-right">{{ fmt(v.cells) }}</td>
                <td class="text-right font-semibold">{{ v.pct }}%</td>
                <td class="pl-2"><div class="bg-slate-100 rounded h-2"><div class="h-2 rounded" :style="{width: v.pct+'%', background:'#1B3A66'}"></div></div></td>
              </tr>
            </tbody>
          </table>
        </section>

        <!-- 4. Premises -->
        <section v-if="mode==='council'" class="rounded-xl border border-slate-200 p-4">
          <div class="text-3xl font-bold" style="color:#1B3A66">{{ council?.uprn_pct }}%</div>
          <div class="text-sm text-slate-600">premises with recent outdoor measurement within 250m</div>
          <div class="text-xs text-slate-500 mt-1">{{ fmt(council?.uprn_near) }} of {{ fmt(council?.uprn_total) }} UPRNs</div>
        </section>

        <!-- 5. Named roads -->
        <section class="rounded-xl border border-slate-200 p-4">
          <button class="w-full text-left text-sm font-semibold flex justify-between focus:ring-2 focus:ring-blue-600 rounded" @click="namedOpen=!namedOpen" :aria-expanded="namedOpen">
            Named roads <span>{{ namedOpen ? '▾' : '▸' }}</span>
          </button>
          <table v-if="namedOpen" class="w-full text-xs mt-2">
            <thead><tr class="text-slate-500 text-left"><th class="py-1">Road</th><th>Class</th><th class="text-right">Cells</th><th class="text-right">% (12m)</th></tr></thead>
            <tbody>
              <tr v-for="r in namedRoads" :key="r.road" class="border-t border-slate-100" :class="{'bg-amber-50': r.trunk}">
                <td class="py-1 font-medium">{{ r.road }}{{ r.trunk ? ' ⛟' : '' }}</td>
                <td>{{ r.class }}</td>
                <td class="text-right">{{ fmt(r.cells) }}</td>
                <td class="text-right font-semibold">{{ r.pct }}%</td>
              </tr>
            </tbody>
          </table>
        </section>

        <!-- 6. Demand -->
        <section v-if="showDemand" class="rounded-xl border border-slate-200 p-4">
          <h2 class="text-sm font-semibold mb-1">Resident demand</h2>
          <div class="flex gap-4 text-sm">
            <div><span class="text-2xl font-bold" style="color:#D55E00">{{ council?.demand_outstanding }}</span> outstanding</div>
            <div><span class="text-2xl font-bold" style="color:#1B3A66">{{ council?.demand_cleared }}</span> cleared this period</div>
          </div>
          <ul class="mt-2 space-y-1 max-h-44 overflow-y-auto">
            <li v-for="p in demand" :key="p.id">
              <button class="w-full text-left text-xs p-1.5 rounded hover:bg-slate-50 focus:ring-2 focus:ring-blue-600 flex justify-between"
                      @click="flyToPin(p)">
                <span>{{ p.status==='cleared' ? '✓' : '●' }} {{ p.kind==='map_the_gap' ? 'Map-the-gap' : 'Checker request' }} · {{ p.ward }}</span>
                <span class="text-slate-400">{{ ageOf(p.created_at) }}</span>
              </button>
            </li>
          </ul>
        </section>

        <!-- 7. League table -->
        <section v-if="mode!=='council'" class="rounded-xl border border-slate-200 p-4">
          <div class="flex items-center justify-between mb-2">
            <h2 class="text-sm font-semibold">{{ mode==='wards' ? 'Wards' : 'Parishes' }} league table</h2>
            <a :href="dataUrl(mode==='wards' ? 'wards_stats.csv' : 'parishes_stats.csv')" download
               class="text-xs underline focus:ring-2 focus:ring-blue-600 rounded" style="color:#1B3A66">⬇ CSV</a>
          </div>
          <table class="w-full text-xs">
            <thead><tr class="text-slate-500 text-left">
              <th><button class="underline decoration-dotted" @click="sortBy='name'">Name</button></th>
              <th class="text-right"><button class="underline decoration-dotted" @click="sortBy='pct'">%</button></th>
              <th class="text-right"><button class="underline decoration-dotted" @click="sortBy='cells'">Cells</button></th>
            </tr></thead>
            <tbody>
              <tr v-for="row in league" :key="row.code"
                  class="border-t border-slate-100 cursor-pointer focus-within:bg-blue-50"
                  :class="{ 'bg-blue-50': hoverRow===row.code, 'font-semibold': selected===row.code }"
                  @mouseenter="hoverRow=row.code" @mouseleave="hoverRow=null">
                <td class="py-1">
                  <button class="text-left w-full focus:ring-2 focus:ring-blue-600 rounded" @click="onDrill(row.code, row.name)">{{ row.name }}</button>
                </td>
                <td class="text-right">{{ row.pct }}%</td>
                <td class="text-right">{{ fmt(row.cells) }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <p class="text-[11px] text-slate-400 leading-relaxed">
          Synthetic demonstration data — not real Streetwave collection history. Sources: OS Open Roads, OS Open UPRN,
          ONS boundaries (OGL v3). This screen shows data collection only — never mobile signal quality.
        </p>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
const { load, meta, rollups, demand, dataUrl } = useCc()
const route = useRoute(); const router = useRouter()

const mode = ref<'council'|'wards'|'parishes'>((route.query.mode as any) || 'council')
const selected = ref<string|null>((route.query.sel as string) || null)
const selectedName = ref<string|null>((route.query.seln as string) || null)
const trunk = ref(route.query.trunk === '1')
const showDemand = ref(route.query.demand === '1')
const visibleBands = ref<string[]>(((route.query.bands as string) || '0-12,12-24,>24,never').split(','))
const windowInfo = ref(false)
const namedOpen = ref(false)
const sortBy = ref<'name'|'pct'|'cells'>('pct')
const hoverRow = ref<string|null>(null)
const mapRef = ref()

await load()

const bandDefs = [
  { key: '0-12', label: '0–12 months', color: '#08519C' },
  { key: '12-24', label: '12–24 months', color: '#6BAED6' },
  { key: '>24', label: '>24 months', color: '#C6DBEF' },
  { key: 'never', label: 'Never', color: '#D55E00' }
]

const council = computed(() => rollups.value?.council)
const scope = computed(() => {
  if (selected.value && mode.value !== 'council') return rollups.value?.[mode.value]?.[selected.value]
  return council.value
})
const namedRoads = computed(() => {
  const list = scope.value?.named_roads || []
  return trunk.value ? list.filter((r:any) => r.trunk || r.class === 'Motorway') : list.slice(0, 40)
})
const league = computed(() => {
  const m = rollups.value?.[mode.value] || {}
  const rows = Object.entries(m).map(([code, v]: any) => ({ code, name: v.name, pct: v.pct, cells: v.total_cells }))
  return rows.sort((a,b) => sortBy.value==='name' ? a.name.localeCompare(b.name)
    : sortBy.value==='cells' ? b.cells-a.cells : b.pct-a.pct)
})
const summaryLine = computed(() =>
  `${scope.value?.pct}% of road cells collected in last 12 months; ${fmt(scope.value?.bands?.never)} never collected.`)

watch(trunk, v => { if (v) namedOpen.value = true })
watch([mode, selected, trunk, showDemand, visibleBands], () => {
  router.replace({ query: {
    mode: mode.value !== 'council' ? mode.value : undefined,
    sel: selected.value || undefined, seln: selectedName.value || undefined,
    trunk: trunk.value ? '1' : undefined, demand: showDemand.value ? '1' : undefined,
    bands: visibleBands.value.length === 4 ? undefined : visibleBands.value.join(',')
  } })
}, { deep: true })
watch(mode, () => { selected.value = null; selectedName.value = null })

function onDrill (code: string, name: string) { selected.value = code; selectedName.value = name }
function goUp () { if (selected.value) { selected.value = null; selectedName.value = null } else if (mode.value !== 'council') mode.value = 'council' }
function reset () { mode.value = 'council'; selected.value = null; selectedName.value = null }
function toggleBand (b: string) {
  visibleBands.value = visibleBands.value.includes(b)
    ? visibleBands.value.filter(x => x !== b) : [...visibleBands.value, b]
}
function flyToPin (p: any) { mapRef.value?.flyTo(p.lat, p.lon) }
function fmt (n?: number) { return (n ?? 0).toLocaleString() }
function ageOf (d: string) {
  const days = Math.round((Date.now() - new Date(d).getTime()) / 864e5)
  return days > 60 ? Math.round(days/30) + 'mo ago' : days + 'd ago'
}
</script>

<style>
.hatch-bg { background-image: repeating-linear-gradient(45deg, rgba(60,25,0,.85) 0 2px, transparent 2px 6px); }
.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); border:0; }
</style>
