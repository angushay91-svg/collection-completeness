export default defineNuxtConfig({
  ssr: false,
  modules: ['@nuxtjs/tailwindcss'],
  app: {
    // NUXT_APP_BASE_URL env overrides this at generate time (GitHub Pages subpath)
    baseURL: process.env.NUXT_APP_BASE_URL || '/',
    head: {
      title: 'Collection completeness — Streetwave',
      meta: [{ name: 'viewport', content: 'width=device-width, initial-scale=1' }]
    }
  },
  css: ['maplibre-gl/dist/maplibre-gl.css'],
  compatibilityDate: '2026-06-12'
})
