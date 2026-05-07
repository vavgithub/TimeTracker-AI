/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Trace proxy (python proxy.py). Default http://localhost:5899. */
  readonly VITE_TRACE_PROXY_URL?: string
  readonly VITE_AW_URL?: string
  readonly VITE_AW_HOSTNAME?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
