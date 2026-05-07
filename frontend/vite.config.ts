import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const OUT_DIR = path.resolve(__dirname, '../out')

/** Dev-only: GET /pipeline-out/daily_YYYY-MM-DD.json and sessions_YYYY-MM-DD.json from repo ../out */
function pipelineOutPlugin() {
  return {
    name: 'pipeline-out',
    configureServer(server: import('vite').ViteDevServer) {
      server.middlewares.use('/pipeline-out', (req, res, next) => {
        const name = (req.url ?? '').replace(/^\//, '').split('?')[0] ?? ''
        if (!/^(daily|sessions)_\d{4}-\d{2}-\d{2}\.json$/.test(name)) {
          next()
          return
        }
        const filePath = path.resolve(OUT_DIR, name)
        const resolvedOut = path.resolve(OUT_DIR)
        if (!filePath.startsWith(resolvedOut + path.sep) && filePath !== resolvedOut) {
          res.statusCode = 403
          res.end()
          return
        }
        fs.readFile(filePath, (err, buf) => {
          if (err) {
            res.statusCode = 404
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ error: 'not_found', file: name }))
            return
          }
          res.setHeader('Content-Type', 'application/json; charset=utf-8')
          res.end(buf)
        })
      })
    },
  }
}

// Optional production/static: VITE_DAILY_BASE_URL where daily_*.json is hosted
export default defineConfig({
  plugins: [react(), tailwindcss(), pipelineOutPlugin()],
  server: { port: 5173 },
})
