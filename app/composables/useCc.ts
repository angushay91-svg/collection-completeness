// Central data loader: everything the panel renders comes from rollups.json —
// the frontend never aggregates raw cells.
export const useCc = () => {
  const base = useRuntimeConfig().app.baseURL
  const slug = 'dorset'
  const dataUrl = (f: string) => `${base}data/${slug}/${f}`

  const meta = useState<any>('meta', () => null)
  const rollups = useState<any>('rollups', () => null)
  const demand = useState<any[]>('demand', () => [])
  const wardBbox = useState<Record<string, number[]>>('wardBbox', () => ({}))

  const load = async () => {
    if (meta.value) return
    const [m, r, d, b] = await Promise.all([
      $fetch(dataUrl('meta.json')),
      $fetch(dataUrl('rollups.json')),
      $fetch(dataUrl('demand.json')),
      $fetch(dataUrl('ward_bbox.json'))
    ])
    meta.value = m; rollups.value = r; demand.value = d as any[]; wardBbox.value = b as any
  }
  return { load, meta, rollups, demand, wardBbox, dataUrl, slug }
}
